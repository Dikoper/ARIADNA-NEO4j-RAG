"""Тесты search/answer.py (A-11): answer_question() — вопрос -> роутер ->
retrieval -> синтез -> contracts.Answer. ВЕСЬ синтез (rag_demo.call_answer_llm)
здесь ТОЛЬКО замокан — ни один тест этого файла не обращается к живой Ollama
для генерации ответа (ограничение ресурсов — параллельный интеграционный смоук
оркестратора). retrieve() тоже подменяется целиком (monkeypatch answer.retrieve)
— гибридное слияние граф+вектор уже покрыто test_retrieval.py, здесь важен
только контракт answer_question() поверх произвольного результата retrieve().

Покрыто: happy path (Answer с citations из чанков контекста, contradictions из
contradiction_pairs, subgraph_node_ids, Answer.model_validate); пустой retrieval
-> found=False «в корпусе не найдено» citations=[] (инвариант №6); синтез
недоступен дважды (первая попытка + ретрай) -> экстрактивная деградация
(SEARCH-007), found=True, citations сохранены; цитаты (quote<=300, doc_id/
chunk_id реальные); route_fn упал -> честный rag_fallback (не роняет ответ);
ANSWER_BACKEND=anthropic -> заглушка без обращения к call_answer_llm.
"""
from __future__ import annotations

from ariadna.contracts import Answer, Citation, QueryIntent
from ariadna.logutil import LOG_DIR
from ariadna.search import answer, rag_demo


class _FakeDriver:
    def close(self):
        pass


def _make_retrieve_stub(result: dict, captured: dict | None = None):
    # Назначение: подменяет answer.retrieve() заранее заданным результатом —
    #   изолирует контракт answer_question от гибридного слияния (test_retrieval.py).
    # Уровень: ✅ реализовано (module-tester A-11)
    def _stub(driver, intent, question, *, execute_fn=None, top_k=None, logger=None):
        if captured is not None:
            captured["intent"] = intent
        return result

    return _stub


def _long_chunk(chunk_id: str, doc_id: str, title: str = "Документ", year: int = 2021) -> dict:
    text = ("Пример текста чанка про электроэкстракцию никеля и циркуляцию католита. " * 10)
    return {"chunk_id": chunk_id, "doc_id": doc_id, "text": text, "title": title, "year": year}


# ══════════════════════ Happy path: Answer с citations/contradictions/subgraph (п.1) ══════════════════════

# Назначение: полный офлайн-путь (route_fn+retrieve+синтез замоканы) ->
#   валидный Answer: citations построены из чанков контекста, contradictions —
#   из contradiction_pairs, subgraph_node_ids — из node_ids retrieve().
# Уровень: ✅ реализовано (module-tester A-11)
def test_answer_question_happy_path_builds_citations_contradictions_and_subgraph(monkeypatch):
    chunks = [_long_chunk("docA#0", "docA", title="Документ A", year=2020),
              _long_chunk("docB#5", "docB", title="Документ B", year=2022)]
    contradiction_pairs = [{
        "node_a_id": "n1", "node_b_id": "n2", "name_a": "Обратный осмос", "name_b": "Мембранная дистилляция",
        "doc_id": "docC", "chunk_id": "docC#2", "title": "Документ C", "year": 2019,
        "quote": "Цитата провенанса противоречия.",
    }]
    retrieve_result = {"chunks": chunks, "rows": [{"x": 1}], "node_ids": ["n1", "n2"],
                        "contradiction_pairs": contradiction_pairs}

    monkeypatch.setattr(answer, "retrieve", _make_retrieve_stub(retrieve_result))
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())
    monkeypatch.setattr(rag_demo, "call_answer_llm", lambda q, ctx, logger=None: "Синтезированный ответ [1][2].")

    route_fn = lambda q: QueryIntent(question=q, template_id="desalination_methods")  # noqa: E731

    result = answer.answer_question(
        "Какие методы обессоливания подходят?", route_fn=route_fn,
        execute_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("execute_fn не должен вызываться напрямую")),
        run_id="test_a11_answer_happy",
    )

    assert isinstance(result, Answer)
    Answer.model_validate(result.model_dump())
    assert result.found is True
    assert result.text == "Синтезированный ответ [1][2]."
    assert result.subgraph_node_ids == ["n1", "n2"]

    assert len(result.citations) == 2
    for cit, chunk in zip(result.citations, chunks):
        assert isinstance(cit, Citation)
        assert cit.doc_id == chunk["doc_id"]
        assert cit.chunk_id == chunk["chunk_id"]
        assert cit.title == chunk["title"]
        assert cit.year == chunk["year"]
        assert len(cit.quote) <= 300 + 1  # +1 запас на символ «…»
        assert cit.quote.endswith("…")  # исходный текст длиннее лимита

    assert len(result.contradictions) == 1
    contradiction = result.contradictions[0]
    assert contradiction.claim_a == "Обратный осмос"
    assert contradiction.claim_b == "Мембранная дистилляция"
    assert len(contradiction.citations) == 1
    assert contradiction.citations[0].doc_id == "docC"
    assert contradiction.citations[0].chunk_id == "docC#2"
    assert contradiction.citations[0].quote == "Цитата провенанса противоречия."


# ══════════════════════ Инвариант №6: пустой retrieval -> found=False (п.2) ══════════════════════

# Назначение: retrieve() не нашёл ни одного чанка -> found=False, честное
#   «в корпусе не найдено», citations=[] — синтез (call_answer_llm) НЕ вызывается.
# Уровень: ✅ реализовано (module-tester A-11)
def test_answer_question_empty_retrieval_returns_not_found_without_calling_llm(monkeypatch):
    empty_result = {"chunks": [], "rows": [], "node_ids": [], "contradiction_pairs": []}
    monkeypatch.setattr(answer, "retrieve", _make_retrieve_stub(empty_result))
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())

    def _boom_llm(q, ctx, logger=None):
        raise AssertionError("call_answer_llm не должен вызываться без найденных чанков")

    monkeypatch.setattr(rag_demo, "call_answer_llm", _boom_llm)

    route_fn = lambda q: QueryIntent(question=q, template_id="rag_fallback")  # noqa: E731
    result = answer.answer_question("Вопрос без ответа в корпусе?", route_fn=route_fn, run_id="test_a11_answer_empty")

    assert result.found is False
    assert result.text == "в корпусе не найдено"
    assert result.citations == []


# ══════════════════════ Экстрактивная деградация (п.3, SEARCH-007) ══════════════════════

# Назначение: синтез недоступен ОБЕ попытки (первая + ретрай упрощённым
#   промптом) -> экстрактивная деградация: found=True (источники есть),
#   citations сохранены, текст явно помечен как выжимка, SEARCH-007 в логе.
# Уровень: ✅ реализовано (module-tester A-11)
def test_answer_question_synthesis_fails_twice_degrades_to_extractive_fallback(monkeypatch):
    chunks = [_long_chunk("docA#0", "docA"), _long_chunk("docA#1", "docA")]
    retrieve_result = {"chunks": chunks, "rows": [], "node_ids": [], "contradiction_pairs": []}
    monkeypatch.setattr(answer, "retrieve", _make_retrieve_stub(retrieve_result))
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())

    call_count = {"n": 0}

    def _always_failing_llm(q, ctx, logger=None):
        call_count["n"] += 1
        raise rag_demo.AnswerLLMError("Ollama chat недоступна (смоделировано)")

    monkeypatch.setattr(rag_demo, "call_answer_llm", _always_failing_llm)

    route_fn = lambda q: QueryIntent(question=q, template_id="desalination_methods")  # noqa: E731
    run_id = "test_a11_answer_extractive"
    result = answer.answer_question("Вопрос при недоступном синтезе?", route_fn=route_fn, run_id=run_id)

    assert call_count["n"] == 2, "ожидались обе попытки: первая + ретрай упрощённым промптом"
    assert result.found is True
    assert "экстрактивная выжимка" in result.text.lower()
    assert len(result.citations) == 2
    assert result.citations[0].doc_id == "docA"
    assert result.citations[0].chunk_id == "docA#0"

    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-007" in log_text


# ══════════════════════ ANSWER_BACKEND=anthropic — заглушка (бонус) ══════════════════════

# Назначение: ANSWER_BACKEND=anthropic (решение PM 03.07.2026 — Claude API
#   отключён) -> экстрактивная деградация СРАЗУ, без единой попытки вызвать
#   call_answer_llm (заглушка, не реализация Anthropic-клиента заново).
# Уровень: ✅ реализовано (module-tester A-11)
def test_answer_question_anthropic_backend_stub_never_calls_ollama_llm(monkeypatch):
    chunks = [_long_chunk("docA#0", "docA")]
    retrieve_result = {"chunks": chunks, "rows": [], "node_ids": [], "contradiction_pairs": []}
    monkeypatch.setattr(answer, "retrieve", _make_retrieve_stub(retrieve_result))
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())
    monkeypatch.setenv("ANSWER_BACKEND", "anthropic")

    def _boom_llm(q, ctx, logger=None):
        raise AssertionError("call_answer_llm не должен вызываться при ANSWER_BACKEND=anthropic")

    monkeypatch.setattr(rag_demo, "call_answer_llm", _boom_llm)

    route_fn = lambda q: QueryIntent(question=q, template_id="desalination_methods")  # noqa: E731
    result = answer.answer_question("Вопрос?", route_fn=route_fn, run_id="test_a11_answer_anthropic")

    assert result.found is True
    assert "экстрактивная выжимка" in result.text.lower()
    assert len(result.citations) == 1


# ══════════════════════ route_fn упал -> честный rag_fallback (бонус) ══════════════════════

# Назначение: route_fn бросает исключение -> _route гасит его и возвращает
#   QueryIntent(template_id='rag_fallback') — answer_question не падает целиком,
#   retrieve() получает именно rag_fallback-intent.
# Уровень: ✅ реализовано (module-tester A-11)
def test_answer_question_route_fn_failure_degrades_to_rag_fallback_intent(monkeypatch):
    captured: dict = {}
    empty_result = {"chunks": [], "rows": [], "node_ids": [], "contradiction_pairs": []}
    monkeypatch.setattr(answer, "retrieve", _make_retrieve_stub(empty_result, captured))
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())

    def _boom_route(question):
        raise RuntimeError("роутер упал (смоделировано)")

    result = answer.answer_question("Вопрос?", route_fn=_boom_route, run_id="test_a11_answer_route_fail")

    assert result.found is False
    assert captured["intent"].template_id == "rag_fallback"
