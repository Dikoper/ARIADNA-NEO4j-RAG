"""Блок «Рекомендации» (У-1, A-14): готовый `contracts.Answer` -> `list[Recommendation]`
без LLM — только вектор (похожие кейсы) и Cypher (эксперты, смежные темы),
целевое время < 5 с.

Вход: наполненный Neo4j (только чтение, инвариант №2) + vector-индекс
`chunk_embedding_idx` (A-05) + вопрос (строка) + уже построенный `contracts.Answer`
(citations/subgraph_node_ids — A-11). Выход: `list[contracts.Recommendation]` —
до `top_k` КАЖДОГО вида (`RecommendationKind`), в порядке similar_case → expert →
adjacent_topic (сортировка списка целиком, не только внутри вида).
Зависимости: `search.embeddings.embed_texts` (эмбеддинг вопроса — обход системного
    прокси к localhost уже реализован там), `search.rag_demo.vector_search_chunks`/
    `_truncate_quote` (переиспользованы, не продублированы — тот же индекс/метрика,
    что и синтез ответа), `search.retrieval.fetch_chunks_by_ids` (метаданные
    чанков-подтверждений эксперта), `graph.recommendation_queries` (тексты Cypher —
    RECOMMENDATION_EXPERT_QUERY, RECOMMENDATION_ADJACENT_QUERY; вынесены из
    `graph.cypher_templates` в отдельный файл — тот уже у потолка в 350 строк
    после A-12, см. docstring recommendation_queries.py).
Инварианты: analytics ТОЛЬКО читает Neo4j; `driver=None` -> `[]` (рекомендации —
    доп. блок к уже готовому ответу, не самостоятельный CLI-путь, в отличие от
    `gap_map.build_gap_report` — driver открывать/закрывать здесь не нужно);
    сбой одной ветки (вектор/Cypher) не роняет остальные — партиальная деградация,
    как в `search.retrieval.retrieve`.
Паспорт: docs/dev/modules/analytics.md.
"""
from __future__ import annotations

from neo4j import Driver

from ariadna.contracts import Answer, Citation, Recommendation, RecommendationKind
from ariadna.graph.recommendation_queries import RECOMMENDATION_ADJACENT_QUERY, RECOMMENDATION_EXPERT_QUERY
from ariadna.logutil import get_logger, log_event, new_run_id
from ariadna.search.embeddings import EmbeddingAPIError, embed_texts
from ariadna.search.rag_demo import VectorSearchError, _truncate_quote, vector_search_chunks
from ariadna.search.retrieval import fetch_chunks_by_ids

# Эмбеддинг вопроса не посчитан либо векторный поиск похожих кейсов не выполнился —
# ветка similar_case пуста, expert/adjacent_topic строятся независимо (не роняем
# весь build_recommendations из-за одной ветки, тот же принцип, что SEARCH-004/006).
ANALYTICS_RECOMMENDATION_VECTOR_FAILED = "ANALYTICS-003"

# Cypher-ветка (expert/adjacent_topic) упала (Neo4j недоступен/сеть) — партиальная
# деградация: ветка пуста, остальные виды рекомендаций не затронуты.
ANALYTICS_RECOMMENDATION_QUERY_FAILED = "ANALYTICS-004"

# Буфер векторного пула similar_case: top_k РАЗЛИЧНЫХ документов нужно получить
# из top-K ЧАНКОВ (несколько чанков на документ + исключение уже процитированных
# doc_id ответа) — берём заметно больше top_k, не только его.
SIMILAR_CASE_POOL_MIN = 40
SIMILAR_CASE_POOL_MULTIPLIER = 10

# Буфер Cypher-запроса adjacent_topic (RECOMMENDATION_ADJACENT_QUERY — relation-hop
# и co-mention объединены UNION) до финальной сортировки/дедупа/среза в Python
# (предпочтение типа Material/Process/Property + n_mentions).
ADJACENT_QUERY_LIMIT_MULTIPLIER = 4

# Предпочтительные типы сущностей для adjacent_topic (паспорт модуля analytics:
# «предпочитай типы Material/Process/Property») — тир 0 в сортировке, прочие — тир 1.
_ADJACENT_PREFERRED_TYPES = {"Material", "Process", "Property"}

# До скольких чанков-подтверждений прикладывать к рекомендации эксперта (паспорт
# модуля: «citations = до 2 чанков-подтверждений»).
EXPERT_MAX_CITATIONS = 2


# Назначение: похожие кейсы — векторная близость вопроса к чанкам, агрегированная
#   до документа (первый по score чанк документа — представитель), исключая
#   doc_id, уже процитированные в ответе; reason — шаблонная фраза со скором
#   (без LLM, паспорт модуля). Сбой эмбеддинга/векторного поиска -> [] (лог
#   ANALYTICS-003), не роняет expert/adjacent_topic.
# Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
def _build_similar_case(
    driver: Driver, question: str, cited_doc_ids: set[str], top_k: int, logger,
) -> list[Recommendation]:
    if top_k <= 0:
        return []
    try:
        question_vec = embed_texts([question])[0]
    except EmbeddingAPIError as exc:
        log_event(logger, stage="recommendations", event=ANALYTICS_RECOMMENDATION_VECTOR_FAILED, level="ERROR",
                   detail=f"эмбеддинг вопроса не посчитан: {str(exc)[:500]}")
        return []

    pool_k = max(SIMILAR_CASE_POOL_MIN, top_k * SIMILAR_CASE_POOL_MULTIPLIER)
    try:
        rows = vector_search_chunks(driver, question_vec, pool_k)
    except VectorSearchError as exc:
        log_event(logger, stage="recommendations", event=ANALYTICS_RECOMMENDATION_VECTOR_FAILED, level="ERROR",
                   detail=f"векторный поиск похожих кейсов не выполнился: {str(exc)[:500]}")
        return []

    recs: list[Recommendation] = []
    seen_docs: set[str] = set()
    for row in rows:
        doc_id = row.get("doc_id") or ""
        if not doc_id or doc_id in cited_doc_ids or doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)
        score = row.get("score")
        title = row.get("title") or doc_id
        reason = (f"близкий по содержанию документ (сходство {score:.2f})" if score is not None
                  else "близкий по содержанию документ")
        citation = Citation(
            doc_id=doc_id, chunk_id=row.get("chunk_id") or "", title=title,
            year=row.get("year"), quote=_truncate_quote(row.get("text") or ""),
        )
        recs.append(Recommendation(kind=RecommendationKind.SIMILAR_CASE, title=title, reason=reason,
                                    citations=[citation]))
        if len(recs) >= top_k:
            break
    return recs


# Назначение: эксперты — узлы `:Expert`, найденные RECOMMENDATION_EXPERT_QUERY
#   (со-упоминание с цитатами ответа либо связь с сущностями подграфа), уже
#   отранжированные и обрезанные Cypher-шаблоном до top_k; citations — до
#   EXPERT_MAX_CITATIONS подтверждающих чанков через fetch_chunks_by_ids
#   (переиспользован из search/retrieval, не дублируем чтение чанков по id).
# Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
def _build_expert(
    driver: Driver, cited_chunk_ids: list[str], cited_doc_ids: list[str], node_ids: list[str],
    top_k: int, logger,
) -> list[Recommendation]:
    if top_k <= 0 or not (cited_chunk_ids or cited_doc_ids or node_ids):
        return []
    try:
        with driver.session() as session:
            rows = [dict(r) for r in session.run(
                RECOMMENDATION_EXPERT_QUERY,
                chunk_ids=cited_chunk_ids, doc_ids=cited_doc_ids, node_ids=node_ids, limit=top_k,
            )]
    except Exception as exc:  # noqa: BLE001 — любой сбой драйвера единообразно оборачиваем
        log_event(logger, stage="recommendations", event=ANALYTICS_RECOMMENDATION_QUERY_FAILED, level="ERROR",
                   detail=f"поиск экспертов-кандидатов не выполнился: {str(exc)[:500]}")
        return []
    if not rows:
        return []

    sample_chunk_ids = sorted({cid for row in rows for cid in (row.get("sample_chunk_ids") or [])})
    try:
        chunk_meta = fetch_chunks_by_ids(driver, sample_chunk_ids) if sample_chunk_ids else {}
    except VectorSearchError as exc:
        log_event(logger, stage="recommendations", event=ANALYTICS_RECOMMENDATION_QUERY_FAILED, level="ERROR",
                   detail=f"метаданные чанков-подтверждений эксперта не прочитаны: {str(exc)[:500]}")
        chunk_meta = {}

    recs: list[Recommendation] = []
    for row in rows:
        n_sources = row.get("n_matched_sources") or 0
        reason = (f"упоминается в {n_sources} источниках по теме" if n_sources
                  else "связан с сущностями темы вопроса")
        citations: list[Citation] = []
        for cid in (row.get("sample_chunk_ids") or [])[:EXPERT_MAX_CITATIONS]:
            meta = chunk_meta.get(cid)
            if not meta:
                continue
            citations.append(Citation(
                doc_id=meta.get("doc_id") or "", chunk_id=cid, title=meta.get("title") or "",
                year=meta.get("year"), quote=_truncate_quote(meta.get("text") or ""),
            ))
        recs.append(Recommendation(kind=RecommendationKind.EXPERT, title=row["name"], reason=reason,
                                    citations=citations))
    return recs


# Назначение: дедуп кандидатов adjacent_topic по id — сосед, найденный через
#   разные $node_ids/пути UNION, встречается несколько раз (с разным via) —
#   оставляем строку с наибольшим n_mentions (у одного соседа n_mentions
#   одинаков во всех строках, дедуп просто сохраняет первую попавшуюся via-связь).
# Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
def _dedupe_adjacent_candidates(rows: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for row in rows:
        nid = row.get("id")
        if not nid:
            continue
        existing = best.get(nid)
        if existing is None or (row.get("n_mentions") or 0) > (existing.get("n_mentions") or 0):
            best[nid] = row
    return list(best.values())


# Назначение: смежные темы — соседи узлов подграфа ответа (1 хоп по связям
#   онтологии ИЛИ co-mention через общий чанк, RECOMMENDATION_ADJACENT_QUERY —
#   оба пути объединены UNION), не входящие в сам subgraph_node_ids;
#   предпочтение типам Material/Process/Property (тир 0), затем по n_mentions
#   DESC (паспорт модуля analytics). Сбой Cypher -> [] (лог ANALYTICS-004),
#   не роняет similar_case/expert.
# Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
def _build_adjacent_topic(driver: Driver, node_ids: list[str], top_k: int, logger) -> list[Recommendation]:
    if top_k <= 0 or not node_ids:
        return []
    query_limit = top_k * ADJACENT_QUERY_LIMIT_MULTIPLIER
    try:
        with driver.session() as session:
            rows = [dict(r) for r in session.run(
                RECOMMENDATION_ADJACENT_QUERY, node_ids=node_ids, limit=query_limit,
            )]
    except Exception as exc:  # noqa: BLE001 — любой сбой драйвера единообразно оборачиваем
        log_event(logger, stage="recommendations", event=ANALYTICS_RECOMMENDATION_QUERY_FAILED, level="ERROR",
                   detail=f"поиск смежных тем не выполнился: {str(exc)[:500]}")
        return []

    candidates = _dedupe_adjacent_candidates(rows)

    # Назначение: ключ сортировки кандидата — Material/Process/Property первыми
    #   (тир 0), затем n_mentions DESC (паспорт модуля analytics).
    # Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
    def _sort_key(row: dict):
        tier = 0 if row.get("type") in _ADJACENT_PREFERRED_TYPES else 1
        return (tier, -(row.get("n_mentions") or 0))

    ordered = sorted(candidates, key=_sort_key)[:top_k]
    return [
        Recommendation(
            kind=RecommendationKind.ADJACENT_TOPIC,
            title=row["name"],
            reason=f"связана через «{row.get('via_name') or row.get('via_id')}»",
            citations=[],
        )
        for row in ordered
    ]


# ─── build_recommendations ───────────────────────────────────────────────────
# Назначение: публичный вход модуля (У-1, сигнатура ФИКСИРОВАНА оркестратором —
#   UI-агент вызывает параллельно) — до `top_k` рекомендаций КАЖДОГО вида,
#   список отсортирован: сначала similar_case, потом expert, потом
#   adjacent_topic. `driver=None` -> `[]` (рекомендации — необязательный
#   доп. блок к уже готовому ответу, деградация тем же принципом, что UI-001/
#   UI-002 у вызывающей стороны).
# Входные связи: neo4j.Driver | None, вопрос, contracts.Answer (citations —
#   исключение doc_id из similar_case; subgraph_node_ids — вход expert/
#   adjacent_topic), top_k (на вид рекомендации, не суммарно)
# Выходные данные: list[contracts.Recommendation]
# Уровень: ✅ реализовано (A-14, worklogs/analytics.md)
def build_recommendations(
    driver: Driver | None, question: str, answer: Answer, *, top_k: int = 3,
) -> list[Recommendation]:
    if driver is None:
        return []

    run_id = new_run_id("recommendations_")
    logger = get_logger("analytics", run_id)

    cited_doc_ids = {c.doc_id for c in answer.citations if c.doc_id}
    cited_chunk_ids = sorted({c.chunk_id for c in answer.citations if c.chunk_id})
    node_ids = list(answer.subgraph_node_ids or [])

    similar_case = _build_similar_case(driver, question, cited_doc_ids, top_k, logger)
    expert = _build_expert(driver, cited_chunk_ids, sorted(cited_doc_ids), node_ids, top_k, logger)
    adjacent_topic = _build_adjacent_topic(driver, node_ids, top_k, logger)

    log_event(logger, stage="recommendations", event="build_finished",
               detail=f"n_similar_case={len(similar_case)} n_expert={len(expert)} "
                      f"n_adjacent_topic={len(adjacent_topic)}")
    return similar_case + expert + adjacent_topic
