"""Тесты analytics/recommendations.py (A-14): блок «Рекомендации» (У-1) —
similar_case (векторная близость) + expert/adjacent_topic (Cypher), без LLM.

Офлайновые тесты (мок driver — _FakeDriver/_FakeSession, тот же паттерн, что
tests/analytics/test_gap_map.py/tests/search/test_retrieval.py): embed_texts/
vector_search_chunks/fetch_chunks_by_ids монкипатчатся модулем-точкой импорта
(ariadna.analytics.recommendations.*), Cypher — через _FakeDriver.rows_by_query.
"""
from __future__ import annotations

import pytest

from ariadna.analytics import recommendations as recs_mod
from ariadna.contracts import Answer, Citation, Recommendation, RecommendationKind
from ariadna.graph.recommendation_queries import RECOMMENDATION_ADJACENT_QUERY, RECOMMENDATION_EXPERT_QUERY
from ariadna.logutil import get_logger, new_run_id
from ariadna.search.rag_demo import VectorSearchError
from ariadna.search.embeddings import EmbeddingAPIError


# Назначение: настоящий логгер для тестов, задевающих ветки log_event(ERROR) —
#   log_event() ожидает валидный LoggerAdapter, не None (см. logutil.py).
# Уровень: ✅ реализовано (module-dev A-14)
def _test_logger():
    return get_logger("analytics", new_run_id("test_recommendations_"))


# ══════════════════════ Фиктивный driver (офлайн) ══════════════════════

class _FakeSession:
    """Фиктивная neo4j.Session — .run(query, **kwargs) отдаёт заранее заданный
    список dict-строк по тексту запроса (без реального bolt-соединения)."""

    def __init__(self, rows_by_query, calls):
        self._rows_by_query = rows_by_query
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **kwargs):
        self._calls.append((query, kwargs))
        return [dict(r) for r in self._rows_by_query.get(query, [])]


class _FakeDriver:
    """Фиктивный neo4j.Driver — .session() отдаёт _FakeSession с заготовленными
    строками по тексту запроса."""

    def __init__(self, rows_by_query=None):
        self._rows_by_query = rows_by_query or {}
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return _FakeSession(self._rows_by_query, self.calls)


def _answer(citations=None, subgraph_node_ids=None) -> Answer:
    return Answer(
        question="вопрос",
        text="ответ",
        citations=citations or [],
        subgraph_node_ids=subgraph_node_ids or [],
        found=True,
    )


# ══════════════════════ driver=None -> [] ══════════════════════

# Назначение: driver=None -> [] БЕЗ обращения к embed_texts/Cypher (рекомендации —
#   необязательный доп. блок, не самостоятельный CLI-путь, в отличие от gap_map).
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_recommendations_none_driver_returns_empty_list(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("driver=None не должен обращаться ни к embed_texts, ни к Cypher")

    monkeypatch.setattr(recs_mod, "embed_texts", _boom)
    result = recs_mod.build_recommendations(None, "вопрос", _answer())
    assert result == []


# ══════════════════════ _build_similar_case ══════════════════════

# Назначение: агрегация до документа (первый по score чанк — представитель),
#   исключение doc_id, уже процитированных в ответе, обрезка до top_k.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_similar_case_dedupes_by_doc_excludes_cited_and_respects_top_k(monkeypatch):
    monkeypatch.setattr(recs_mod, "embed_texts", lambda texts: [[0.1, 0.2]])
    rows = [
        {"chunk_id": "cited#0", "doc_id": "doc-cited", "text": "текст", "score": 0.99, "title": "Т1", "year": 2020},
        {"chunk_id": "docA#0", "doc_id": "doc-a", "text": "текст А", "score": 0.9, "title": "Док А", "year": 2021},
        {"chunk_id": "docA#1", "doc_id": "doc-a", "text": "второй чанк А", "score": 0.8, "title": "Док А", "year": 2021},
        {"chunk_id": "docB#0", "doc_id": "doc-b", "text": "текст Б", "score": 0.7, "title": "Док Б", "year": 2019},
        {"chunk_id": "docC#0", "doc_id": "doc-c", "text": "текст В", "score": 0.6, "title": "Док В", "year": 2018},
    ]
    monkeypatch.setattr(recs_mod, "vector_search_chunks", lambda driver, vec, k: rows)

    driver = _FakeDriver()
    answer = _answer(citations=[Citation(doc_id="doc-cited", chunk_id="cited#0")])

    result = recs_mod._build_similar_case(driver, "вопрос", {"doc-cited"}, top_k=2, logger=None)

    assert len(result) == 2
    assert [r.title for r in result] == ["Док А", "Док Б"]
    assert all(r.kind == RecommendationKind.SIMILAR_CASE for r in result)
    assert result[0].citations[0].doc_id == "doc-a"
    assert "0.90" in result[0].reason


# Назначение: сбой embed_texts (EmbeddingAPIError) -> [] (лог ANALYTICS-003),
#   vector_search_chunks не вызывается.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_similar_case_embedding_failure_returns_empty_list(monkeypatch):
    def _boom(texts):
        raise EmbeddingAPIError("Ollama недоступна")

    monkeypatch.setattr(recs_mod, "embed_texts", _boom)
    monkeypatch.setattr(recs_mod, "vector_search_chunks",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должен вызываться")))

    result = recs_mod._build_similar_case(_FakeDriver(), "вопрос", set(), top_k=3, logger=_test_logger())
    assert result == []


# Назначение: сбой vector_search_chunks (VectorSearchError) -> [] (лог ANALYTICS-003).
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_similar_case_vector_search_failure_returns_empty_list(monkeypatch):
    monkeypatch.setattr(recs_mod, "embed_texts", lambda texts: [[0.1]])

    def _boom(driver, vec, k):
        raise VectorSearchError("индекс недоступен")

    monkeypatch.setattr(recs_mod, "vector_search_chunks", _boom)
    result = recs_mod._build_similar_case(_FakeDriver(), "вопрос", set(), top_k=3, logger=_test_logger())
    assert result == []


# Назначение: top_k=0 -> [] без обращения к embed_texts.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_similar_case_top_k_zero_returns_empty_without_embedding(monkeypatch):
    monkeypatch.setattr(recs_mod, "embed_texts",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должен вызываться")))
    result = recs_mod._build_similar_case(_FakeDriver(), "вопрос", set(), top_k=0, logger=None)
    assert result == []


# ══════════════════════ _build_expert ══════════════════════

# Назначение: эксперты из RECOMMENDATION_EXPERT_QUERY -> Recommendation с reason
#   «упоминается в N источниках по теме», citations — метаданные чанков через
#   fetch_chunks_by_ids (замокан), обрезка до EXPERT_MAX_CITATIONS.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_expert_builds_recommendations_with_reason_and_citations(monkeypatch):
    expert_rows = [
        {"id": "expert:ivanov", "name": "Иванов И.И.", "n_matched_sources": 3,
         "sample_chunk_ids": ["c1", "c2", "c3"], "n_mentions": 10},
    ]
    driver = _FakeDriver({RECOMMENDATION_EXPERT_QUERY: expert_rows})
    chunk_meta = {
        "c1": {"doc_id": "doc-1", "text": "фрагмент один", "title": "Документ 1", "year": 2019},
        "c2": {"doc_id": "doc-2", "text": "фрагмент два", "title": "Документ 2", "year": 2020},
    }
    monkeypatch.setattr(recs_mod, "fetch_chunks_by_ids", lambda d, ids: chunk_meta)

    result = recs_mod._build_expert(driver, ["cited#0"], ["doc-cited"], ["node-1"], top_k=3, logger=None)

    assert len(result) == 1
    rec = result[0]
    assert rec.kind == RecommendationKind.EXPERT
    assert rec.title == "Иванов И.И."
    assert rec.reason == "упоминается в 3 источниках по теме"
    assert len(rec.citations) == recs_mod.EXPERT_MAX_CITATIONS
    assert {c.doc_id for c in rec.citations} == {"doc-1", "doc-2"}


# Назначение: n_matched_sources=0 (эксперт найден только через связь с узлом
#   подграфа, без со-упоминания в чанках ответа) -> запасной reason.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_expert_zero_matched_sources_uses_fallback_reason(monkeypatch):
    expert_rows = [{"id": "expert:petrov", "name": "Петров П.П.", "n_matched_sources": 0,
                    "sample_chunk_ids": [], "n_mentions": 2}]
    driver = _FakeDriver({RECOMMENDATION_EXPERT_QUERY: expert_rows})
    monkeypatch.setattr(recs_mod, "fetch_chunks_by_ids", lambda d, ids: {})

    result = recs_mod._build_expert(driver, [], [], ["node-1"], top_k=3, logger=None)

    assert result[0].reason == "связан с сущностями темы вопроса"
    assert result[0].citations == []


# Назначение: пустые chunk_ids/doc_ids/node_ids -> [] БЕЗ обращения к driver.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_expert_no_inputs_returns_empty_without_driver_call():
    driver = _FakeDriver()
    result = recs_mod._build_expert(driver, [], [], [], top_k=3, logger=None)
    assert result == []
    assert driver.calls == []


# Назначение: сбой Cypher-запроса (driver.session бросает исключение) -> []
#   (лог ANALYTICS-004), не падает.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_expert_query_failure_returns_empty_list():
    class _BrokenDriver:
        def session(self):
            raise RuntimeError("Neo4j недоступен")

    result = recs_mod._build_expert(_BrokenDriver(), ["c1"], [], [], top_k=3, logger=_test_logger())
    assert result == []


# ══════════════════════ _build_adjacent_topic ══════════════════════

# Назначение: соседи предпочитают тип Material/Process/Property (тир 0) над
#   прочими типами (тир 1), внутри тира — по n_mentions DESC; дедуп кандидата,
#   найденного и relation-hop, и co-mention путём; обрезка до top_k.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_adjacent_topic_prefers_material_process_property_then_mentions():
    rows = [
        {"id": "n-expert", "name": "Эксперт-сосед", "type": "Expert", "via_id": "n1", "via_name": "Узел 1",
         "n_mentions": 100},
        {"id": "n-material", "name": "Материал-сосед", "type": "Material", "via_id": "n1", "via_name": "Узел 1",
         "n_mentions": 5},
        {"id": "n-process", "name": "Процесс-сосед", "type": "Process", "via_id": "n2", "via_name": "Узел 2",
         "n_mentions": 20},
        {"id": "n-material", "name": "Материал-сосед", "type": "Material", "via_id": "n2", "via_name": "Узел 2",
         "n_mentions": 5},
    ]
    driver = _FakeDriver({RECOMMENDATION_ADJACENT_QUERY: rows})

    result = recs_mod._build_adjacent_topic(driver, ["n1", "n2"], top_k=2, logger=None)

    assert len(result) == 2
    assert all(r.kind == RecommendationKind.ADJACENT_TOPIC for r in result)
    titles = [r.title for r in result]
    assert "Процесс-сосед" in titles and "Материал-сосед" in titles
    assert "Эксперт-сосед" not in titles  # тир 1, не прошёл top_k=2 среди тира 0


# Назначение: пустой node_ids -> [] без обращения к driver.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_adjacent_topic_empty_node_ids_returns_empty_without_driver_call():
    driver = _FakeDriver()
    result = recs_mod._build_adjacent_topic(driver, [], top_k=3, logger=None)
    assert result == []
    assert driver.calls == []


# Назначение: reason указывает узел-посредник (via_name).
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_adjacent_topic_reason_names_via_entity():
    rows = [{"id": "n-x", "name": "Тема-сосед", "type": "Process", "via_id": "n1", "via_name": "Флотация",
             "n_mentions": 1}]
    driver = _FakeDriver({RECOMMENDATION_ADJACENT_QUERY: rows})

    result = recs_mod._build_adjacent_topic(driver, ["n1"], top_k=3, logger=None)

    assert result[0].reason == "связана через «Флотация»"


# Назначение: сбой Cypher-запроса -> [] (лог ANALYTICS-004), не падает.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_adjacent_topic_query_failure_returns_empty_list():
    class _BrokenDriver:
        def session(self):
            raise RuntimeError("Neo4j недоступен")

    result = recs_mod._build_adjacent_topic(_BrokenDriver(), ["n1"], top_k=3, logger=_test_logger())
    assert result == []


# ══════════════════════ build_recommendations — сортировка/интеграция ══════════════════════

# Назначение: итоговый список отсортирован similar_case -> expert ->
#   adjacent_topic (порядок видов, паспорт модуля), top_k применяется на КАЖДЫЙ вид.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_recommendations_orders_kinds_similar_expert_adjacent(monkeypatch):
    monkeypatch.setattr(
        recs_mod, "_build_similar_case",
        lambda driver, question, cited_doc_ids, top_k, logger: [
            Recommendation(kind=RecommendationKind.SIMILAR_CASE, title="s1"),
        ],
    )
    monkeypatch.setattr(
        recs_mod, "_build_expert",
        lambda driver, cc, cd, nid, top_k, logger: [
            Recommendation(kind=RecommendationKind.EXPERT, title="e1"),
        ],
    )
    monkeypatch.setattr(
        recs_mod, "_build_adjacent_topic",
        lambda driver, nid, top_k, logger: [
            Recommendation(kind=RecommendationKind.ADJACENT_TOPIC, title="a1"),
        ],
    )

    result = recs_mod.build_recommendations(_FakeDriver(), "вопрос", _answer(), top_k=3)

    assert [r.kind for r in result] == [
        RecommendationKind.SIMILAR_CASE, RecommendationKind.EXPERT, RecommendationKind.ADJACENT_TOPIC,
    ]


# Назначение: build_recommendations пробрасывает citations/subgraph_node_ids
#   ответа в соответствующие приватные builder'ы правильными аргументами.
# Уровень: ✅ реализовано (module-dev A-14)
def test_build_recommendations_passes_citations_and_node_ids_to_builders(monkeypatch):
    captured = {}

    def _fake_similar(driver, question, cited_doc_ids, top_k, logger):
        captured["cited_doc_ids"] = cited_doc_ids
        return []

    def _fake_expert(driver, cited_chunk_ids, cited_doc_ids, node_ids, top_k, logger):
        captured["expert_chunk_ids"] = cited_chunk_ids
        captured["expert_node_ids"] = node_ids
        return []

    def _fake_adjacent(driver, node_ids, top_k, logger):
        captured["adjacent_node_ids"] = node_ids
        return []

    monkeypatch.setattr(recs_mod, "_build_similar_case", _fake_similar)
    monkeypatch.setattr(recs_mod, "_build_expert", _fake_expert)
    monkeypatch.setattr(recs_mod, "_build_adjacent_topic", _fake_adjacent)

    answer = _answer(
        citations=[Citation(doc_id="doc-1", chunk_id="doc-1#0")],
        subgraph_node_ids=["node-a", "node-b"],
    )
    recs_mod.build_recommendations(_FakeDriver(), "вопрос", answer, top_k=3)

    assert captured["cited_doc_ids"] == {"doc-1"}
    assert captured["expert_chunk_ids"] == ["doc-1#0"]
    assert captured["expert_node_ids"] == ["node-a", "node-b"]
    assert captured["adjacent_node_ids"] == ["node-a", "node-b"]
