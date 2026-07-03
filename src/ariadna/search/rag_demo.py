"""Страховочное демо: чисто векторный путь «вопрос → ответ с цитатами» (M-01).

Вход: вопрос пользователя (строка, CLI-аргумент) + наполненный Neo4j (vector index
`chunk_embedding_idx` по Chunk.embedding, A-05) + Ollama (ANSWER_MODEL, .env).
Выход: `contracts.Answer` — текст синтеза + citations, построенные КОДОМ из
найденных чанков (LLM тексту не доверяем на предмет doc_id/chunk_id).
Зависимости: `ariadna.search.embeddings.embed_texts` (эмбеддинг вопроса — не
дублирует HTTP-код), `neo4j` (bolt, только чтение), Ollama `/v1/chat/completions`.
Инварианты: без роутера и без графовых Cypher-шаблонов — future `rag_fallback`
(template_id='rag_fallback', SEARCH-001) для роутера A-10; нет найденных чанков →
found=False, текст «в корпусе не найдено» (инвариант №6, contracts.Answer).
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request

from neo4j import Driver, GraphDatabase

from ariadna.contracts import Answer, Citation
from ariadna.graph.config import CHUNK_VECTOR_INDEX_NAME
from ariadna.logutil import get_logger, log_event, new_run_id
# Импорт embeddings подхватывает .env в os.environ (embeddings._load_dotenv при
# импорте) — переиспользуем побочный эффект вместо своего загрузчика .env.
from ariadna.search.embeddings import EmbeddingAPIError, embed_texts

DEFAULT_TOP_K = 8
QUOTE_MAX_CHARS = 300  # contracts.Citation.quote — лимит по контракту

# Reasoning-модель тратит бюджет max_tokens на thinking (наблюдалось на живом
# стенде: ~1500 ток. thinking на тривиальном вопросе) — закладываем большой запас,
# иначе content обрезается пустым (docs/dev/modules/search.md).
ANSWER_MAX_TOKENS = 8192
ANSWER_TIMEOUT_SEC = 240  # синтез с thinking дольше эмбеддинга — не переиспользуем таймаут embeddings

OLLAMA_BASE_URL_DEFAULT = "http://localhost:11434"
ANSWER_MODEL_DEFAULT = "qwen3.5:35b-a3b"

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Ollama — локальная инфраструктура (не внешний сервис): системный прокси не
# должен применяться к localhost, иначе 502 (тот же факт, что и в embeddings.py).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class VectorSearchError(Exception):
    """Neo4j недоступен или векторный индекс не отвечает (SEARCH-004)."""


class AnswerLLMError(Exception):
    """Ollama chat-эндпоинт недоступен или вернул нечитаемый ответ (SEARCH-005)."""


# ─── get_driver ─────────────────────────────────────────────────────────
# Назначение: neo4j-драйвер по NEO4J_URI/USER/PASSWORD из окружения (.env
#   уже подхвачен импортом embeddings) — search подключается ТОЛЬКО на чтение.
# Входные связи: os.environ (NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD)
# Выходные данные: neo4j.Driver
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def get_driver() -> Driver:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("NEO4J_PASSWORD не задан ни в окружении, ни в .env")
    return GraphDatabase.driver(uri, auth=(user, password))


# ─── vector_search_chunks ───────────────────────────────────────────────
# Назначение: топ-K чанков по косинусной близости к вектору вопроса +
#   метаданные документа-родителя через (:Document)-[:HAS_CHUNK]->(:Chunk).
# Входные связи: neo4j.Driver (чтение), вектор вопроса (embed_texts), top_k
# Выходные данные: list[dict] — chunk_id, doc_id, text, score, title, year
#   (упорядочены по убыванию score); [] если индекс пуст
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def vector_search_chunks(driver: Driver, question_vec: list[float], top_k: int) -> list[dict]:
    query = (
        "CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node, score "
        "OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(node) "
        "RETURN node.chunk_id AS chunk_id, node.doc_id AS doc_id, node.text AS text, "
        "       score AS score, d.title AS title, d.year AS year "
        "ORDER BY score DESC"
    )
    try:
        with driver.session() as session:
            result = session.run(query, index=CHUNK_VECTOR_INDEX_NAME, k=top_k, vec=question_vec)
            return [dict(record) for record in result]
    except Exception as exc:  # noqa: BLE001 — любой сбой драйвера/индекса единообразно оборачиваем
        raise VectorSearchError(f"векторный поиск по индексу {CHUNK_VECTOR_INDEX_NAME} не выполнился: {exc}") from exc


# Назначение: обрезает текст чанка до QUOTE_MAX_CHARS символов (по контракту
#   Citation.quote), не разрывая слово — добавляет «…» при усечении.
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def _truncate_quote(text: str, limit: int = QUOTE_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


# ─── build_citations ─────────────────────────────────────────────────────
# Назначение: собирает contracts.Citation из найденных чанков; источник истины —
#   код (не доверяем LLM номера doc_id/chunk_id, только текст ответа).
# Входные связи: строки vector_search_chunks (chunk_id, doc_id, text, title, year)
# Выходные данные: list[contracts.Citation] в том же порядке (= порядок фрагментов в промпте)
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def build_citations(rows: list[dict]) -> list[Citation]:
    return [
        Citation(
            doc_id=row.get("doc_id") or "",
            chunk_id=row.get("chunk_id") or "",
            title=row.get("title") or "",
            year=row.get("year"),
            quote=_truncate_quote(row.get("text") or ""),
        )
        for row in rows
    ]


# Назначение: формирует нумерованный текстовый блок фрагментов для промпта
#   answer-LLM — номер фрагмента = порядок в rows (1-индексация), совпадает
#   с порядком citations в build_citations.
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def _build_context_block(rows: list[dict]) -> str:
    parts = []
    for i, row in enumerate(rows, start=1):
        title = row.get("title") or row.get("doc_id") or "?"
        year = row.get("year")
        year_str = f", {year}" if year else ""
        parts.append(f"[Фрагмент {i}] ({title}{year_str}):\n{(row.get('text') or '').strip()}")
    return "\n\n".join(parts)


# Назначение: вырезает блок(и) <think>...</think> из content ответа Ollama —
#   некоторые версии chat-эндпоинта кладут рассуждения прямо в content, а не
#   в отдельное поле message.reasoning.
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def _strip_think_tags(text: str) -> str:
    return _THINK_TAG_RE.sub("", text).strip()


# ─── call_answer_llm ──────────────────────────────────────────────────────
# Назначение: синтез ответа через Ollama `/v1/chat/completions` (ANSWER_MODEL);
#   промпт требует отвечать ТОЛЬКО по приведённым фрагментам со ссылками на их
#   номера и честно сказать «нет ответа», если фрагменты не содержат нужного.
# Входные связи: вопрос, нумерованный блок фрагментов (_build_context_block)
# Выходные данные: str — текст ответа (content без <think>, либо reasoning-фоллбек)
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def call_answer_llm(question: str, context_block: str, *, model: str | None = None, base_url: str | None = None,
                     logger=None) -> str:
    model = model or os.environ.get("ANSWER_MODEL", ANSWER_MODEL_DEFAULT)
    base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)).rstrip("/")

    system_prompt = (
        "Ты — ассистент карты знаний R&D горно-металлургической отрасли. "
        "Отвечай ТОЛЬКО на основе приведённых фрагментов источников, ничего не "
        "придумывай. При каждом утверждении указывай номер фрагмента в квадратных "
        "скобках, например [2]. Если в приведённых фрагментах нет ответа на вопрос — "
        "прямо напиши, что в предоставленных источниках ответа нет, и не пытайся "
        "угадать. Отвечай на русском языке."
    )
    user_prompt = f"Вопрос: {question}\n\nФрагменты источников:\n\n{context_block}"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": ANSWER_MAX_TOKENS,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _NO_PROXY_OPENER.open(request, timeout=ANSWER_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise AnswerLLMError(f"Ollama chat недоступна ({base_url}, model={model}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AnswerLLMError(f"битый JSON от Ollama chat: {exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise AnswerLLMError(f"пустой choices в ответе Ollama chat: тело={str(body)[:500]}")
    message = choices[0].get("message") or {}
    content = _strip_think_tags(message.get("content") or "")

    if content:
        return content

    # content пуст (весь бюджет ушёл на thinking) — reasoning не является финальным
    # ответом (черновые рассуждения), но фиксируем факт в лог как воспроизводимый
    # контекст сбоя; вызывающая сторона решает, как деградировать (SEARCH-005).
    reasoning = (message.get("reasoning") or "")[:500]
    if logger is not None:
        log_event(
            logger, stage="rag_demo", event="SEARCH-005", level="ERROR",
            detail=f"model={model} content пуст, reasoning_len={len(message.get('reasoning') or '')} "
                   f"reasoning_head={reasoning}",
        )
    raise AnswerLLMError("content пуст — answer-LLM не вернула финальный ответ (весь бюджет ушёл на thinking)")


# ─── answer_question ────────────────────────────────────────────────────
# Назначение: главный сквозной путь модуля — эмбеддинг вопроса, векторный
#   поиск чанков, синтез ответа, сборка contracts.Answer с цитатами из кода.
# Входные связи: вопрос (строка), top_k, наполненный Neo4j + Ollama
# Выходные данные: contracts.Answer (found=False + «в корпусе не найдено»,
#   если чанков не нашлось — инвариант №6)
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def answer_question(question: str, top_k: int = DEFAULT_TOP_K, run_id: str | None = None) -> Answer:
    run_id = run_id or new_run_id("rag_")
    logger = get_logger("search", run_id)
    log_event(logger, stage="rag_demo", event="query_received", detail=f"top_k={top_k} question={question[:200]}")

    try:
        question_vec = embed_texts([question])[0]
    except EmbeddingAPIError as exc:
        log_event(logger, stage="rag_demo", event="SEARCH-004", level="ERROR",
                   detail=f"эмбеддинг вопроса не посчитан: {str(exc)[:500]}")
        return Answer(question=question, text="в корпусе не найдено (эмбеддинг вопроса не посчитан)", found=False)

    driver = get_driver()
    try:
        try:
            rows = vector_search_chunks(driver, question_vec, top_k)
        except VectorSearchError as exc:
            log_event(logger, stage="rag_demo", event="SEARCH-004", level="ERROR",
                       detail=f"top_k={top_k} question={question[:200]} error={str(exc)[:500]}")
            return Answer(question=question, text="в корпусе не найдено (векторный поиск недоступен)", found=False)
    finally:
        driver.close()

    if not rows:
        log_event(logger, stage="rag_demo", event="no_chunks_found", level="WARNING", detail=f"question={question[:200]}")
        return Answer(question=question, text="в корпусе не найдено", found=False)

    citations = build_citations(rows)
    context_block = _build_context_block(rows)

    try:
        answer_text = call_answer_llm(question, context_block, logger=logger)
    except AnswerLLMError as exc:
        log_event(logger, stage="rag_demo", event="SEARCH-005", level="ERROR",
                   detail=f"question={question[:200]} error={str(exc)[:500]}")
        # Чанки найдены (found=True держим честным относительно инварианта №6 —
        # источники есть), но синтез не удался — сообщаем прямо, цитаты не теряем.
        answer_text = (
            "Не удалось получить синтезированный ответ от локальной LLM "
            f"({str(exc)[:200]}). Ниже — найденные по вопросу фрагменты источников (см. citations)."
        )

    log_event(logger, stage="rag_demo", event="answer_ready", detail=f"n_citations={len(citations)}")
    return Answer(question=question, text=answer_text, citations=citations, found=True)


# ─── main ────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа демо: `python -m ariadna.search.rag_demo "вопрос"`.
# Входные связи: sys.argv (--top-k, --json)
# Выходные данные: нет (печать в stdout — текст ответа + цитаты, либо JSON Answer)
# Уровень: ✅ реализовано (M-01, worklogs/search.md#2026-07-03)
def main() -> None:
    parser = argparse.ArgumentParser(description="Векторный RAG-демо: вопрос -> ответ с цитатами")
    parser.add_argument("question", help="Вопрос на русском (или английском)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--json", action="store_true", help="Печатать полный Answer в JSON")
    args = parser.parse_args()

    answer = answer_question(args.question, top_k=args.top_k)

    if args.json:
        print(answer.model_dump_json(indent=2))
        return

    print(f"Вопрос: {answer.question}\n")
    print(f"Ответ:\n{answer.text}\n")
    print(f"Найдено: {answer.found}, цитат: {len(answer.citations)}")
    for i, cit in enumerate(answer.citations, start=1):
        year_str = f", {cit.year}" if cit.year else ""
        print(f"  [{i}] {cit.title}{year_str} (doc_id={cit.doc_id}, chunk_id={cit.chunk_id})")
        print(f"      «{cit.quote}»")


if __name__ == "__main__":
    main()
