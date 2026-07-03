"""Тесты search/rag_demo.py (M-01): контракт Answer/Citation, инвариант №6 (нет
чанков -> found=False), деградация синтеза (LLM недоступна/пустой content, но
чанки есть -> честный текст + citations на месте + SEARCH-005), недоступность
Neo4j/индекса (SEARCH-004, без трейсбека наружу), парсинг ответа reasoning-модели
(<think>…</think> в content, отдельное поле message.reasoning), обход системного
HTTP_PROXY для вызова answer-LLM.

Офлайновые тесты не требуют живого стенда: embed_texts/get_driver/
vector_search_chunks/call_answer_llm подменяются заглушками monkeypatch, либо
запросы направляются на локальный HTTP-сервер поднятый самим тестом (обход
прокси — без реальной Ollama). Живой смоук (answer_question целиком) скипается,
если Ollama и/или Neo4j недоступны (см. conftest.OLLAMA_LIVE/NEO4J_LIVE).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ariadna.contracts import Answer, Citation
from ariadna.search import rag_demo

from conftest import NEO4J_LIVE, OLLAMA_LIVE

RAG_LIVE = OLLAMA_LIVE and NEO4J_LIVE


# Назначение: детерминированная заглушка embed_texts — вектор фиксированной
#   размерности без сети (для офлайновых тестов answer_question).
# Уровень: ✅ реализовано (module-tester M-01)
def _fake_embed_texts(texts, *, model=None, base_url=None):
    return [[0.1, 0.2, 0.3] for _ in texts]


# Назначение: фиктивный neo4j.Driver — .close() no-op; настоящее bolt-соединение
#   офлайновым тестам не нужно, потому что vector_search_chunks подменяется
#   отдельно на уровне answer_question в самих тестах.
# Уровень: ✅ реализовано (module-tester M-01)
class _FakeDriver:
    def close(self):
        pass


# Назначение: строки вида vector_search_chunks — n чанков одного документа;
#   long_text=True даёт текст > QUOTE_MAX_CHARS для проверки усечения цитаты.
# Уровень: ✅ реализовано (module-tester M-01)
def _rows_fixture(n: int = 2, long_text: bool = False) -> list[dict]:
    text = ("Пример текста чанка про электроэкстракцию никеля. " * 20) if long_text else "Короткий текст чанка."
    return [
        {
            "chunk_id": f"doc1#{i}",
            "doc_id": "doc1",
            "text": text,
            "score": 0.9 - i * 0.01,
            "title": "Тестовый документ",
            "year": 2020 + i,
        }
        for i in range(n)
    ]


# ══════════════════════ Контракт Answer/Citation (п.1) ══════════════════════

# Назначение: полный офлайн-путь answer_question -> валидный contracts.Answer,
#   citations собраны КОДОМ из чанков (doc_id/chunk_id/title/year/quote<=300),
#   а не из текста LLM; порядок citations = порядок найденных чанков.
# Уровень: ✅ реализовано (module-tester M-01)
def test_answer_question_returns_valid_answer_with_code_built_citations(monkeypatch):
    rows = _rows_fixture(n=3, long_text=True)
    monkeypatch.setattr(rag_demo, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())
    monkeypatch.setattr(rag_demo, "vector_search_chunks", lambda driver, vec, top_k: rows)
    monkeypatch.setattr(rag_demo, "call_answer_llm", lambda q, ctx, logger=None: "Синтезированный ответ [1][2][3].")

    answer = rag_demo.answer_question("Вопрос про электроэкстракцию?", top_k=3, run_id="test_m01_contract")

    assert isinstance(answer, Answer)
    assert answer.found is True
    assert answer.text == "Синтезированный ответ [1][2][3]."
    assert len(answer.citations) == len(rows)
    for cit, row in zip(answer.citations, rows):
        assert isinstance(cit, Citation)
        assert cit.doc_id == row["doc_id"]
        assert cit.chunk_id == row["chunk_id"]
        assert cit.title == row["title"]
        assert cit.year == row["year"]
        assert len(cit.quote) <= rag_demo.QUOTE_MAX_CHARS + 1  # +1 запас на символ «…»
        assert cit.quote.endswith("…"), "исходный текст длиннее лимита -> цитата должна быть усечена"


# Назначение: build_citations напрямую — отсутствующие title/year/doc_id/chunk_id
#   в строке не роняют сборку, а дают честные пустые/None значения по контракту.
# Уровень: ✅ реализовано (module-tester M-01)
def test_build_citations_handles_missing_optional_fields():
    rows = [{"chunk_id": "d1#0", "doc_id": "d1", "text": "текст"}]  # без title/year
    citations = rag_demo.build_citations(rows)
    assert len(citations) == 1
    assert citations[0].title == ""
    assert citations[0].year is None
    assert citations[0].quote == "текст"


# Назначение: _truncate_quote — короткий текст не режется; текст ровно на лимите
#   не режется; текст длиннее лимита режется по границе слова и получает «…»;
#   текст без пробелов (одно длинное «слово») не падает.
# Уровень: ✅ реализовано (module-tester M-01)
def test_truncate_quote_respects_limit_and_word_boundary():
    short = "короткий текст"
    assert rag_demo._truncate_quote(short) == short

    exact = "а" * rag_demo.QUOTE_MAX_CHARS
    assert rag_demo._truncate_quote(exact) == exact

    long_text = ("слово " * 100).strip()  # 599 символов, пробелы каждые 6 симв.
    truncated = rag_demo._truncate_quote(long_text)
    assert len(truncated) <= rag_demo.QUOTE_MAX_CHARS + 1
    assert truncated.endswith("…")

    one_long_word = "а" * (rag_demo.QUOTE_MAX_CHARS + 50)  # без пробелов вовсе
    truncated_word = rag_demo._truncate_quote(one_long_word)
    assert truncated_word.endswith("…")
    assert len(truncated_word) <= rag_demo.QUOTE_MAX_CHARS + 1


# ══════════════════════ Инвариант №6: нет чанков -> найдено=False (п.2) ══════════════════════

# Назначение: векторный поиск вернул 0 чанков -> found=False, честное «в корпусе
#   не найдено», citations пусты (инвариант №6 contracts.Answer) — LLM не вызывается.
# Уровень: ✅ реализовано (module-tester M-01)
def test_answer_question_no_chunks_found_returns_not_found(monkeypatch):
    monkeypatch.setattr(rag_demo, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())
    monkeypatch.setattr(rag_demo, "vector_search_chunks", lambda driver, vec, top_k: [])

    def _boom_llm(q, ctx, logger=None):
        raise AssertionError("call_answer_llm не должен вызываться без найденных чанков")

    monkeypatch.setattr(rag_demo, "call_answer_llm", _boom_llm)

    answer = rag_demo.answer_question("Вопрос без ответа в корпусе?", run_id="test_m01_no_chunks")

    assert answer.found is False
    assert answer.text == "в корпусе не найдено"
    assert answer.citations == []


# ══════════════════════ Деградация синтеза LLM (п.3, SEARCH-005) ══════════════════════

# Назначение: чанки найдены, но синтез упал (AnswerLLMError) -> found остаётся
#   True (источники есть), citations на месте, текст честно сообщает об ошибке
#   синтеза, событие SEARCH-005 попадает в лог прогона.
# Уровень: ✅ реализовано (module-tester M-01)
def test_answer_question_llm_failure_degrades_with_citations_and_logs_search005(monkeypatch):
    rows = _rows_fixture(n=2)
    monkeypatch.setattr(rag_demo, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _FakeDriver())
    monkeypatch.setattr(rag_demo, "vector_search_chunks", lambda driver, vec, top_k: rows)

    def _failing_llm(q, ctx, logger=None):
        raise rag_demo.AnswerLLMError("Ollama chat недоступна (смоделировано)")

    monkeypatch.setattr(rag_demo, "call_answer_llm", _failing_llm)

    run_id = "test_m01_llm_failure"
    answer = rag_demo.answer_question("Вопрос при недоступной LLM?", run_id=run_id)

    assert answer.found is True, "источники есть -> found=True даже если синтез не удался"
    assert len(answer.citations) == len(rows)
    assert "не удалось" in answer.text.lower()

    from ariadna.logutil import LOG_DIR
    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-005" in log_text


# ══════════════════════ Недоступность Neo4j/индекса (п.4, SEARCH-004) ══════════════════════

# Назначение: vector_search_chunks оборачивает ЛЮБОЙ сбой драйвера/индекса
#   (session()/run() бросает исключение) в VectorSearchError — не голый
#   трейсбек драйвера наружу.
# Уровень: ✅ реализовано (module-tester M-01)
def test_vector_search_chunks_wraps_driver_exception_as_vector_search_error():
    class _BrokenDriver:
        def session(self):
            raise RuntimeError("neo4j недоступен (смоделировано)")

    with pytest.raises(rag_demo.VectorSearchError):
        rag_demo.vector_search_chunks(_BrokenDriver(), [0.1, 0.2], top_k=5)


# Назначение: сквозной путь answer_question при недоступном Neo4j/индексе ->
#   осмысленное found=False «в корпусе не найдено» (без трейсбека наружу),
#   событие SEARCH-004 в логе прогона; driver.close() всё равно вызывается (finally).
# Уровень: ✅ реализовано (module-tester M-01)
def test_answer_question_neo4j_unavailable_returns_not_found_and_logs_search004(monkeypatch):
    closed = {"called": False}

    class _DriverThatCloses(_FakeDriver):
        def close(self):
            closed["called"] = True

    monkeypatch.setattr(rag_demo, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(rag_demo, "get_driver", lambda: _DriverThatCloses())

    def _broken_search(driver, vec, top_k):
        raise rag_demo.VectorSearchError("индекс chunk_embedding_idx недоступен (смоделировано)")

    monkeypatch.setattr(rag_demo, "vector_search_chunks", _broken_search)

    run_id = "test_m01_neo4j_down"
    answer = rag_demo.answer_question("Вопрос при недоступном Neo4j?", run_id=run_id)

    assert answer.found is False
    assert "не найдено" in answer.text
    assert closed["called"], "driver.close() обязан вызваться даже при сбое поиска (finally)"

    from ariadna.logutil import LOG_DIR
    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-004" in log_text


# Назначение: сбой эмбеддинга вопроса (EmbeddingAPIError) тоже деградирует
#   честно — found=False «в корпусе не найдено», без похода в Neo4j/LLM вовсе.
# Уровень: ✅ реализовано (module-tester M-01)
def test_answer_question_embedding_failure_returns_not_found(monkeypatch):
    def _boom_embed(texts):
        raise rag_demo.EmbeddingAPIError("Ollama /api/embed недоступна (смоделировано)")

    monkeypatch.setattr(rag_demo, "embed_texts", _boom_embed)

    def _boom_driver():
        raise AssertionError("get_driver не должен вызываться, если эмбеддинг вопроса не посчитан")

    monkeypatch.setattr(rag_demo, "get_driver", _boom_driver)

    answer = rag_demo.answer_question("Вопрос при недоступном эмбеддинге?", run_id="test_m01_embed_fail")

    assert answer.found is False
    assert "не найдено" in answer.text


# ══════════════════════ Парсинг ответа reasoning-модели (п.5) ══════════════════════

# Назначение: _strip_think_tags — блок <think>…</think> (в т.ч. многострочный,
#   регистронезависимо) вырезается целиком, окружающий текст сохраняется.
# Уровень: ✅ реализовано (module-tester M-01)
def test_strip_think_tags_removes_block_case_insensitive_multiline():
    text = "<THINK>\nразмышления\nна несколько строк\n</THINK>Итоговый ответ."
    assert rag_demo._strip_think_tags(text) == "Итоговый ответ."

    assert rag_demo._strip_think_tags("Ответ без тегов.") == "Ответ без тегов."


# Назначение: фиктивный ответ Ollama с <think>…</think> ВНУТРИ content ->
#   call_answer_llm возвращает только текст после тега (вариант «thinking в content»).
# Уровень: ✅ реализовано (module-tester M-01)
def test_call_answer_llm_strips_think_tags_from_content(monkeypatch):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            body = {"choices": [{"message": {"content": "<think>черновые рассуждения</think>Итоговый ответ [1]."}}]}
            return json.dumps(body).encode("utf-8")

    monkeypatch.setattr(rag_demo._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp())
    result = rag_demo.call_answer_llm("вопрос", "контекст")
    assert result == "Итоговый ответ [1]."


# Назначение: вариант «thinking в отдельном поле message.reasoning» при
#   НЕПУСТОМ content -> content возвращается как есть, reasoning игнорируется
#   (используется только как фоллбек-контекст, когда content пуст).
# Уровень: ✅ реализовано (module-tester M-01)
def test_call_answer_llm_uses_content_ignores_separate_reasoning_field(monkeypatch):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            body = {"choices": [{"message": {"content": "Финальный ответ.", "reasoning": "отдельные размышления"}}]}
            return json.dumps(body).encode("utf-8")

    monkeypatch.setattr(rag_demo._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp())
    result = rag_demo.call_answer_llm("вопрос", "контекст")
    assert result == "Финальный ответ."


# Назначение: content пуст (весь бюджет ушёл в reasoning) -> AnswerLLMError,
#   а не пустая строка/трейсбек; событие SEARCH-005 пишется в лог с превью reasoning.
# Уровень: ✅ реализовано (module-tester M-01)
def test_call_answer_llm_empty_content_raises_and_logs_search005(monkeypatch):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            body = {"choices": [{"message": {"content": "", "reasoning": "весь бюджет ушёл на размышления"}}]}
            return json.dumps(body).encode("utf-8")

    monkeypatch.setattr(rag_demo._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp())

    from ariadna.logutil import get_logger

    run_id = "test_m01_empty_content"
    logger = get_logger("search", run_id)
    with pytest.raises(rag_demo.AnswerLLMError):
        rag_demo.call_answer_llm("вопрос", "контекст", logger=logger)

    from ariadna.logutil import LOG_DIR
    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-005" in log_text


# Назначение: мёртвый хост -> AnswerLLMError (не голый urllib.error наружу),
#   тот же контракт, что embeddings.embed_texts для SEARCH-003.
# Уровень: ✅ реализовано (module-tester M-01)
def test_call_answer_llm_dead_host_raises_answer_llm_error():
    with pytest.raises(rag_demo.AnswerLLMError):
        rag_demo.call_answer_llm("вопрос", "контекст", base_url="http://127.0.0.1:1")


# ══════════════════════ Обход HTTP_PROXY (п.6) ══════════════════════

# Назначение: обработчик локального тестового HTTP-сервера — имитирует Ollama
#   `/v1/chat/completions`, отдаёт валидный JSON без обращения к сети/GPU.
# Уровень: ✅ реализовано (module-tester M-01)
class _FakeChatHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — имя метода фиксировано BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"choices": [{"message": {"content": "Ответ с локального сервера."}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 — подавляем access-лог http.server в stdout тестов
        pass


# Назначение: клиент не должен использовать HTTP_PROXY/HTTPS_PROXY из окружения —
#   даже с заведомо мёртвым прокси-адресом запрос к локальному тестовому серверу
#   проходит напрямую (без живой Ollama — свой HTTP-сервер поднят самим тестом).
# Уровень: ✅ реализовано (module-tester M-01)
def test_call_answer_llm_bypasses_http_proxy_env(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeChatHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")

        result = rag_demo.call_answer_llm("вопрос", "контекст", base_url=f"http://127.0.0.1:{port}")
        assert result == "Ответ с локального сервера."
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ══════════════════════ Живой смоук (п.7) ══════════════════════

# Назначение: живой сквозной путь — короткий вопрос -> Answer(found=True) с
#   непустыми citations на наполненном корпусе (177 Document/9580 Chunk). Синтез
#   reasoning-моделью на живом стенде занимает ~2-3 минуты даже на тривиальном
#   вопросе (ANSWER_TIMEOUT_SEC=240 в rag_demo.py уже закладывает этот запас) —
#   вопрос выбран короткий, чтобы не раздувать время сверх нужного. Скип, если
#   Ollama и/или Neo4j не подняты (conftest.OLLAMA_LIVE/NEO4J_LIVE).
# Уровень: ✅ реализовано (module-tester M-01)
@pytest.mark.skipif(not RAG_LIVE, reason="нужен живой стенд Ollama+Neo4j (OLLAMA_LIVE/NEO4J_LIVE)")
def test_answer_question_live_smoke_short_question():
    answer = rag_demo.answer_question(
        "Что такое электроэкстракция никеля?", top_k=3, run_id="test_m01_live_smoke",
    )
    assert isinstance(answer, Answer)
    assert answer.found is True
    assert answer.citations, "ожидали непустые citations на живом наполненном корпусе"
    assert answer.text.strip() != ""
