"""Синтез ответа: вопрос → роутер (A-10) → retrieval (граф+вектор, A-11) →
contracts.Answer с цитатами и пометкой contradicts (У-3).

Вход: вопрос пользователя (строка) + наполненный Neo4j + Ollama (ANSWER_MODEL,
.env). Выход: `contracts.Answer`.
Зависимости: `ariadna.search.router.route` / `ariadna.graph.templates.execute_intent`
    — инъекция извне (route_fn/execute_fn) с ленивым импортом по умолчанию: до
    приземления A-10 `ImportError` перехватывается и путь честно уходит в
    rag_fallback (не роняет ответ). Переиспользует `ariadna.search.rag_demo`
    (get_driver, call_answer_llm, build_citations, _build_context_block) и
    `ariadna.search.retrieval.retrieve` — не дублирует их логику.
Инварианты: инвариант №6 — пустой retrieval → found=False, «в корпусе не
    найдено», citations=[]; синтез: таймаут/пустой content → ретрай ×1 упрощённым
    промптом, затем экстрактивная деградация (SEARCH-007), found=True (citations
    не теряются); ANSWER_BACKEND из .env — «anthropic» временно заглушка (решение
    PM 03.07.2026 — Claude API отключён), по умолчанию «ollama».
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations

import argparse
import os
import time

from ariadna.contracts import Answer, Citation, Contradiction, QueryIntent
from ariadna.logutil import get_logger, log_event, new_run_id
from ariadna.search import rag_demo
from ariadna.search.retrieval import DEFAULT_TOP_K, retrieve

ANSWER_BACKEND_DEFAULT = "ollama"
# Экстрактивная деградация (SEARCH-007): сколько первых предложений топ-чанка
# брать в выжимку — достаточно для контекста, не превращает выжимку в копию чанка.
EXTRACT_SENTENCES_PER_CHUNK = 2


# Назначение: вопрос → QueryIntent через route_fn (инъекция для тестов или
#   ленивый импорт `router.route`); роутер ещё не существует/упал → честный
#   rag_fallback вместо падения всего ответа.
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _route(question: str, route_fn, logger) -> QueryIntent:
    if route_fn is None:
        try:
            from ariadna.search.router import route as route_fn  # noqa: PLC0415 — ленивый импорт зоны A-10
        except ImportError as exc:
            log_event(logger, stage="answer", event="SEARCH-001", level="INFO",
                       detail=f"router недоступен ({str(exc)[:200]}) — rag_fallback")
            return QueryIntent(question=question, template_id="rag_fallback")
    try:
        return route_fn(question)
    except Exception as exc:  # noqa: BLE001 — сбой роутера не должен ронять answer_question
        log_event(logger, stage="answer", event="SEARCH-001", level="ERROR",
                   detail=f"route_fn упал: {str(exc)[:500]} — rag_fallback")
        return QueryIntent(question=question, template_id="rag_fallback")


# Назначение: execute_fn (граф-шаблоны) — инъекция для тестов или ленивый импорт
#   `graph.templates.execute_intent`; недоступен → None (retrieve() сам уйдёт
#   в чисто векторную ветку и залогирует SEARCH-001).
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _resolve_execute_fn(execute_fn):
    if execute_fn is not None:
        return execute_fn
    try:
        from ariadna.graph.templates import execute_intent  # noqa: PLC0415 — ленивый импорт зоны A-10
        return execute_intent
    except ImportError:
        return None


# Назначение: экстрактивная выжимка вместо LLM-синтеза (SEARCH-007) — первые
#   предложения топ-чанков контекста; честно помечена как нередактированная
#   выборка, а не выдаётся за LLM-ответ.
# Входные связи: chunks — тот же формат, что и retrieve()["chunks"]
# Выходные данные: str — текст ответа
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _extractive_fallback(chunks: list[dict]) -> str:
    lines = ["Синтез локальной LLM недоступен — экстрактивная выжимка по найденным фрагментам:"]
    for i, chunk in enumerate(chunks, start=1):
        text = (chunk.get("text") or "").strip().replace("\n", " ")
        sentences = text.split(". ")
        excerpt = ". ".join(s for s in sentences[:EXTRACT_SENTENCES_PER_CHUNK] if s).strip()
        if excerpt and not excerpt.endswith((".", "…", "!", "?")):
            excerpt += "…"
        lines.append(f"[{i}] {excerpt or '(пустой фрагмент)'}")
    return "\n".join(lines)


# Назначение: синтез ответа локальной LLM (переиспользует rag_demo.call_answer_llm)
#   с ретраем ×1 упрощённым промптом при таймауте/пустом content, затем
#   экстрактивная деградация (SEARCH-007); ANSWER_BACKEND=anthropic — временная
#   заглушка (Claude API отключён решением PM 03.07.2026).
# Входные связи: вопрос, chunks (контекст, формат retrieve()["chunks"]), logger
# Выходные данные: str — текст ответа (LLM-синтез либо экстрактивная выжимка)
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _synthesize(question: str, chunks: list[dict], logger) -> str:
    backend = os.environ.get("ANSWER_BACKEND", ANSWER_BACKEND_DEFAULT).strip().lower()
    if backend == "anthropic":
        log_event(logger, stage="answer", event="SEARCH-007", level="WARNING",
                   detail="ANSWER_BACKEND=anthropic — Claude API отключён решением PM 03.07.2026, "
                          "экстрактивная деградация вместо синтеза")
        return _extractive_fallback(chunks)

    context_block = rag_demo._build_context_block(chunks)
    try:
        return rag_demo.call_answer_llm(question, context_block, logger=logger)
    except rag_demo.AnswerLLMError as exc:
        log_event(logger, stage="answer", event="SEARCH-005", level="WARNING",
                   detail=f"первая попытка синтеза упала: {str(exc)[:300]} — ретрай упрощённым промптом")

    simplified_question = f"Кратко и по фактам, без рассуждений: {question}"
    try:
        return rag_demo.call_answer_llm(simplified_question, context_block, logger=logger)
    except rag_demo.AnswerLLMError as exc:
        log_event(logger, stage="answer", event="SEARCH-007", level="ERROR",
                   detail=f"ретрай синтеза тоже упал: {str(exc)[:300]} — экстрактивная деградация")
        return _extractive_fallback(chunks)


# Назначение: contradiction_pairs (retrieve(), обогащён title/year/quote) →
#   contracts.Contradiction — claim_a/claim_b из имён узлов, citation из
#   провенанса связи CONTRADICTS (doc_id/chunk_id самой связи, У-3).
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _pair_to_contradiction(pair: dict) -> Contradiction:
    citation = Citation(
        doc_id=pair.get("doc_id") or "",
        chunk_id=pair.get("chunk_id") or "",
        title=pair.get("title") or "",
        year=pair.get("year"),
        quote=pair.get("quote") or "",
    )
    return Contradiction(
        claim_a=pair.get("name_a") or pair.get("node_a_id") or "?",
        claim_b=pair.get("name_b") or pair.get("node_b_id") or "?",
        citations=[citation] if (citation.doc_id or citation.chunk_id) else [],
    )


# ─── answer_question ────────────────────────────────────────────────────
# Назначение: главный сквозной путь A-11 — вопрос → роутер → retrieval
#   (граф+вектор) → синтез → contracts.Answer с цитатами, contradictions (У-3)
#   и subgraph_node_ids (для UI A-13).
# Входные связи: вопрос, route_fn|execute_fn (инъекция для тестов/до A-10),
#   top_k, наполненный Neo4j + Ollama (ANSWER_BACKEND/ANSWER_MODEL из .env)
# Выходные данные: contracts.Answer (found=False + «в корпусе не найдено» +
#   citations=[], если retrieval пуст — инвариант №6)
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def answer_question(
    question: str,
    *,
    route_fn=None,
    execute_fn=None,
    top_k: int = DEFAULT_TOP_K,
    run_id: str | None = None,
) -> Answer:
    run_id = run_id or new_run_id("answer_")
    logger = get_logger("search", run_id)
    log_event(logger, stage="answer", event="query_received", detail=f"top_k={top_k} question={question[:200]}")

    t0 = time.monotonic()
    intent = _route(question, route_fn, logger)
    t1 = time.monotonic()
    log_event(logger, stage="answer", event="route_done",
               detail=f"template_id={intent.template_id} elapsed_sec={t1 - t0:.2f}")

    resolved_execute_fn = _resolve_execute_fn(execute_fn)

    driver = rag_demo.get_driver()
    try:
        result = retrieve(driver, intent, question, execute_fn=resolved_execute_fn, top_k=top_k, logger=logger)
    finally:
        driver.close()
    t2 = time.monotonic()
    log_event(logger, stage="answer", event="retrieve_done",
               detail=f"n_chunks={len(result['chunks'])} n_nodes={len(result['node_ids'])} "
                      f"n_contradictions={len(result['contradiction_pairs'])} elapsed_sec={t2 - t1:.2f}")

    if not result["chunks"]:
        log_event(logger, stage="answer", event="no_chunks_found", level="WARNING", detail=f"question={question[:200]}")
        return Answer(question=question, text="в корпусе не найдено", found=False,
                       subgraph_node_ids=result.get("node_ids", []))

    answer_text = _synthesize(question, result["chunks"], logger)
    t3 = time.monotonic()
    log_event(logger, stage="answer", event="synthesis_done", detail=f"elapsed_sec={t3 - t2:.2f}")

    citations = rag_demo.build_citations(result["chunks"])
    contradictions = [_pair_to_contradiction(p) for p in result.get("contradiction_pairs", [])]

    log_event(logger, stage="answer", event="answer_ready",
               detail=f"n_citations={len(citations)} n_contradictions={len(contradictions)} "
                      f"total_elapsed_sec={t3 - t0:.2f}")
    return Answer(
        question=question,
        text=answer_text,
        citations=citations,
        contradictions=contradictions,
        subgraph_node_ids=result.get("node_ids", []),
        found=True,
    )


# ─── main ────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа: `python -m ariadna.search.answer "вопрос"` —
#   печатает Answer в JSON в stdout, тайминги этапов уже в stderr через логгер
#   (route_done/retrieve_done/synthesis_done в logs/pipeline/<run_id>.jsonl).
# Входные связи: sys.argv (вопрос, --top-k)
# Выходные данные: нет (печать в stdout)
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def main() -> None:
    parser = argparse.ArgumentParser(description="Гибридный ответ (граф+вектор) с цитатами и contradicts")
    parser.add_argument("question", help="Вопрос на русском (или английском)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    answer = answer_question(args.question, top_k=args.top_k)
    print(answer.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
