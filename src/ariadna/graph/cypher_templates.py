"""Реестр Cypher-шаблонов роутера (A-10) — только текст запросов + их реестр.

Вход: нет (статические строки). Выход: `TEMPLATES` — dict[template_id, Cypher],
`_TEMPLATE_DEFAULT_CANONICALS` — дефолтные канонические термины слота на случай,
если `search/router.py` не распознал в вопросе ничего конкретнее.
Логика подстановки параметров и исполнения — `graph/templates.py`
(`_build_params`/`execute_intent`), это лишь позволяет уложить `templates.py`
в лимит ~350 строк (CONVENTIONS.md §3) — исполняемого кода/веток тут нет.

Инвариант: ТОЛЬКО параметризованные запросы (`$param`) — конкатенация строк
в Cypher ЗАПРЕЩЕНА (инвариант №4, ARCHITECTURE.md).
Паспорт: docs/dev/modules/graph.md (A-10).
"""
from __future__ import annotations

# ═══════════════════════ Cypher-шаблоны (только $param) ═══════════════════

# ─── desalination_methods (эталонный запрос №1) ──────────────────────────
# Process-узлы (методы), совпавшие по имени/name_en с $process_terms
# (обессоливание/обратный осмос/электродиализ/нанофильтрация…); связанные
# сущности через USES_MATERIAL/OPERATES_AT_CONDITION/PRODUCES_OUTPUT/
# DESCRIBED_IN/VALIDATED_BY; NumericConstraint — через MENTIONED_IN->Chunk->
# HAS_CONSTRAINT (c_* на рёбрах пусты во всём корпусе, см. постановку задачи) —
# constraints фильтруются по $param_terms (сульфаты/сухой остаток/…) ПОСЛЕ
# сбора (список-проекция, не WHERE внутри OPTIONAL MATCH) — фильтр не режет
# сами Process-строки, только помечает релевантные ограничения.
_Q_DESALINATION_METHODS = """
MATCH (p:Process)
WHERE ANY(t IN $process_terms WHERE toLower(p.name) CONTAINS t OR toLower(coalesce(p.name_en,'')) CONTAINS t)
OPTIONAL MATCH (p)-[:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT|DESCRIBED_IN|VALIDATED_BY]-(o:Entity)
OPTIONAL MATCH (p)-[:MENTIONED_IN]->(c:Chunk)
OPTIONAL MATCH (c)-[:HAS_CONSTRAINT]->(nc:NumericConstraint)
WITH p,
     collect(DISTINCT CASE WHEN o IS NOT NULL THEN {id: o.id, name: o.name} END) AS related_raw,
     collect(DISTINCT c.chunk_id) AS chunk_ids_raw,
     collect(DISTINCT CASE WHEN nc IS NOT NULL
             THEN {param: nc.param, norm_value: nc.norm_value, value_max: nc.value_max,
                   norm_unit: nc.norm_unit, source_text: nc.source_text} END) AS constraints_raw
WITH p,
     [x IN related_raw WHERE x IS NOT NULL] AS related,
     [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
     [x IN constraints_raw WHERE x IS NOT NULL] AS all_constraints
RETURN p.id AS node_id, p.name AS name, p.name_en AS name_en, p.geography AS geography,
       p.is_tech_solution AS is_tech_solution, p.confidence AS confidence, p.n_mentions AS n_mentions,
       related,
       [x IN all_constraints WHERE size($param_terms) = 0
        OR ANY(t IN $param_terms WHERE toLower(x.param) CONTAINS t)] AS constraints,
       chunk_ids,
       [p.id] + [x IN related | x.id] AS node_ids
ORDER BY p.is_tech_solution DESC, p.confidence DESC, p.n_mentions DESC
LIMIT $limit
"""

# ─── catholyte_circulation (эталонный запрос №2) ─────────────────────────
# Material-узел католита ($material_terms) -> связанные Process-узлы
# (электролиз/циркуляция/электроэкстракция…) через USES_MATERIAL/
# OPERATES_AT_CONDITION/PRODUCES_OUTPUT; constraints — скорость потока/расход
# ($param_terms), тем же принципом «фильтр после сбора», что и в шаблоне a.
_Q_CATHOLYTE_CIRCULATION = """
MATCH (m:Material)
WHERE ANY(t IN $material_terms WHERE toLower(m.name) CONTAINS t OR toLower(coalesce(m.name_en,'')) CONTAINS t)
MATCH (m)-[:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT]-(p:Process)
OPTIONAL MATCH (p)-[:MENTIONED_IN]->(c:Chunk)
OPTIONAL MATCH (c)-[:HAS_CONSTRAINT]->(nc:NumericConstraint)
WITH m, p,
     collect(DISTINCT c.chunk_id) AS chunk_ids_raw,
     collect(DISTINCT CASE WHEN nc IS NOT NULL
             THEN {param: nc.param, norm_value: nc.norm_value, value_max: nc.value_max,
                   norm_unit: nc.norm_unit, source_text: nc.source_text} END) AS constraints_raw
WITH m, p,
     [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
     [x IN constraints_raw WHERE x IS NOT NULL] AS all_constraints
RETURN p.id AS node_id, p.name AS name, p.name_en AS name_en, p.geography AS geography,
       p.is_tech_solution AS is_tech_solution, p.confidence AS confidence, p.n_mentions AS n_mentions,
       m.id AS material_id, m.name AS material_name,
       [x IN all_constraints WHERE size($param_terms) = 0
        OR ANY(t IN $param_terms WHERE toLower(x.param) CONTAINS t)] AS constraints,
       chunk_ids,
       [p.id, m.id] AS node_ids
ORDER BY p.is_tech_solution DESC, p.confidence DESC, p.n_mentions DESC
LIMIT $limit
"""

# ─── experiments_publications_by_topic (эталонный запрос №3) ─────────────
# Material-узлы темы ($material_terms: штейн/шлак/платиноиды/Au/Ag…) ->
# Experiment/Publication, делящие с ними чанк (MENTIONED_IN) — прямых связей
# DESCRIBED_IN/VALIDATED_BY от Experiment к теме в корпусе мало, со-упоминание
# в чанке даёт полноту; год — через (:Document)-[:HAS_CHUNK]->(:Chunk),
# $year_from не задан -> фильтр по году не применяется.
_Q_EXPERIMENTS_PUBLICATIONS_BY_TOPIC = """
MATCH (m:Material)
WHERE ANY(t IN $material_terms WHERE toLower(m.name) CONTAINS t OR toLower(coalesce(m.name_en,'')) CONTAINS t)
MATCH (m)-[:MENTIONED_IN]->(c:Chunk)<-[:MENTIONED_IN]-(e:Entity)
WHERE e:Experiment OR e:Publication
OPTIONAL MATCH (doc:Document)-[:HAS_CHUNK]->(c)
WITH e, m, c, doc
WHERE $year_from IS NULL OR doc.year IS NULL OR doc.year >= $year_from
WITH e,
     collect(DISTINCT m.name) AS materials,
     collect(DISTINCT c.chunk_id) AS chunk_ids_raw,
     collect(DISTINCT doc.year) AS years_raw
RETURN e.id AS node_id, e.name AS name,
       CASE WHEN e:Experiment THEN 'Experiment' ELSE 'Publication' END AS entity_kind,
       e.confidence AS confidence, e.n_mentions AS n_mentions,
       materials,
       [y IN years_raw WHERE y IS NOT NULL] AS years,
       [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
       [e.id] AS node_ids
ORDER BY e.confidence DESC, e.n_mentions DESC
LIMIT $limit
"""

# ─── mine_water_injection (эталонный запрос №4) ──────────────────────────
# Process-узлы либо по имени ($process_terms: закачка/нагнетание/инъекция/
# скважина/горизонт…), либо со-упоминаемые в одном чанке с материалом
# «шахтные воды» ($material_terms) — EXISTS{} subquery (Neo4j 5.x), т.к.
# прямых графовых связей «закачка шахтных вод» <-> конкретный Process почти
# нет (узкая тема корпуса, см. worklog); doc_geographies — гео документов,
# в которых встречается техрешение (для разбивки РФ/зарубеж, У-2/пункт 4).
_Q_MINE_WATER_INJECTION = """
MATCH (p:Process)
WHERE ANY(t IN $process_terms WHERE toLower(p.name) CONTAINS t OR toLower(coalesce(p.name_en,'')) CONTAINS t)
   OR EXISTS {
        MATCH (p)-[:MENTIONED_IN]->(mc:Chunk)<-[:MENTIONED_IN]-(m:Material)
        WHERE ANY(t IN $material_terms WHERE toLower(m.name) CONTAINS t OR toLower(coalesce(m.name_en,'')) CONTAINS t)
      }
OPTIONAL MATCH (p)-[:MENTIONED_IN]->(c:Chunk)
OPTIONAL MATCH (doc:Document)-[:HAS_CHUNK]->(c)
OPTIONAL MATCH (c)-[:HAS_CONSTRAINT]->(nc:NumericConstraint)
WITH p,
     collect(DISTINCT c.chunk_id) AS chunk_ids_raw,
     collect(DISTINCT doc.geography) AS doc_geo_raw,
     collect(DISTINCT CASE WHEN nc IS NOT NULL
             THEN {param: nc.param, norm_value: nc.norm_value, value_max: nc.value_max,
                   norm_unit: nc.norm_unit, source_text: nc.source_text} END) AS constraints_raw
WITH p,
     [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
     [x IN doc_geo_raw WHERE x IS NOT NULL] AS doc_geographies,
     [x IN constraints_raw WHERE x IS NOT NULL] AS all_constraints
RETURN p.id AS node_id, p.name AS name, p.name_en AS name_en, p.geography AS geography,
       p.is_tech_solution AS is_tech_solution, p.confidence AS confidence, p.n_mentions AS n_mentions,
       doc_geographies,
       [x IN all_constraints WHERE size($param_terms) = 0
        OR ANY(t IN $param_terms WHERE toLower(x.param) CONTAINS t)] AS constraints,
       chunk_ids,
       [p.id] AS node_ids
ORDER BY p.is_tech_solution DESC, p.confidence DESC, p.n_mentions DESC
LIMIT $limit
"""

# ─── compare_ru_foreign (У-2) ─────────────────────────────────────────────
# Срез сущностей темы ($terms) с разбивкой по geography — как узла (majority
# по документам-упоминаниям, entity_dedup.majority_geography), так и
# документов-источников (doc_geographies) — A-11/analytics группирует rows
# по geography для таблицы сравнения технологий.
_Q_COMPARE_RU_FOREIGN = """
MATCH (n:Entity)
WHERE ANY(t IN $terms WHERE toLower(n.name) CONTAINS t OR toLower(coalesce(n.name_en,'')) CONTAINS t)
OPTIONAL MATCH (n)-[:MENTIONED_IN]->(c:Chunk)
OPTIONAL MATCH (doc:Document)-[:HAS_CHUNK]->(c)
WITH n,
     collect(DISTINCT c.chunk_id) AS chunk_ids_raw,
     collect(DISTINCT doc.doc_id) AS doc_ids_raw,
     collect(DISTINCT doc.geography) AS doc_geo_raw
RETURN n.id AS node_id, n.name AS name, n.name_en AS name_en,
       [l IN labels(n) WHERE l <> 'Entity'][0] AS entity_type,
       n.geography AS node_geography,
       [x IN doc_geo_raw WHERE x IS NOT NULL] AS doc_geographies,
       [x IN doc_ids_raw WHERE x IS NOT NULL] AS doc_ids,
       n.confidence AS confidence, n.n_mentions AS n_mentions,
       [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
       [n.id] AS node_ids
ORDER BY n.geography, n.confidence DESC, n.n_mentions DESC
LIMIT $limit
"""

# ─── gap_matrix (заготовка A-12) ──────────────────────────────────────────
# Пары (Material x TechSolution-Process) БЕЗ прямой связи между ними
# (NOT EXISTS), но со-упоминаемые хотя бы в одном чанке (n_sources — счётчик
# таких чанков; n_sources=0 -> пара вообще не встречается вместе — «полный»
# пробел). ВАЖНО: material_terms/process_terms ОБЯЗАНЫ быть непустыми — иначе
# декартово произведение Material x Process (~26 млн пар) — см. templates.py:
# execute_intent(). material_n_mentions/process_n_mentions (A-12, дефект №1
# tester-отчёта) — ДОБАВЛЕНЫ в RETURN поверх исходных полей, порядок и состав
# остальных полей НЕ менялся: analytics.gap_map использует их как тай-брейк
# по значимости (сумма n_mentions DESC вместо алфавита — иначе редкий по
# алфавиту материал типа "(NH4)2SO4" выталкивает частые пары из топа среди
# ~42.2k пар с n_sources=0, см. worklogs/analytics.md); graph.templates.
# execute_intent()/search читают только row["node_ids"]/row["chunk_ids"]
# (единый межшаблонный контракт, см. docstring templates.py) — новые поля
# роутеру не мешают.
_Q_GAP_MATRIX = """
MATCH (m:Material), (p:Process)
WHERE p.is_tech_solution = true
  AND ANY(t IN $material_terms WHERE toLower(m.name) CONTAINS t OR toLower(coalesce(m.name_en,'')) CONTAINS t)
  AND ANY(t IN $process_terms WHERE toLower(p.name) CONTAINS t OR toLower(coalesce(p.name_en,'')) CONTAINS t)
  AND NOT (m)-[:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT]-(p)
OPTIONAL MATCH (m)-[:MENTIONED_IN]->(c:Chunk)<-[:MENTIONED_IN]-(p)
WITH m, p, count(DISTINCT c) AS n_sources, collect(DISTINCT c.chunk_id) AS chunk_ids_raw
RETURN m.id AS material_id, m.name AS material_name, p.id AS process_id, p.name AS process_name,
       n_sources,
       [x IN chunk_ids_raw WHERE x IS NOT NULL] AS chunk_ids,
       [m.id, p.id] AS node_ids,
       coalesce(m.n_mentions, 0) AS material_n_mentions,
       coalesce(p.n_mentions, 0) AS process_n_mentions
ORDER BY n_sources DESC, material_name, process_name
LIMIT $limit
"""

# ─── TEMPLATES ─────────────────────────────────────────────────────────────
# Назначение: реестр Cypher-шаблонов роутера — единственный источник истины
#   template_id -> текст запроса; A-11 обращается только через
#   graph.templates.execute_intent(), не читает TEMPLATES напрямую (но не
#   запрещено — модуль не скрывает словарь).
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
TEMPLATES: dict[str, str] = {
    "desalination_methods": _Q_DESALINATION_METHODS,
    "catholyte_circulation": _Q_CATHOLYTE_CIRCULATION,
    "experiments_publications_by_topic": _Q_EXPERIMENTS_PUBLICATIONS_BY_TOPIC,
    "mine_water_injection": _Q_MINE_WATER_INJECTION,
    "compare_ru_foreign": _Q_COMPARE_RU_FOREIGN,
    "gap_matrix": _Q_GAP_MATRIX,
}

# Дефолтные канонические термины слота, если router не распознал в вопросе
# ничего более конкретного (см. search/router.py) — используются ТОЛЬКО
# как fallback внутри templates._expand_terms при пустом слоте.
TEMPLATE_DEFAULT_CANONICALS: dict[str, dict[str, list[str]]] = {
    "desalination_methods": {
        "process": ["обессоливание", "обратный осмос", "электродиализ", "нанофильтрация"],
        "property": ["минерализация"],
    },
    "catholyte_circulation": {
        "material": ["католит"],
        "process": ["электроэкстракция", "электролиз"],
    },
    "experiments_publications_by_topic": {
        "material": ["штейн", "шлак", "платиноиды", "золото", "серебро"],
    },
    "mine_water_injection": {
        "process": ["закачка шахтных вод"],
        "material": ["шахтные воды"],
    },
}

# ─── gap_cell_context (A-12) ───────────────────────────────────────────────
# Назначение: batched-запрос текстового условия для ячеек карты пробелов —
# по набору chunk_id (со-упоминания пары Material x Process из gap_matrix)
# возвращает краткое NumericConstraint (param+op+norm_value+norm_unit) и имена
# Property-сущностей, со-упоминаемых в том же чанке — analytics.gap_map строит
# GapCell.condition. ОДИН запрос на весь отчёт (не по ячейке) — иначе N мелких
# запросов на живом графе.
GAP_CELL_CONTEXT_QUERY = """
UNWIND $chunk_ids AS cid
MATCH (c:Chunk {chunk_id: cid})
OPTIONAL MATCH (c)-[:HAS_CONSTRAINT]->(nc:NumericConstraint)
OPTIONAL MATCH (c)<-[:MENTIONED_IN]-(prop:Property)
WITH c.chunk_id AS chunk_id,
     collect(DISTINCT CASE WHEN nc IS NOT NULL
             THEN nc.param + ' ' + nc.op + ' ' + toString(nc.norm_value) + ' ' + nc.norm_unit END) AS constraint_texts_raw,
     collect(DISTINCT prop.name) AS property_names_raw
RETURN chunk_id,
       [x IN constraint_texts_raw WHERE x IS NOT NULL] AS constraint_texts,
       [x IN property_names_raw WHERE x IS NOT NULL] AS property_names
"""

# ─── geography_themes (A-12) ────────────────────────────────────────────────
# Назначение: срез Material/Process-тем с достаточным n_mentions ($min_mentions)
# и множеством geography документов, где тема упоминается (doc_geographies) —
# analytics.gap_map относит тему к only_ru/only_foreign, если множество состоит
# РОВНО из одного значения ('ru' либо 'foreign'; 'unknown'/смешанное — тема не
# попадает ни в один список). Единичный label-скан Material|Process без
# term-фильтра — НЕ декартово произведение (в отличие от gap_matrix), безопасно
# исполнять без обязательных слотов.
GEOGRAPHY_THEMES_QUERY = """
MATCH (n)
WHERE (n:Material OR n:Process) AND n.n_mentions >= $min_mentions
OPTIONAL MATCH (n)-[:MENTIONED_IN]->(c:Chunk)
OPTIONAL MATCH (doc:Document)-[:HAS_CHUNK]->(c)
WITH n, collect(DISTINCT doc.geography) AS doc_geo_raw
RETURN n.id AS node_id, n.name AS name,
       [l IN labels(n) WHERE l <> 'Entity'][0] AS entity_type,
       [x IN doc_geo_raw WHERE x IS NOT NULL] AS doc_geographies
ORDER BY n.n_mentions DESC
LIMIT $limit
"""

# ─── doc_geography_update (A-22) ────────────────────────────────────────────
# Назначение: точечное обновление Document.geography по результату правилового
# (или последующего LLM) классификатора geo_classify.py — MATCH по doc_id (узел
# уже создан graph.lexical_loader.load_documents), не MERGE (документ, которого
# нет в графе, — строка тихо не даёт результата, не создаёт сироту). updated_at/
# edited_by — provenance-поля У-4, тот же принцип, что graph_entity_writer.
DOC_GEOGRAPHY_UPDATE_QUERY = """
UNWIND $rows AS row
MATCH (d:Document {doc_id: row.doc_id})
SET d.geography = row.geography, d.updated_at = row.updated_at, d.edited_by = row.edited_by
"""

# ─── subgraph_nodes / subgraph_edges (A-12, интерфейс для UI) ───────────────
# Назначение: узлы подграфа ответа по набору Entity.id, урезанные до $max_nodes
# по n_mentions (UI рисует ограниченный граф, не весь набор node_ids ответа).
SUBGRAPH_NODES_QUERY = """
MATCH (n:Entity) WHERE n.id IN $node_ids
RETURN n.id AS id, n.name AS name,
       [l IN labels(n) WHERE l <> 'Entity'][0] AS type,
       coalesce(n.is_tech_solution, false) AS is_tech_solution
ORDER BY n.n_mentions DESC
LIMIT $max_nodes
"""

# Назначение: рёбра ВСЕХ 6 UPPER_SNAKE-типов онтологии между узлами набора
# $node_ids (вызывающая сторона — graph.templates.fetch_subgraph — передаёт уже
# урезанный SUBGRAPH_NODES_QUERY набор id, не исходный список ответа) —
# CONTRADICTS помечается is_contradicts=true (У-3, UI красит красным).
SUBGRAPH_EDGES_QUERY = """
MATCH (a:Entity)-[r:USES_MATERIAL|OPERATES_AT_CONDITION|PRODUCES_OUTPUT|DESCRIBED_IN|VALIDATED_BY|CONTRADICTS]->(b:Entity)
WHERE a.id IN $node_ids AND b.id IN $node_ids
RETURN a.id AS source, b.id AS target, type(r) AS type,
       coalesce(r.confidence, 0.5) AS confidence,
       type(r) = 'CONTRADICTS' AS is_contradicts
"""
