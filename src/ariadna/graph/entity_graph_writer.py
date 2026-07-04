"""Запись агрегированного сущностного графа (entity_dedup.AggregationResult)
в Neo4j: констрейнты, батчи UNWIND+MERGE узлов/связей/constraint-узлов/
provenance-связей, самопроверка счётчиков.

Вход: `entity_dedup.AggregationResult` (чистая агрегация, без Neo4j),
`contracts.Geography` по doc_id. Выход: узлы `:Entity`+`:<EntityType>`,
связи `:<RELATION_TYPE>`, узлы `:NumericConstraint` + `HAS_CONSTRAINT`,
`MENTIONED_IN` — побочный эффект в Neo4j.
Зависимости: neo4j (bolt-драйвер), `ariadna.contracts`, `ariadna.graph.
entity_dedup`, `ariadna.graph.config`.
Инвариант: единственный модуль с правом записи в Neo4j (docs/dev/modules/
graph.md) — вызывается только из `entity_loader.main()`.
Паспорт: docs/dev/modules/graph.md (A-09).
"""
from __future__ import annotations

from collections import defaultdict

from neo4j import Driver

from ariadna.contracts import EntityType, Geography, NumericConstraint, RelationType
from ariadna.graph.config import ENTITY_NODE_DEFAULT_CONFIDENCE, LOAD_BATCH_SIZE
from ariadna.graph.entity_dedup import AggregationResult, constraint_node_id, majority_geography, pick_name_en


# ─── ensure_entity_constraints ───────────────────────────────────────────
# Назначение: констрейнты уникальности Entity.id и NumericConstraint.id —
#   до загрузки, идемпотентность последующих MERGE опирается на них.
# Входные связи: neo4j.Driver
# Выходные данные: нет (побочный эффект — DDL в Neo4j)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def ensure_entity_constraints(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE e.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT numeric_constraint_id_unique IF NOT EXISTS "
            "FOR (n:NumericConstraint) REQUIRE n.id IS UNIQUE"
        )


# Назначение: MERGE-запрос узлов одного EntityType — метка интерполируется
#   строкой (EntityType — фиксированный enum контракта, не пользовательский
#   ввод; Cypher не параметризует идентификаторы меток/типов связей). ON CREATE
#   пишет все свойства текущей (возможно частичной, при --limit) агрегации —
#   первый прогон. ON MATCH — только монотонные обновления существующего узла:
#   n_mentions/confidence не убывают, is_tech_solution — только OR (никогда не
#   гасится обратно в false). Имя/geography/synonyms/attrs/provenance первого
#   упоминания на ON MATCH НЕ трогаются — частичный повторный прогон (--limit)
#   поверх уже загруженной базы больше не деградирует боевые данные (module-
#   tester A-09 багрепорт: n_tech_solution 3012→3008 на `--limit 5`).
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def _entity_merge_query(entity_type: EntityType) -> str:
    label = entity_type.value
    return (
        "UNWIND $rows AS row "
        "MERGE (e:Entity {id: row.id}) "
        f"ON CREATE SET e:{label}, "
        "    e.name = row.name, e.name_en = row.name_en, e.geography = row.geography, "
        "    e.n_mentions = row.n_mentions, e.doc_id = row.doc_id, e.chunk_id = row.chunk_id, "
        "    e.confidence = row.confidence, e.updated_at = row.updated_at, "
        "    e.edited_by = row.edited_by, e.synonyms = row.synonyms, "
        "    e.is_tech_solution = row.is_tech_solution, "
        "    e += row.attrs_flat "
        "ON MATCH SET "
        "    e.n_mentions = CASE WHEN row.n_mentions > e.n_mentions THEN row.n_mentions ELSE e.n_mentions END, "
        "    e.confidence = CASE WHEN row.confidence > e.confidence THEN row.confidence ELSE e.confidence END, "
        "    e.is_tech_solution = (e.is_tech_solution OR row.is_tech_solution)"
    )


# ─── load_entities ────────────────────────────────────────────────────────
# Назначение: пишет узлы :Entity+:<Type> батчами UNWIND+MERGE, сгруппированными
#   по EntityType (динамическая метка); attrs -> плоские свойства attr_<key>
#   через SET e += map; is_tech_solution — по AggregationResult.tech_solution_ids.
#   Существующий узел (повторный/частичный прогон) обновляется МОНОТОННО —
#   см. _entity_merge_query: частичный агрегат (--limit) не может понизить
#   n_mentions/confidence/is_tech_solution уже загруженного узла.
# Входные связи: neo4j.Driver, AggregationResult.entities, doc_geo (meta.jsonl),
#   today_iso — дата актуализации (У-4)
# Выходные данные: int — число обработанных узлов (созданных или обновлённых)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def load_entities(
    driver: Driver,
    agg: AggregationResult,
    doc_geo: dict[str, Geography],
    today_iso: str,
) -> int:
    by_type: dict[EntityType, list[dict]] = defaultdict(list)
    for node_id, ent in agg.entities.items():
        geography = majority_geography(ent.doc_ids, doc_geo)
        name_en = pick_name_en(ent.canon, ent.synonyms)
        by_type[ent.type].append({
            "id": node_id,
            "name": ent.canon,
            "name_en": name_en,
            "geography": geography.value,
            "n_mentions": ent.n_mentions,
            "doc_id": ent.first_doc_id,
            "chunk_id": ent.first_chunk_id,
            "confidence": ENTITY_NODE_DEFAULT_CONFIDENCE,
            "updated_at": today_iso,
            "edited_by": "",
            "synonyms": ent.synonyms,
            "is_tech_solution": node_id in agg.tech_solution_ids,
            "attrs_flat": {f"attr_{k}": v for k, v in ent.attrs.items()},
        })

    n_total = 0
    with driver.session() as session:
        for entity_type, rows in by_type.items():
            query = _entity_merge_query(entity_type)
            for i in range(0, len(rows), LOAD_BATCH_SIZE):
                session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])
            n_total += len(rows)
    return n_total


# Назначение: MERGE-запрос связей одного RelationType — тип связи UPPER_SNAKE
#   интерполируется строкой (RelationType — фиксированный enum контракта).
#   Эндпойнты — MATCH, не MERGE: узлы уже загружены load_entities раньше.
#   ON CREATE пишет все свойства текущей агрегации. ON MATCH — только
#   монотонно: n_evidence/confidence не убывают на частичном повторном
#   прогоне (--limit) поверх уже загруженной связи; constraints_json/
#   c_param/c_op/c_norm_value/c_norm_unit и provenance первого упоминания
#   на ON MATCH НЕ трогаются (см. _entity_merge_query — тот же принцип).
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def _relation_merge_query(rel_type: RelationType) -> str:
    edge_label = rel_type.value.upper()
    return (
        "UNWIND $rows AS row "
        "MATCH (s:Entity {id: row.source_id}) "
        "MATCH (t:Entity {id: row.target_id}) "
        f"MERGE (s)-[r:{edge_label}]->(t) "
        "ON CREATE SET r.confidence = row.confidence, r.n_evidence = row.n_evidence, "
        "    r.doc_id = row.doc_id, r.chunk_id = row.chunk_id, "
        "    r.updated_at = row.updated_at, r.edited_by = row.edited_by, "
        "    r.constraints_json = row.constraints_json, r.c_param = row.c_param, "
        "    r.c_op = row.c_op, r.c_norm_value = row.c_norm_value, r.c_norm_unit = row.c_norm_unit "
        "ON MATCH SET "
        "    r.n_evidence = CASE WHEN row.n_evidence > r.n_evidence THEN row.n_evidence ELSE r.n_evidence END, "
        "    r.confidence = CASE WHEN row.confidence > r.confidence THEN row.confidence ELSE r.confidence END"
    )


# ─── load_relations ────────────────────────────────────────────────────────
# Назначение: пишет связи батчами UNWIND+MERGE по (source_id, target_id, type);
#   constraints (внутри связи, уже объединены entity_dedup.aggregate_from_rows
#   по всем вхождениям) сериализуются списком JSON-строк в constraints_json +
#   плоские c_param/c_op/c_norm_value/c_norm_unit от ПЕРВОГО constraint связи
#   (для простых Cypher-фильтров A-10). Существующая связь (повторный/частичный
#   прогон) обновляется МОНОТОННО — см. _relation_merge_query.
# Входные связи: neo4j.Driver, AggregationResult.relations, today_iso
# Выходные данные: int — число обработанных связей (созданных или обновлённых)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def load_relations(driver: Driver, agg: AggregationResult, today_iso: str) -> int:
    by_type: dict[RelationType, list[dict]] = defaultdict(list)
    for (source_id, target_id, rel_type), ragg in agg.relations.items():
        first = ragg.constraints[0] if ragg.constraints else None
        by_type[rel_type].append({
            "source_id": source_id,
            "target_id": target_id,
            "confidence": ragg.confidence,
            "n_evidence": ragg.n_evidence,
            "doc_id": ragg.first_doc_id,
            "chunk_id": ragg.first_chunk_id,
            "updated_at": today_iso,
            "edited_by": "",
            "constraints_json": [c.model_dump_json() for c in ragg.constraints],
            "c_param": first.param if first else "",
            "c_op": first.op.value if first else "",
            "c_norm_value": first.norm_value if first else None,
            "c_norm_unit": first.norm_unit if first else "",
        })

    n_total = 0
    with driver.session() as session:
        for rel_type, rows in by_type.items():
            query = _relation_merge_query(rel_type)
            for i in range(0, len(rows), LOAD_BATCH_SIZE):
                session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])
            n_total += len(rows)
    return n_total


# ─── load_numeric_constraints ─────────────────────────────────────────────
# Назначение: пишет chunk-level узлы :NumericConstraint (regex-нормализатор,
#   не LLM) + связь (:Chunk)-[:HAS_CONSTRAINT]->(:NumericConstraint). Чанк не
#   найден по chunk_id — MATCH идёт ДО MERGE узла: ни узел, ни связь не
#   создаются, строка просто не даёт результата, не падает (сирота
#   :NumericConstraint без HAS_CONSTRAINT невозможен — module-tester A-09
#   багрепорт: `--limit 5` на боевой базе создавал ровно такой сироту, т.к.
#   chunk_id частичного набора не резолвился ни к одному :Chunk, а узел всё
#   равно создавался). constraint_node_id теперь включает unit/value_max
#   (см. entity_dedup.constraint_node_id) — коллизия разных по смыслу
#   ограничений с одинаковым узлом больше невозможна, повторный MERGE
#   идентичной записи безопасен (перезаписывает теми же значениями).
# Входные связи: neo4j.Driver, AggregationResult.chunk_constraints
# Выходные данные: int — число обработанных constraint-записей (не все
#   обязательно попадают в граф — см. пропуск при отсутствующем :Chunk)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def load_numeric_constraints(driver: Driver, chunk_constraints: list[tuple[str, NumericConstraint]]) -> int:
    rows = []
    for chunk_id, c in chunk_constraints:
        rows.append({
            "id": constraint_node_id(chunk_id, c),
            "chunk_id": chunk_id,
            "param": c.param,
            "op": c.op.value,
            "value": c.value,
            "value_max": c.value_max,
            "unit": c.unit,
            "norm_value": c.norm_value,
            "norm_unit": c.norm_unit,
            "source_text": c.source_text,
        })

    query = (
        "UNWIND $rows AS row "
        "MATCH (c:Chunk {chunk_id: row.chunk_id}) "
        "MERGE (n:NumericConstraint {id: row.id}) "
        "SET n.param = row.param, n.op = row.op, n.value = row.value, "
        "    n.value_max = row.value_max, n.unit = row.unit, "
        "    n.norm_value = row.norm_value, n.norm_unit = row.norm_unit, "
        "    n.source_text = row.source_text "
        "MERGE (c)-[:HAS_CONSTRAINT]->(n)"
    )
    with driver.session() as session:
        for i in range(0, len(rows), LOAD_BATCH_SIZE):
            session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])
    return len(rows)


# ─── load_mentioned_in ─────────────────────────────────────────────────────
# Назначение: пишет provenance-связь (:Entity)-[:MENTIONED_IN]->(:Chunk) для
#   каждой пары (сущность, чанк-упоминание) — цитируемость до чанка (У-4/У-6).
#   Отсутствующий Entity/Chunk — MATCH не даёт строки, пара пропускается.
# Входные связи: neo4j.Driver, AggregationResult.mentioned_in
# Выходные данные: int — число обработанных пар
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def load_mentioned_in(driver: Driver, mentioned_in: list[tuple[str, str]]) -> int:
    rows = [{"entity_id": e, "chunk_id": c} for e, c in mentioned_in]
    query = (
        "UNWIND $rows AS row "
        "MATCH (e:Entity {id: row.entity_id}) "
        "MATCH (c:Chunk {chunk_id: row.chunk_id}) "
        "MERGE (e)-[:MENTIONED_IN]->(c)"
    )
    with driver.session() as session:
        for i in range(0, len(rows), LOAD_BATCH_SIZE):
            session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])
    return len(rows)


# ─── self_check ────────────────────────────────────────────────────────────
# Назначение: самопроверка после загрузки — счётчики узлов по label, связей
#   по типу, MENTIONED_IN/HAS_CONSTRAINT, NumericConstraint, tech-solution.
#   Смоук-ассерты: сумма узлов по типам = :Entity целиком; tech-solution —
#   подмножество :Process.
# Входные связи: neo4j.Driver (после load_entities/load_relations/…)
# Выходные данные: dict с цифрами — в лог и в отчёт агента
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def self_check(driver: Driver) -> dict:
    report: dict = {}
    with driver.session() as session:
        report["n_entity_total"] = session.run("MATCH (e:Entity) RETURN count(e) AS c").single()["c"]

        by_label = {}
        for et in EntityType:
            by_label[et.value] = session.run(
                f"MATCH (e:{et.value}) RETURN count(e) AS c"
            ).single()["c"]
        report["n_by_label"] = by_label

        by_rel = {}
        for rt in RelationType:
            edge = rt.value.upper()
            by_rel[edge] = session.run(
                f"MATCH ()-[r:{edge}]->() RETURN count(r) AS c"
            ).single()["c"]
        report["n_by_relation"] = by_rel

        report["n_mentioned_in"] = session.run(
            "MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS c"
        ).single()["c"]
        report["n_numeric_constraints"] = session.run(
            "MATCH (n:NumericConstraint) RETURN count(n) AS c"
        ).single()["c"]
        report["n_has_constraint"] = session.run(
            "MATCH ()-[r:HAS_CONSTRAINT]->() RETURN count(r) AS c"
        ).single()["c"]
        report["n_tech_solution"] = session.run(
            "MATCH (p:Process {is_tech_solution: true}) RETURN count(p) AS c"
        ).single()["c"]

    assert sum(report["n_by_label"].values()) == report["n_entity_total"], (
        "каждый узел :Entity обязан иметь ровно одну типовую метку EntityType"
    )
    assert report["n_tech_solution"] <= report["n_by_label"][EntityType.PROCESS.value], (
        "tech-solution — подмножество узлов :Process"
    )
    return report
