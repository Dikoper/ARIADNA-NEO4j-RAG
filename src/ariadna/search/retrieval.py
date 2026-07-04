"""Гибридный retrieval: граф (execute_intent, зона A-10) + вектор (rag_demo/
embeddings, M-01/A-04) → единый пул чанков-свидетельств для синтеза ответа (A-11).

Вход: neo4j.Driver (только чтение), contracts.QueryIntent (роутер A-10), вопрос
(строка). Выход: plain dict — промежуточный формат между router/graph и answer.py,
НЕ модель contracts (в отличие от Answer/QueryIntent, не пересекает границу модуля):
    {"chunks": [{chunk_id, doc_id, text, title, year}, ...],
     "rows": list[dict] (сырые строки графового шаблона, для будущих нужд analytics/UI),
     "node_ids": list[str] (узлы подграфа — A-13),
     "contradiction_pairs": list[dict] (node_a_id, node_b_id, name_a, name_b, doc_id,
         chunk_id + добавленные title/year/quote чанка-провенанса)}.
Зависимости: ariadna.search.rag_demo (vector_search_chunks, VectorSearchError,
    _truncate_quote, DEFAULT_TOP_K) и ariadna.search.embeddings (embed_texts,
    EmbeddingAPIError) — переиспользованы, не продублированы; graph.templates.
    execute_intent — НЕ импортируется на уровне модуля (зона A-10, может ещё не
    существовать), инъекция через параметр execute_fn.
Инварианты: никогда не пишет в Neo4j; template_id='rag_fallback', execute_fn=None
    или execute_fn пуст/упал → чисто векторная ветка (лог SEARCH-001), как в M-01;
    ГРАФ-ЧАНКИ РАНЖИРУЮТСЯ по косинусной близости к вопросу перед слиянием —
    переиспользуется question_vec, уже посчитанный для векторной ветки (не второй
    эмбеддинг). Без ранжирования сотни граф-кандидатов в исходном (лексическом,
    не по релевантности) порядке Cypher-шаблона забивают весь контекст раньше,
    чем вектор вообще попадает в промпт синтеза — критический баг, найденный
    интеграционным смоуком оркестратора (все 4 эталонных запроса жюри давали
    «в предоставленных источниках нет ответа», см. worklogs/search.md). Квоты
    слияния: граф — до GRAPH_CONTEXT_QUOTA мест (отранжированных), вектор —
    минимум VECTOR_CONTEXT_MIN мест, дедуп по chunk_id (чанк, найденный и
    графом, и вектором, считается графовым), добор из любого источника до
    MAX_CONTEXT_CHUNKS.
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations

import math

from neo4j import Driver

from ariadna.contracts import QueryIntent
from ariadna.logutil import log_event
from ariadna.search.embeddings import EmbeddingAPIError, embed_texts
from ariadna.search.rag_demo import (
    DEFAULT_TOP_K,
    VectorSearchError,
    _truncate_quote,
    vector_search_chunks,
)

# Верхняя граница размера контекста синтеза (граф+вектор вместе) — не даём
# промпту разрастись до неконтролируемого размера при широком графовом ответе
# (reasoning-модель и так тратит бюджет на thinking, см. worklogs/search.md#M-01).
MAX_CONTEXT_CHUNKS = 12

# Квоты слияния графа/вектора (см. докстринг модуля — фикс критического бага
# «граф забивает контекст нерелевантным порядком»): граф получает ДО этого
# числа мест ПОСЛЕ ранжирования по векторной близости к вопросу — не
# безусловный приоритет исходного порядка Cypher-шаблона. Вектору гарантирован
# остаток мест — VECTOR_CONTEXT_MIN.
GRAPH_CONTEXT_QUOTA = 7
VECTOR_CONTEXT_MIN = MAX_CONTEXT_CHUNKS - GRAPH_CONTEXT_QUOTA  # 5


# ─── fetch_chunks_by_ids ────────────────────────────────────────────────
# Назначение: тексты + метаданные документа (title, year) + вектор embedding
#   для чанков, отданных графовым шаблоном (execute_intent несёт только
#   chunk_id, без текста и без вектора) — embedding нужен для ранжирования
#   граф-кандидатов по близости к вопросу (см. _rank_graph_chunks_by_relevance),
#   а также для чанков-провенансов contradiction_pairs.
# Входные связи: neo4j.Driver (чтение), список chunk_id
# Выходные данные: dict[chunk_id -> {chunk_id, doc_id, text, title, year,
#   embedding}]; чанков, которых нет в базе (устаревший id), в словаре не
#   будет — вызывающий код пропускает недостающие, не падает
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04; embedding — фикс A-10/A-11)
def fetch_chunks_by_ids(driver: Driver, chunk_ids: list[str]) -> dict[str, dict]:
    if not chunk_ids:
        return {}
    query = (
        "MATCH (c:Chunk) WHERE c.chunk_id IN $ids "
        "OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(c) "
        "RETURN c.chunk_id AS chunk_id, c.doc_id AS doc_id, c.text AS text, "
        "       c.embedding AS embedding, d.title AS title, d.year AS year"
    )
    try:
        with driver.session() as session:
            result = session.run(query, ids=chunk_ids)
            return {row["chunk_id"]: dict(row) for row in result}
    except Exception as exc:  # noqa: BLE001 — любой сбой драйвера единообразно оборачиваем
        raise VectorSearchError(f"чтение чанков по id не выполнилось: {exc}") from exc


# Назначение: дополняет contradiction_pairs метаданными чанка-провенанса
#   (title/year/quote) — уже известные чанки (из vector_by_id/graph_meta) не
#   перезапрашиваются, недостающие читаются одним batch-запросом.
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04)
def _enrich_contradictions(
    driver: Driver,
    pairs: list[dict],
    vector_by_id: dict[str, dict],
    graph_meta: dict[str, dict],
    logger,
) -> list[dict]:
    if not pairs:
        return []
    known = {**vector_by_id, **graph_meta}
    missing = [p["chunk_id"] for p in pairs if p.get("chunk_id") and p["chunk_id"] not in known]
    if missing:
        try:
            known = {**known, **fetch_chunks_by_ids(driver, missing)}
        except VectorSearchError as exc:
            if logger is not None:
                log_event(logger, stage="retrieval", event="SEARCH-004", level="ERROR",
                           detail=f"чтение чанков contradiction_pairs не выполнилось: {str(exc)[:500]}")
    enriched = []
    for pair in pairs:
        row = known.get(pair.get("chunk_id"), {})
        enriched.append({
            **pair,
            "title": row.get("title") or "",
            "year": row.get("year"),
            "quote": _truncate_quote(row.get("text") or "") if row.get("text") else "",
        })
    return enriched


# Назначение: косинусная близость двух векторов на чистом Python (numpy не в
#   зависимостях модуля, см. pyproject.toml) — несовместимые размерности или
#   пустой/нулевой вектор дают -1.0 (заведомо худший скор: кандидат уходит в
#   конец ранжирования, а не роняет retrieve()).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md)
def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return -1.0
    return dot / (norm_a * norm_b)


# ─── _rank_graph_chunks_by_relevance ─────────────────────────────────────
# Назначение: ранжирует граф-кандидатов (chunk_id из execute_fn — исходный
#   порядок Cypher лексический/по сборке множества, НЕ по релевантности,
#   может достигать сотен id) по косинусной близости к вопросу. Чанк, уже
#   найденный векторным поиском, берёт готовый score оттуда (тот же индекс и
#   метрика cosine, graph.config.VECTOR_SIMILARITY_FUNCTION — сопоставимые
#   величины); остальным эмбеддинг вычитывается батчем из Neo4j
#   (fetch_chunks_by_ids) и косинус считается в Python — кандидатов на
#   практике ≤ ~900 (наблюдалось Q1=891, Q4=699), это дёшево.
# Входные связи: neo4j.Driver, chunk_id графа, question_vec (переиспользован
#   из векторной ветки), vector_by_id (уже найденные вектором строки с score)
# Выходные данные: (ranked_ids — отсортированы по убыванию score, graph_meta —
#   dict chunk_id -> строка с текстом/метаданными для чанков вне vector_by_id)
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md)
def _rank_graph_chunks_by_relevance(
    driver: Driver,
    graph_chunk_ids: list[str],
    question_vec: list[float],
    vector_by_id: dict[str, dict],
    logger,
) -> tuple[list[str], dict[str, dict]]:
    missing_ids = [cid for cid in graph_chunk_ids if cid not in vector_by_id]
    graph_meta: dict[str, dict] = {}
    if missing_ids:
        try:
            graph_meta = fetch_chunks_by_ids(driver, missing_ids)
        except VectorSearchError as exc:
            if logger is not None:
                log_event(logger, stage="retrieval", event="SEARCH-004", level="ERROR",
                           detail=f"чтение граф-чанков для ранжирования не выполнилось: {str(exc)[:500]}")

    scored: list[tuple[float, str]] = []
    for cid in graph_chunk_ids:
        if cid in vector_by_id:
            score = vector_by_id[cid].get("score")
            scored.append((float(score) if score is not None else -1.0, cid))
        else:
            row = graph_meta.get(cid)
            scored.append((_cosine(question_vec, row.get("embedding") if row else None), cid))

    scored.sort(key=lambda pair: pair[0], reverse=True)  # sort стабилен — равный score сохраняет исходный порядок
    return [cid for _, cid in scored], graph_meta


# ─── _merge_with_quota ────────────────────────────────────────────────────
# Назначение: объединяет отранжированных граф-кандидатов и векторный топ-K в
#   итоговый пул с квотами (GRAPH_CONTEXT_QUOTA/VECTOR_CONTEXT_MIN): сначала
#   граф — первые GRAPH_CONTEXT_QUOTA уникальных id по убыванию релевантности;
#   затем вектор — все ещё не отобранные id из vector_ids (до max_total, это
#   гарантирует вектору минимум VECTOR_CONTEXT_MIN мест, т.к. граф не превышает
#   свою квоту); затем добор оставшихся мест ЛЮБЫМ источником — лишние
#   граф-кандидаты сверх квоты, если вектору не хватило кандидатов для
#   заполнения до max_total. Дедуп по chunk_id — id, отобранный на графовом
#   шаге, повторно вектором не берётся (граф-чанк, найденный и вектором тоже,
#   считается графовым).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md)
def _merge_with_quota(
    ranked_graph_ids: list[str],
    vector_ids: list[str],
    *,
    graph_quota: int = GRAPH_CONTEXT_QUOTA,
    max_total: int = MAX_CONTEXT_CHUNKS,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    # Назначение: дописывает в selected уникальные id из pool, пока не достигнут limit.
    # Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md)
    def _extend(pool: list[str], limit: int) -> None:
        for cid in pool:
            if len(selected) >= limit:
                return
            if cid not in seen:
                selected.append(cid)
                seen.add(cid)

    _extend(ranked_graph_ids, graph_quota)  # граф: до GRAPH_CONTEXT_QUOTA мест, по релевантности
    _extend(vector_ids, max_total)          # вектор: минимум VECTOR_CONTEXT_MIN (остаток квоты)
    _extend(ranked_graph_ids, max_total)    # добор графом, если вектору не хватило кандидатов
    return selected


# ─── retrieve ───────────────────────────────────────────────────────────
# Назначение: главная точка входа модуля — граф-свидетельства (execute_fn)
#   ранжируются по векторной близости к вопросу (_rank_graph_chunks_by_relevance),
#   векторный топ-K ищется отдельно; оба пула сливаются по квотам
#   (_merge_with_quota: граф ≤ GRAPH_CONTEXT_QUOTA, вектор ≥ VECTOR_CONTEXT_MIN,
#   дедуп, добор до MAX_CONTEXT_CHUNKS) в единый пул чанков-свидетельств для
#   синтеза.
# Входные связи: neo4j.Driver, QueryIntent (роутер), вопрос, execute_fn (инъекция;
#   по умолчанию None — вызывающая сторона отвечает за ленивый импорт зоны A-10)
# Выходные данные: dict — chunks (list[dict], ≤ MAX_CONTEXT_CHUNKS), rows (сырые
#   строки графового шаблона), node_ids (list[str], для UI-подграфа A-13),
#   contradiction_pairs (list[dict], дополнены title/year/quote)
# Уровень: ✅ реализовано (A-11, worklogs/search.md#2026-07-04; ранжирование+квоты — фикс module-dev)
def retrieve(
    driver: Driver,
    intent: QueryIntent,
    question: str,
    *,
    execute_fn=None,
    top_k: int = DEFAULT_TOP_K,
    logger=None,
) -> dict:
    graph_result: dict = {"rows": [], "node_ids": [], "chunk_ids": [], "contradiction_pairs": []}
    use_graph = execute_fn is not None and intent.template_id != "rag_fallback"

    if use_graph:
        try:
            raw = execute_fn(driver, intent) or {}
            graph_result.update(raw)
        except Exception as exc:  # noqa: BLE001 — сбой шаблона не должен ронять весь ответ
            if logger is not None:
                log_event(logger, stage="retrieval", event="SEARCH-006", level="ERROR",
                           detail=f"template_id={intent.template_id} execute_fn упал: {str(exc)[:500]}")
            graph_result = {"rows": [], "node_ids": [], "chunk_ids": [], "contradiction_pairs": []}
            use_graph = False

    graph_has_evidence = bool(graph_result.get("rows") or graph_result.get("chunk_ids"))
    if not use_graph or not graph_has_evidence:
        if logger is not None:
            log_event(logger, stage="retrieval", event="SEARCH-001", level="INFO",
                       detail=f"template_id={intent.template_id} use_graph={use_graph} "
                              f"graph_has_evidence={graph_has_evidence} — векторная ветка (rag_fallback)")

    question_vec: list[float] | None = None
    try:
        question_vec = embed_texts([question])[0]
    except EmbeddingAPIError as exc:
        if logger is not None:
            log_event(logger, stage="retrieval", event="SEARCH-004", level="ERROR",
                       detail=f"эмбеддинг вопроса не посчитан (ранжирование графа деградирует "
                              f"к исходному порядку шаблона): {str(exc)[:500]}")

    vector_rows: list[dict] = []
    if question_vec is not None:
        try:
            vector_rows = vector_search_chunks(driver, question_vec, top_k)
        except VectorSearchError as exc:
            if logger is not None:
                log_event(logger, stage="retrieval", event="SEARCH-004", level="ERROR",
                           detail=f"векторный поиск не выполнился: {str(exc)[:500]}")

    vector_by_id = {row["chunk_id"]: row for row in vector_rows if row.get("chunk_id")}
    graph_chunk_ids = [cid for cid in graph_result.get("chunk_ids", []) or [] if cid]

    ranked_graph_ids: list[str] = []
    graph_meta: dict[str, dict] = {}
    if graph_chunk_ids:
        if question_vec is not None:
            ranked_graph_ids, graph_meta = _rank_graph_chunks_by_relevance(
                driver, graph_chunk_ids, question_vec, vector_by_id, logger,
            )
        else:
            # Эмбеддинг вопроса недоступен (SEARCH-004 уже залогирован выше) —
            # ранжировать нечем: граф-кандидаты в исходном порядке шаблона
            # (деградация, не падение всего retrieve()).
            ranked_graph_ids = graph_chunk_ids
            try:
                graph_meta = fetch_chunks_by_ids(
                    driver, [cid for cid in graph_chunk_ids if cid not in vector_by_id],
                )
            except VectorSearchError as exc:
                if logger is not None:
                    log_event(logger, stage="retrieval", event="SEARCH-004", level="ERROR",
                               detail=f"чтение граф-чанков не выполнилось: {str(exc)[:500]}")

    vector_ids_ordered = [row["chunk_id"] for row in vector_rows if row.get("chunk_id")]
    selected_ids = _merge_with_quota(ranked_graph_ids, vector_ids_ordered)

    chunks: list[dict] = []
    for cid in selected_ids:
        row = vector_by_id.get(cid) or graph_meta.get(cid)
        if row is None:
            continue
        chunks.append({
            "chunk_id": cid,
            "doc_id": row.get("doc_id") or "",
            "text": row.get("text") or "",
            "title": row.get("title") or "",
            "year": row.get("year"),
        })

    contradiction_pairs = _enrich_contradictions(
        driver, graph_result.get("contradiction_pairs", []) or [], vector_by_id, graph_meta, logger,
    )

    return {
        "chunks": chunks,
        "rows": graph_result.get("rows", []) or [],
        "node_ids": graph_result.get("node_ids", []) or [],
        "contradiction_pairs": contradiction_pairs,
    }
