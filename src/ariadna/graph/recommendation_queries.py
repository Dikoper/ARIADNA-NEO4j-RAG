"""Cypher-шаблоны блока «Рекомендации» (A-14, У-1) — только текст запросов.

Вход: нет (статические строки). Выход: `RECOMMENDATION_EXPERT_QUERY`,
`RECOMMENDATION_ADJACENT_QUERY` — параметризованные Cypher-запросы для
`analytics.recommendations` (A-14). Вынесены в отдельный файл, а не в
`graph/cypher_templates.py` (единственный источник Cypher-текста роутера A-10),
ради лимита ~350 строк на модуль (CONVENTIONS.md §3) — тот файл уже у потолка
после A-12 (subgraph/gap-запросы). Логика подстановки параметров/исполнения —
`analytics/recommendations.py`, здесь только текст.
Инвариант: ТОЛЬКО параметризованные запросы (`$param`) — конкатенация строк
в Cypher запрещена (инвариант №4, ARCHITECTURE.md).
Паспорт: docs/dev/modules/analytics.md (A-14); graph.md — по расположению файла.
"""
from __future__ import annotations

# ─── recommendation_expert ────────────────────────────────────────────────
# `:Expert` (обычная сущность онтологии, 727 узлов — единичный label-скан, не
# декартово произведение, EXISTS{} безопасен без непустых слотов) со-упомянут
# в чанках/документах ответа ($chunk_ids/$doc_ids) ЛИБО связан любой из 6 связей
# с сущностью подграфа ($node_ids). n_matched_sources — число РАЗНЫХ doc_id
# пересечения (не общий n_mentions эксперта) — «упоминается в N источниках».
RECOMMENDATION_EXPERT_QUERY = """
MATCH (e:Expert)
WHERE EXISTS {
    MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
    WHERE c.chunk_id IN $chunk_ids OR c.doc_id IN $doc_ids
} OR EXISTS {
    MATCH (e)-[:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT|DESCRIBED_IN|VALIDATED_BY|CONTRADICTS]-(n:Entity)
    WHERE n.id IN $node_ids
}
OPTIONAL MATCH (e)-[:MENTIONED_IN]->(mc:Chunk)
WHERE mc.chunk_id IN $chunk_ids OR mc.doc_id IN $doc_ids
WITH e, collect(DISTINCT mc.doc_id) AS matched_doc_ids, collect(DISTINCT mc.chunk_id) AS matched_chunk_ids_raw
RETURN e.id AS id, e.name AS name,
       size(matched_doc_ids) AS n_matched_sources,
       [x IN matched_chunk_ids_raw WHERE x IS NOT NULL] AS sample_chunk_ids,
       coalesce(e.n_mentions, 0) AS n_mentions
ORDER BY n_matched_sources DESC, n_mentions DESC
LIMIT $limit
"""

# ─── recommendation_adjacent ──────────────────────────────────────────────
# Соседи узлов подграфа ответа ($node_ids), исключая сами узлы, ДВУМЯ путями,
# объединёнными UNION (analytics дедупит/ранжирует в Python, предпочитая
# Material/Process/Property): 1 хоп по любой из 6 связей онтологии, ИЛИ
# co-mention — сосед в ТОМ ЖЕ чанке (MENTIONED_IN), без прямой связи между ними
# (constraints на рёбрах пусты во всём корпусе — co-mention даёт полноту,
# тот же приём, что experiments_publications_by_topic в cypher_templates.py).
RECOMMENDATION_ADJACENT_QUERY = """
CALL () {
    UNWIND $node_ids AS nid
    MATCH (n:Entity {id: nid})-[:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT|DESCRIBED_IN|VALIDATED_BY|CONTRADICTS]-(neighbor:Entity)
    WHERE NOT neighbor.id IN $node_ids
    RETURN neighbor, n.id AS via_id, n.name AS via_name
    UNION
    UNWIND $node_ids AS nid
    MATCH (n:Entity {id: nid})-[:MENTIONED_IN]->(c:Chunk)<-[:MENTIONED_IN]-(neighbor:Entity)
    WHERE NOT neighbor.id IN $node_ids
    RETURN neighbor, n.id AS via_id, n.name AS via_name
}
RETURN DISTINCT neighbor.id AS id, neighbor.name AS name,
       [l IN labels(neighbor) WHERE l <> 'Entity'][0] AS type,
       via_id, via_name,
       coalesce(neighbor.n_mentions, 0) AS n_mentions
LIMIT $limit
"""
