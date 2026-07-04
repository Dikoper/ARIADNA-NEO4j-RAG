"""Тесты search/retrieval.py (A-11/фикс module-dev): гибридный retrieval —
граф (execute_fn, инъекция) + вектор (мок embed_texts/vector_search_chunks) ->
единый пул чанков-свидетельств.

ИСТОРИЯ: интеграционный смоук оркестратора нашёл критический баг — граф-чанки
получали безусловный приоритет В ИСХОДНОМ (нерелевантном) ПОРЯДКЕ Cypher-шаблона
(сотни id, Q1=891/Q4=699), забивая весь контекст раньше, чем вектор вообще
попадал в промпт синтеза; все 4 эталонных ответа жюри начинались с «нет ответа».
Фикс: граф-кандидаты ранжируются по косинусной близости к вопросу (переиспользован
question_vec векторной ветки), квоты слияния — граф ≤ GRAPH_CONTEXT_QUOTA=7,
вектор ≥ VECTOR_CONTEXT_MIN=5, дедуп, добор до MAX_CONTEXT_CHUNKS=12. Тесты ниже
переписаны под НОВУЮ (пофикшенную) семантику — старые тесты фиксировали приоритет
графа в исходном порядке без ранжирования, это и было багом.

Покрыто: ранжирование граф-кандидатов по векторной близости (переставляет
исходный порядок execute_fn); квоты (граф ≤7, вектор ≥5, добор из любого
источника при нехватке одного из пулов); дедуп по chunk_id (граф-чанк,
найденный и вектором тоже — его score берётся из vector_by_id, а не
пересчитывается косинусом); деградация без question_vec (эмбеддинг упал ->
граф в исходном порядке шаблона, без падения); execute_fn падает -> SEARCH-006
в логе, деградация в вектор-only; template_id='rag_fallback' -> execute_fn не
вызывается вовсе; fetch_chunks_by_ids -> метаданные Document (title, year) +
embedding подтянуты (1 живой тест на боевом Neo4j, только чтение) + офлайн-
контракт (пустой список/сбой драйвера); _cosine (юнит).

Живой Neo4j для fetch_chunks_by_ids и одного смоука retrieve() с РЕАЛЬНЫМ
эмбеддингом вопроса (qwen3-embedding, лёгкая модель, точечно) — НИКАКОГО
живого синтеза ответа (call_answer_llm/answer-LLM) в этом файле не вызывается
вовсе — retrieval.py не знает о синтезе, это зона answer.py (см. test_answer.py).
"""
from __future__ import annotations

import pytest

from ariadna.contracts import QueryIntent
from ariadna.logutil import LOG_DIR, get_logger
from ariadna.search import retrieval

from conftest import NEO4J_LIVE, OLLAMA_LIVE

RETRIEVE_LIVE = NEO4J_LIVE and OLLAMA_LIVE


class _FakeDriver:
    """Фиктивный neo4j.Driver — .close() no-op, session() не используется
    напрямую тестами графовой ветки (execute_fn сам решает, что вернуть)."""

    def close(self):
        pass


class _FakeSession:
    """Фиктивная neo4j.Session — .run() возвращает предзаданные dict-строки
    по списку id из kwargs['ids'], без реального bolt-соединения."""

    def __init__(self, store: dict[str, dict]):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **kwargs):
        ids = kwargs.get("ids", [])
        return [dict(self._store[cid]) for cid in ids if cid in self._store]


class _FakeChunkDriver(_FakeDriver):
    """Фиктивный driver для fetch_chunks_by_ids — хранит chunk_id -> метаданные,
    session().run() отдаёт их (см. _FakeSession)."""

    def __init__(self, store: dict[str, dict]):
        self._store = store

    def session(self):
        return _FakeSession(self._store)


def _chunk_row(chunk_id: str, doc_id: str, text: str = "текст", title: str = "", year=None, embedding=None) -> dict:
    return {"chunk_id": chunk_id, "doc_id": doc_id, "text": text, "title": title, "year": year,
            "embedding": embedding}


def _vector_row(chunk_id: str, doc_id: str, text: str = "текст", title: str = "", year=None, score: float = 0.9) -> dict:
    return {"chunk_id": chunk_id, "doc_id": doc_id, "text": text, "score": score, "title": title, "year": year}


QUESTION_VEC = [1.0, 0.0]  # вектор вопроса, зафиксирован во всех тестах ранжирования


# ══════════════════════ _cosine — юнит (базовая математика) ══════════════════════

# Назначение: коллинеарные вектора -> 1.0; ортогональные -> 0.0; противоположные
#   -> -1.0; несовместимые размерности/пустой/нулевой вектор -> -1.0 (заведомо
#   худший скор, не исключение).
# Уровень: ✅ реализовано (module-dev fixer)
def test_cosine_basic_cases():
    assert retrieval._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert retrieval._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert retrieval._cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert retrieval._cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == -1.0  # разные размерности
    assert retrieval._cosine(None, [1.0, 0.0]) == -1.0
    assert retrieval._cosine([0.0, 0.0], [1.0, 0.0]) == -1.0  # нулевой вектор


# ══════════════════════ Ранжирование граф-кандидатов по векторной близости ══════════════════════

# Назначение: граф-кандидаты приходят от execute_fn в порядке, НЕ связанном с
#   релевантностью (наименее релевантный первым) — после ранжирования по
#   косинусу к question_vec итоговый порядок в chunks соответствует убыванию
#   близости, а не исходному порядку execute_fn (это и есть фикс критического
#   бага: граф больше не забивает контекст в произвольном порядке).
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_ranks_graph_chunks_by_vector_similarity_not_original_order(monkeypatch):
    # Порядок execute_fn: низкая релевантность, высокая, средняя (специально перемешан).
    graph_store = {
        "g_low": _chunk_row("g_low", "docG", embedding=[0.0, 1.0]),     # cosine(Q, .) = 0.0
        "g_high": _chunk_row("g_high", "docG", embedding=[1.0, 0.0]),   # cosine = 1.0
        "g_mid": _chunk_row("g_mid", "docG", embedding=[0.7, 0.7]),     # cosine ≈ 0.707
    }

    def _fake_execute_fn(driver, intent):
        return {"rows": [{"a": 1}], "node_ids": ["n1"],
                "chunk_ids": ["g_low", "g_high", "g_mid"], "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(retrieval, "vector_search_chunks", lambda driver, vec, top_k: [])

    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeChunkDriver(graph_store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert ids_in_order == ["g_high", "g_mid", "g_low"]


# Назначение: граф-чанк, уже найденный векторным поиском, берёт готовый score
#   ОТТУДА (не пересчитывает косинус по своему embedding) — при ранжировании
#   более высокий vector-score выигрывает у формально более длинного вектора
#   графового кандидата без score.
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_uses_vector_score_for_graph_chunk_also_found_by_vector_search(monkeypatch):
    graph_store = {
        "g_only": _chunk_row("g_only", "docG", embedding=[0.6, 0.6]),  # cosine ≈ 0.707 (средний)
    }

    def _fake_execute_fn(driver, intent):
        # "shared" тоже в графе, но его embedding сделал бы низкий cosine —
        # реальный score должен браться из vector_by_id (0.99), а не отсюда.
        return {"rows": [{"a": 1}], "node_ids": [], "chunk_ids": ["g_only", "shared"], "contradiction_pairs": []}

    def _fake_vector_search(driver, vec, top_k):
        return [_vector_row("shared", "docS", score=0.99)]

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(retrieval, "vector_search_chunks", _fake_vector_search)

    # store графа не содержит "shared" — если бы код пытался читать её embedding
    # напрямую вместо использования vector-score, fetch_chunks_by_ids просто не
    # нашёл бы её и score стал бы -1.0 (наихудший), а тест это бы поймал.
    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeChunkDriver(graph_store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert ids_in_order[0] == "shared"  # выше по score (0.99), несмотря на то что вторым в execute_fn
    assert "g_only" in ids_in_order
    assert ids_in_order.count("shared") == 1  # дедуп — не появляется отдельно ещё и вектором


# ══════════════════════ Квоты: граф ≤7, вектор ≥5, добор до 12 (фикс п.1b) ══════════════════════

# Назначение: 10 граф-кандидатов (все с разным, но высоким cosine) + 5 векторных
#   без пересечений -> итоговый пул РОВНО 12: первые 7 — граф (топ-7 по
#   рангу), следующие 5 — все векторные (вектор получает свою гарантированную
#   квоту полностью, т.к. графу больше 7 не досталось).
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_applies_graph_quota_7_and_vector_min_5(monkeypatch):
    # embedding[i] выбран так, чтобы cosine с QUESTION_VEC=[1,0] убывал по i
    # (g0 — самый релевантный, g9 — наименее) независимо от порядка execute_fn.
    graph_ids = [f"g{i}" for i in range(10)]
    graph_store = {
        cid: _chunk_row(cid, "docG", embedding=[1.0 - i * 0.05, i * 0.05])
        for i, cid in enumerate(graph_ids)
    }
    shuffled_graph_ids = list(reversed(graph_ids))  # исходный порядок execute_fn — обратный релевантности
    vector_ids = [f"v{i}" for i in range(5)]

    def _fake_execute_fn(driver, intent):
        return {"rows": [{"a": 1}], "node_ids": [], "chunk_ids": shuffled_graph_ids, "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row(cid, "docV", score=0.5) for cid in vector_ids],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeChunkDriver(graph_store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert len(ids_in_order) == 12 == retrieval.MAX_CONTEXT_CHUNKS
    assert ids_in_order[:7] == graph_ids[:7]  # топ-7 по рангу (не исходный порядок execute_fn)
    assert ids_in_order[7:] == vector_ids     # все 5 векторных — гарантированная квота


# Назначение: у графа МЕНЬШЕ кандидатов, чем квота (3 < 7) — добор до
#   MAX_CONTEXT_CHUNKS идёт вектором (вектор получает больше гарантированного
#   минимума 5, раз графу не набрать 7 своих).
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_vector_fills_beyond_min_when_graph_pool_smaller_than_quota(monkeypatch):
    graph_ids = ["g0", "g1", "g2"]
    graph_store = {cid: _chunk_row(cid, "docG", embedding=[1.0, 0.0]) for cid in graph_ids}
    vector_ids = [f"v{i}" for i in range(8)]

    def _fake_execute_fn(driver, intent):
        return {"rows": [{"a": 1}], "node_ids": [], "chunk_ids": graph_ids, "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row(cid, "docV", score=0.5) for cid in vector_ids],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeChunkDriver(graph_store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert len(ids_in_order) == 11  # 3 графа + 8 вектора (< MAX, оба пула исчерпаны)
    assert ids_in_order[:3] == graph_ids
    assert ids_in_order[3:] == vector_ids


# Назначение: у вектора МЕНЬШЕ кандидатов, чем гарантированный минимум (2 < 5),
#   а у графа кандидатов с избытком (>7) — добор недостающих мест идёт
#   ГРАФОМ (следующие по рангу сверх квоты 7), а не остаётся недобором.
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_graph_fills_beyond_quota_when_vector_pool_smaller_than_min(monkeypatch):
    graph_ids = [f"g{i}" for i in range(10)]
    graph_store = {
        cid: _chunk_row(cid, "docG", embedding=[1.0 - i * 0.05, i * 0.05])
        for i, cid in enumerate(graph_ids)
    }
    vector_ids = ["v0", "v1"]  # только 2 — меньше VECTOR_CONTEXT_MIN=5

    def _fake_execute_fn(driver, intent):
        return {"rows": [{"a": 1}], "node_ids": [], "chunk_ids": list(reversed(graph_ids)),
                "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row(cid, "docV", score=0.5) for cid in vector_ids],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeChunkDriver(graph_store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert len(ids_in_order) == 12 == retrieval.MAX_CONTEXT_CHUNKS
    # 7 граф по квоте + 2 вектора + 3 добора графом (следующие по рангу, g7..g9)
    assert ids_in_order[:7] == graph_ids[:7]
    assert ids_in_order[7:9] == vector_ids
    assert ids_in_order[9:] == graph_ids[7:10]


# Назначение: дедуп по chunk_id — id, вернувшийся И графом, И вектором,
#   появляется в итоговом пуле РОВНО ОДИН раз, и занимает место в ГРАФОВОЙ
#   квоте (не расходует отдельно вектору), затем вектор добирает следующими
#   по своему списку.
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_dedups_chunk_id_shared_between_graph_and_vector(monkeypatch):
    def _fake_execute_fn(driver, intent):
        return {"rows": [], "node_ids": [], "chunk_ids": ["shared#0", "g#1"], "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row("shared#0", "docS", score=0.95), _vector_row("v#0", "docV", score=0.5)],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    store = {"g#1": _chunk_row("g#1", "docG", embedding=[1.0, 0.0])}
    result = retrieval.retrieve(_FakeChunkDriver(store), intent, "вопрос", execute_fn=_fake_execute_fn)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert ids_in_order.count("shared#0") == 1
    assert set(ids_in_order) == {"shared#0", "g#1", "v#0"}


# ══════════════════════ Деградация: эмбеддинг вопроса недоступен (фикс, наблюдение) ══════════════════════

# Назначение: embed_texts(question) падает -> ранжировать граф-кандидатов
#   нечем (нет question_vec) — retrieve() не роняется, граф-чанки идут в
#   исходном порядке execute_fn (деградация, не потеря контекста), векторный
#   поиск вовсе не вызывается (нет вектора вопроса), SEARCH-004 в логе.
# Уровень: ✅ реализовано (module-dev fixer)
def test_retrieve_falls_back_to_original_graph_order_when_question_embedding_fails(monkeypatch):
    from ariadna.search.embeddings import EmbeddingAPIError

    def _boom_embed(texts):
        raise EmbeddingAPIError("Ollama /api/embed недоступна (смоделировано)")

    def _boom_vector_search(driver, vec, top_k):
        raise AssertionError("vector_search_chunks не должен вызываться без question_vec")

    def _fake_execute_fn(driver, intent):
        return {"rows": [{"a": 1}], "node_ids": [], "chunk_ids": ["g#2", "g#0", "g#1"], "contradiction_pairs": []}

    monkeypatch.setattr(retrieval, "embed_texts", _boom_embed)
    monkeypatch.setattr(retrieval, "vector_search_chunks", _boom_vector_search)

    store = {cid: _chunk_row(cid, "docG") for cid in ["g#0", "g#1", "g#2"]}
    intent = QueryIntent(question="q", template_id="desalination_methods")
    run_id = "test_a11_embed_fail"
    logger = get_logger("search", run_id)
    result = retrieval.retrieve(_FakeChunkDriver(store), intent, "вопрос", execute_fn=_fake_execute_fn, logger=logger)

    ids_in_order = [c["chunk_id"] for c in result["chunks"]]
    assert ids_in_order == ["g#2", "g#0", "g#1"]  # исходный порядок execute_fn, без ранжирования

    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-004" in log_text


# ══════════════════════ execute_fn падает -> SEARCH-006, деградация в вектор-only (п.2) ══════════════════════

# Назначение: execute_fn бросает исключение -> retrieve() не падает целиком,
#   графовые поля обнуляются (rows/node_ids/chunk_ids/contradiction_pairs
#   пусты), событие SEARCH-006 пишется в лог, а векторная ветка всё равно
#   отрабатывает (найденные вектором чанки не теряются).
# Уровень: ✅ реализовано (module-tester A-11)
def test_retrieve_execute_fn_failure_degrades_to_vector_only_and_logs_search006(monkeypatch):
    def _boom_execute_fn(driver, intent):
        raise RuntimeError("шаблон упал (смоделировано)")

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row("v#0", "docV", title="Векторный документ")],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    run_id = "test_a11_execute_fn_fail"
    logger = get_logger("search", run_id)
    result = retrieval.retrieve(_FakeDriver(), intent, "вопрос", execute_fn=_boom_execute_fn, logger=logger)

    assert result["rows"] == []
    assert result["node_ids"] == []
    assert result["contradiction_pairs"] == []
    assert [c["chunk_id"] for c in result["chunks"]] == ["v#0"]

    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-006" in log_text


# ══════════════════════ rag_fallback -> чисто векторная ветка (п.3) ══════════════════════

# Назначение: template_id='rag_fallback' -> execute_fn НЕ вызывается вовсе
#   (даже если он передан и валиден) — retrieve() форсирует use_graph=False
#   по template_id, не по наличию execute_fn.
# Уровень: ✅ реализовано (module-tester A-11)
def test_retrieve_rag_fallback_never_calls_execute_fn(monkeypatch):
    def _boom_execute_fn(driver, intent):
        raise AssertionError("execute_fn не должен вызываться при template_id=rag_fallback")

    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row("v#0", "docV")],
    )

    intent = QueryIntent(question="q", template_id="rag_fallback")
    result = retrieval.retrieve(_FakeDriver(), intent, "вопрос", execute_fn=_boom_execute_fn)

    assert result["node_ids"] == []
    assert [c["chunk_id"] for c in result["chunks"]] == ["v#0"]


# Назначение: execute_fn=None (A-10 ещё не приземлился/не передан) -> тоже
#   чисто векторная ветка, без исключений.
# Уровень: ✅ реализовано (module-tester A-11)
def test_retrieve_execute_fn_none_falls_back_to_vector_only(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [QUESTION_VEC])
    monkeypatch.setattr(
        retrieval, "vector_search_chunks",
        lambda driver, vec, top_k: [_vector_row("v#0", "docV")],
    )

    intent = QueryIntent(question="q", template_id="desalination_methods")
    result = retrieval.retrieve(_FakeDriver(), intent, "вопрос", execute_fn=None)

    assert result["node_ids"] == []
    assert [c["chunk_id"] for c in result["chunks"]] == ["v#0"]


# ══════════════════════ fetch_chunks_by_ids: метаданные Document (п.4) ══════════════════════

# Назначение: пустой список id -> {} без обращения к драйверу вовсе.
# Уровень: ✅ реализовано (module-tester A-11)
def test_fetch_chunks_by_ids_empty_list_returns_empty_dict_without_driver_call():
    class _BoomDriver:
        def session(self):
            raise AssertionError("driver.session() не должен вызываться для пустого списка id")

    assert retrieval.fetch_chunks_by_ids(_BoomDriver(), []) == {}


# Назначение: сбой драйвера (session()/run() бросает исключение) оборачивается
#   в VectorSearchError — единообразно с rag_demo.vector_search_chunks.
# Уровень: ✅ реализовано (module-tester A-11)
def test_fetch_chunks_by_ids_wraps_driver_exception_as_vector_search_error():
    class _BrokenDriver:
        def session(self):
            raise RuntimeError("neo4j недоступен (смоделировано)")

    with pytest.raises(retrieval.VectorSearchError):
        retrieval.fetch_chunks_by_ids(_BrokenDriver(), ["a#0"])


# Назначение: живой смоук на боевом Neo4j (только чтение) — берём несколько
#   реальных chunk_id из графа и проверяем, что fetch_chunks_by_ids подтягивает
#   метаданные документа-родителя (title непусто хотя бы у части, year — int
#   либо None, но ключ присутствует), doc_id/text заполнены, ключ embedding
#   присутствует (нужен ранжированию графа — фикс module-dev).
# Уровень: ✅ реализовано (module-tester A-11; embedding — фикс module-dev)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой Neo4j (conftest.NEO4J_LIVE)")
def test_fetch_chunks_by_ids_live_smoke_pulls_document_metadata():
    from ariadna.search import rag_demo

    driver = rag_demo.get_driver()
    try:
        with driver.session() as session:
            rows = session.run("MATCH (c:Chunk) RETURN c.chunk_id AS chunk_id LIMIT 5").data()
        real_ids = [r["chunk_id"] for r in rows]
        assert real_ids, "боевой граф должен содержать хотя бы несколько Chunk"

        fetched = retrieval.fetch_chunks_by_ids(driver, real_ids)
        assert set(fetched.keys()) == set(real_ids)
        for cid in real_ids:
            row = fetched[cid]
            assert row["doc_id"]
            assert "title" in row
            assert "year" in row
            assert "embedding" in row
    finally:
        driver.close()


# ══════════════════════ Живой смоук: retrieve() с реальным эмбеддингом (п. вектор) ══════════════════════

# Назначение: retrieve() с template_id='rag_fallback' на живом Neo4j + живом
#   embed_texts (лёгкая модель qwen3-embedding, НЕ answer-LLM синтез) -> чанки
#   находятся, node_ids/contradiction_pairs остаются пустыми (чисто векторный
#   путь). Точечный живой тест эмбеддингов — по заданию допустим 1-2 таких теста.
# Уровень: ✅ реализовано (module-tester A-11)
@pytest.mark.skipif(not RETRIEVE_LIVE, reason="нужны живые Neo4j+Ollama (conftest.NEO4J_LIVE/OLLAMA_LIVE)")
def test_retrieve_live_vector_only_smoke_finds_chunks():
    from ariadna.search import rag_demo

    driver = rag_demo.get_driver()
    try:
        intent = QueryIntent(question="q", template_id="rag_fallback")
        result = retrieval.retrieve(driver, intent, "электроэкстракция никеля", top_k=3)
        assert result["chunks"], "ожидали непустые чанки на наполненном корпусе"
        assert result["node_ids"] == []
        assert result["contradiction_pairs"] == []
    finally:
        driver.close()
