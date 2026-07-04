"""Тесты A-09: `ariadna.graph.entity_graph_writer` — против живого Neo4j
(`ariadna_neo4j`, .env в корне), изолированно от боевых данных (23584 :Entity,
24538 связей уже в базе — см. docs/dev/worklogs/graph.md#A-09). Все узлы этого
модуля — с префиксом `test_a09_` в chunk_id / id (см. tests/graph/conftest.py:
`clean_a09_nodes`, автоочистка до/после каждого теста).

Проверяется: двойная метка :Entity+:<Type>, provenance (У-4) на узле и ребре,
идемпотентность повторной загрузки, частичный/повторный прогон НЕ деградирует
уже загруженные узлы/связи (монотонные ON MATCH SET — fix бага №1 module-tester),
MENTIONED_IN к существующему/отсутствующему :Chunk, NumericConstraint+
HAS_CONSTRAINT (id теперь учитывает unit/value_max — см. test_entity_dedup.py::
test_constraint_node_id_differs_by_unit — коллизия и сироты невозможны),
constraints_json/c_param/c_op на ребре, self_check() на боевой базе.

AggregationResult строится вручную (EntityAgg/RelationAgg) — слой entity_dedup
уже покрыт test_entity_dedup.py отдельно, здесь тестируется только запись в Neo4j.
"""
from __future__ import annotations

import json

import pytest

from ariadna.contracts import CompareOp, EntityType, Geography, NumericConstraint, RelationType
from ariadna.graph.config import ENTITY_NODE_DEFAULT_CONFIDENCE
from ariadna.graph.entity_dedup import AggregationResult, EntityAgg, RelationAgg, constraint_node_id, make_node_id
from ariadna.graph.entity_graph_writer import (
    ensure_entity_constraints,
    load_entities,
    load_mentioned_in,
    load_numeric_constraints,
    load_relations,
    self_check,
)

TODAY = "2026-07-04"

COPPER_CANON = "test_a09_медьсодержащая"  # длинное кириллическое слово — латинский
# префикс изоляции "test_a09_" не должен пересилить кириллицу в script-эвристике
# pick_name_en (см. entity_dedup._is_mostly_cyrillic); короткое "test_a09_медь"
# для этой цели не годится: 5 латинских букв балласта >= 4 кириллических.
COPPER_ID = make_node_id(EntityType.MATERIAL, COPPER_CANON)
PROCESS_ID = make_node_id(EntityType.PROCESS, "test_a09_электролиз")
OTHER_PROCESS_ID = make_node_id(EntityType.PROCESS, "test_a09_иной_процесс")

CHUNK_1 = "test_a09_c1"
CHUNK_2 = "test_a09_c2"
CHUNK_MISSING = "test_a09_missing_chunk"


# ─── _make_test_chunks ────────────────────────────────────────────────────
# Назначение: создаёт минимальные узлы :Chunk (только chunk_id) для проверки
#   MENTIONED_IN/HAS_CONSTRAINT — entity_graph_writer только MATCH-ит по
#   chunk_id, остальные свойства Chunk ему не нужны.
# Уровень: ✅ реализовано (module-tester A-09)
def _make_test_chunks(driver, chunk_ids: list[str]) -> None:
    with driver.session() as session:
        session.run(
            "UNWIND $ids AS cid MERGE (c:Chunk {chunk_id: cid})", ids=chunk_ids,
        )


def _nc(param="test_a09_temp", op=CompareOp.LE, value=60.0, unit="°C", norm_value=60.0, norm_unit="°C") -> NumericConstraint:
    return NumericConstraint(
        param=param, op=op, value=value, unit=unit,
        norm_value=norm_value, norm_unit=norm_unit, source_text=f"{param} {op.value} {value}{unit}",
    )


# ─── _basic_agg ────────────────────────────────────────────────────────────
# Назначение: минимальный AggregationResult для большинства тестов writer'а —
#   2 узла (Material copper, Process с constraint на operates_at_condition),
#   1 связь с constraints, copper упомянута в 2 чанках (+ 1 отсутствующий).
# Уровень: ✅ реализовано (module-tester A-09)
def _basic_agg() -> AggregationResult:
    copper = EntityAgg(
        id=COPPER_ID, type=EntityType.MATERIAL, canon=COPPER_CANON,
        attrs={"purity": "99.9%"}, n_mentions=2,
        first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1,
        doc_ids={"test_a09_doc1", "test_a09_doc2"},
        synonyms=["test_a09_copper", COPPER_CANON],
    )
    process = EntityAgg(
        id=PROCESS_ID, type=EntityType.PROCESS, canon="test_a09_электролиз",
        attrs={}, n_mentions=1,
        first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1,
        doc_ids={"test_a09_doc1"},
        synonyms=["test_a09_electrolysis"],
    )
    constraint = _nc()
    relation = RelationAgg(
        source_id=PROCESS_ID, target_id=COPPER_ID, type=RelationType.USES_MATERIAL,
        confidence=0.7, n_evidence=2,
        first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1,
        constraints=[constraint],
    )
    return AggregationResult(
        entities={COPPER_ID: copper, PROCESS_ID: process},
        relations={(PROCESS_ID, COPPER_ID, RelationType.USES_MATERIAL): relation},
        chunk_constraints=[(CHUNK_1, constraint)],
        mentioned_in=[(COPPER_ID, CHUNK_1), (COPPER_ID, CHUNK_2), (PROCESS_ID, CHUNK_1)],
        tech_solution_ids={PROCESS_ID},
    )


DOC_GEO = {"test_a09_doc1": Geography.RU, "test_a09_doc2": Geography.FOREIGN}


# ══════════════════════════ 1. load_entities ══════════════════════════

def test_load_entities_creates_double_label_nodes_with_provenance(driver):
    ensure_entity_constraints(driver)
    agg = _basic_agg()
    n = load_entities(driver, agg, DOC_GEO, TODAY)
    assert n == 2

    with driver.session() as session:
        copper = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=COPPER_ID).single()["e"]
        process = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=PROCESS_ID).single()["e"]
        copper_labels = session.run(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS l", id=COPPER_ID
        ).single()["l"]
        process_labels = session.run(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS l", id=PROCESS_ID
        ).single()["l"]

    assert set(copper_labels) == {"Entity", "Material"}
    assert set(process_labels) == {"Entity", "Process"}

    assert copper["name"] == COPPER_CANON
    assert copper["n_mentions"] == 2
    assert copper["doc_id"] == "test_a09_doc1"
    assert copper["chunk_id"] == CHUNK_1
    assert copper["confidence"] == ENTITY_NODE_DEFAULT_CONFIDENCE
    assert copper["updated_at"] == TODAY
    assert copper["edited_by"] == ""
    assert set(copper["synonyms"]) == {"test_a09_copper", COPPER_CANON}
    assert copper["attr_purity"] == "99.9%"
    assert copper["is_tech_solution"] is False

    assert process["is_tech_solution"] is True


def test_load_entities_sets_geography_by_majority_and_name_en(driver):
    ensure_entity_constraints(driver)
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)

    with driver.session() as session:
        copper = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=COPPER_ID).single()["e"]

    # doc_ids = {doc1(ru), doc2(foreign)} — 1 к 1, majority_geography -> unknown (ничья).
    assert copper["geography"] == Geography.UNKNOWN.value
    assert copper["name_en"] == "test_a09_copper"


def test_double_load_entities_is_idempotent(driver):
    ensure_entity_constraints(driver)
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)
    with driver.session() as session:
        first = session.run(
            "MATCH (e:Entity) WHERE e.id CONTAINS 'test-a09' RETURN count(e) AS c"
        ).single()["c"]

    load_entities(driver, agg, DOC_GEO, TODAY)
    with driver.session() as session:
        second = session.run(
            "MATCH (e:Entity) WHERE e.id CONTAINS 'test-a09' RETURN count(e) AS c"
        ).single()["c"]

    assert first == second == 2


def test_partial_reload_does_not_downgrade_n_mentions_or_tech_solution_flag(driver):
    # FIX критического бага №1 module-tester: частичный прогон (--limit)
    # поверх уже загруженной базы понизил боевой n_tech_solution 3012->3008 —
    # ON MATCH теперь монотонен (n_mentions=max, is_tech_solution=OR).
    ensure_entity_constraints(driver)
    full_agg = _basic_agg()  # process.n_mentions=1 is_tech_solution=True, copper.n_mentions=2
    load_entities(driver, full_agg, DOC_GEO, TODAY)

    # "Партиал": та же сущность, но по неполной агрегации (--limit) — меньше
    # n_mentions и БЕЗ признака tech-solution (relation, дающая его, — в чанке
    # за пределами --limit).
    partial_process = EntityAgg(
        id=PROCESS_ID, type=EntityType.PROCESS, canon="test_a09_электролиз",
        n_mentions=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1, doc_ids={"test_a09_doc1"},
    )
    partial_copper = EntityAgg(
        id=COPPER_ID, type=EntityType.MATERIAL, canon=COPPER_CANON,
        n_mentions=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1, doc_ids={"test_a09_doc1"},
    )
    partial_agg = AggregationResult(
        entities={PROCESS_ID: partial_process, COPPER_ID: partial_copper},
        relations={}, chunk_constraints=[], mentioned_in=[], tech_solution_ids=set(),
    )
    load_entities(driver, partial_agg, {}, TODAY)

    with driver.session() as session:
        process = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=PROCESS_ID).single()["e"]
        copper = session.run("MATCH (e:Entity {id: $id}) RETURN e", id=COPPER_ID).single()["e"]

    assert process["is_tech_solution"] is True  # не сброшен обратно в False
    assert copper["n_mentions"] == 2  # не понижен partial-агрегатом до 1


# ══════════════════════════ 2. load_relations ══════════════════════════

def test_load_relations_creates_edge_with_provenance_and_constraint_fields(driver):
    ensure_entity_constraints(driver)
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)
    n = load_relations(driver, agg, TODAY)
    assert n == 1

    with driver.session() as session:
        rel = session.run(
            "MATCH (:Entity {id: $s})-[r:USES_MATERIAL]->(:Entity {id: $t}) RETURN r",
            s=PROCESS_ID, t=COPPER_ID,
        ).single()["r"]

    assert rel["confidence"] == 0.7
    assert rel["n_evidence"] == 2
    assert rel["doc_id"] == "test_a09_doc1"
    assert rel["chunk_id"] == CHUNK_1
    assert rel["updated_at"] == TODAY
    assert rel["edited_by"] == ""
    assert rel["c_param"] == "test_a09_temp"
    assert rel["c_op"] == "<="
    assert rel["c_norm_value"] == 60.0
    assert rel["c_norm_unit"] == "°C"
    (constraint_json,) = rel["constraints_json"]
    assert json.loads(constraint_json)["param"] == "test_a09_temp"


def test_load_relations_without_constraints_has_empty_defaults(driver):
    ensure_entity_constraints(driver)
    other_process = EntityAgg(
        id=OTHER_PROCESS_ID, type=EntityType.PROCESS, canon="test_a09_иной_процесс",
        n_mentions=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1, doc_ids={"test_a09_doc1"},
    )
    copper = EntityAgg(
        id=COPPER_ID, type=EntityType.MATERIAL, canon=COPPER_CANON,
        n_mentions=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1, doc_ids={"test_a09_doc1"},
    )
    relation = RelationAgg(
        source_id=OTHER_PROCESS_ID, target_id=COPPER_ID, type=RelationType.DESCRIBED_IN,
        confidence=0.5, n_evidence=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1,
    )
    agg = AggregationResult(
        entities={OTHER_PROCESS_ID: other_process, COPPER_ID: copper},
        relations={(OTHER_PROCESS_ID, COPPER_ID, RelationType.DESCRIBED_IN): relation},
        chunk_constraints=[], mentioned_in=[], tech_solution_ids=set(),
    )
    load_entities(driver, agg, {}, TODAY)
    load_relations(driver, agg, TODAY)

    with driver.session() as session:
        rel = session.run(
            "MATCH (:Entity {id: $s})-[r:DESCRIBED_IN]->(:Entity {id: $t}) RETURN r",
            s=OTHER_PROCESS_ID, t=COPPER_ID,
        ).single()["r"]

    assert rel["c_param"] == ""
    assert rel["c_op"] == ""
    assert rel["c_norm_value"] is None
    assert rel["c_norm_unit"] == ""
    assert rel["constraints_json"] == []


def test_double_load_relations_is_idempotent(driver):
    ensure_entity_constraints(driver)
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)
    load_relations(driver, agg, TODAY)
    with driver.session() as session:
        first = session.run(
            "MATCH (:Entity {id: $s})-[r:USES_MATERIAL]->(:Entity {id: $t}) RETURN count(r) AS c",
            s=PROCESS_ID, t=COPPER_ID,
        ).single()["c"]

    load_relations(driver, agg, TODAY)
    with driver.session() as session:
        second = session.run(
            "MATCH (:Entity {id: $s})-[r:USES_MATERIAL]->(:Entity {id: $t}) RETURN count(r) AS c",
            s=PROCESS_ID, t=COPPER_ID,
        ).single()["c"]

    assert first == second == 1


def test_partial_reload_does_not_downgrade_relation_n_evidence_or_confidence(driver):
    # FIX критического бага №1 module-tester (тот же принцип, что для узлов) —
    # частичный прогон с менее полной агрегацией связи не должен понижать
    # уже загруженные n_evidence/confidence.
    ensure_entity_constraints(driver)
    full_agg = _basic_agg()  # relation confidence=0.7, n_evidence=2
    load_entities(driver, full_agg, DOC_GEO, TODAY)
    load_relations(driver, full_agg, TODAY)

    partial_relation = RelationAgg(
        source_id=PROCESS_ID, target_id=COPPER_ID, type=RelationType.USES_MATERIAL,
        confidence=0.3, n_evidence=1, first_doc_id="test_a09_doc1", first_chunk_id=CHUNK_1,
    )
    partial_agg = AggregationResult(
        entities=full_agg.entities,
        relations={(PROCESS_ID, COPPER_ID, RelationType.USES_MATERIAL): partial_relation},
        chunk_constraints=[], mentioned_in=[], tech_solution_ids=set(),
    )
    load_relations(driver, partial_agg, TODAY)

    with driver.session() as session:
        rel = session.run(
            "MATCH (:Entity {id: $s})-[r:USES_MATERIAL]->(:Entity {id: $t}) RETURN r",
            s=PROCESS_ID, t=COPPER_ID,
        ).single()["r"]

    assert rel["confidence"] == 0.7  # не понижен partial-агрегатом (0.3) ниже боевого
    assert rel["n_evidence"] == 2  # не понижен


# ══════════════════════════ 3. load_numeric_constraints ══════════════════════════

def test_load_numeric_constraints_creates_node_and_has_constraint_edge(driver):
    _make_test_chunks(driver, [CHUNK_1])
    c = _nc()
    n = load_numeric_constraints(driver, [(CHUNK_1, c)])
    assert n == 1

    with driver.session() as session:
        node = session.run(
            "MATCH (:Chunk {chunk_id: $cid})-[:HAS_CONSTRAINT]->(n:NumericConstraint) RETURN n", cid=CHUNK_1,
        ).single()["n"]

    assert node["id"] == constraint_node_id(CHUNK_1, c)
    assert node["param"] == "test_a09_temp"
    assert node["op"] == "<="
    assert node["norm_value"] == 60.0
    assert node["norm_unit"] == "°C"


def test_load_numeric_constraints_missing_chunk_creates_no_orphan_node(driver):
    # FIX (было КРИТИЧЕСКОЙ НАХОДКОЙ баг №1 module-tester, наблюдалось на боевых
    # данных как осиротевший узел 8073->8074): MATCH (:Chunk) теперь идёт ДО
    # MERGE узла NumericConstraint в одном запросе — если чанк не найден, ни
    # узел, ни связь не создаются вообще (не падает, просто 0 строк для MERGE).
    c = _nc()
    n = load_numeric_constraints(driver, [(CHUNK_MISSING, c)])
    assert n == 1  # функция обработала запись (счётчик обработанных, не записанных)

    with driver.session() as session:
        node_count = session.run(
            "MATCH (n:NumericConstraint {id: $id}) RETURN count(n) AS c", id=constraint_node_id(CHUNK_MISSING, c),
        ).single()["c"]

    assert node_count == 0  # сирота невозможен


def test_load_numeric_constraints_existing_chunk_still_creates_node_and_edge(driver):
    _make_test_chunks(driver, [CHUNK_1])
    c = _nc()
    n = load_numeric_constraints(driver, [(CHUNK_1, c)])
    assert n == 1

    with driver.session() as session:
        edge_count = session.run(
            "MATCH (:Chunk {chunk_id: $cid})-[:HAS_CONSTRAINT]->(n:NumericConstraint {id: $id}) RETURN count(n) AS c",
            cid=CHUNK_1, id=constraint_node_id(CHUNK_1, c),
        ).single()["c"]

    assert edge_count == 1


def test_load_numeric_constraints_duplicate_entries_collapse_to_one_node(driver):
    _make_test_chunks(driver, [CHUNK_1])
    c = _nc()
    n = load_numeric_constraints(driver, [(CHUNK_1, c), (CHUNK_1, c)])
    assert n == 2  # функция обрабатывает обе записи (счётчик — не после MERGE)

    with driver.session() as session:
        node_count = session.run(
            "MATCH (n:NumericConstraint {id: $id}) RETURN count(n) AS c", id=constraint_node_id(CHUNK_1, c),
        ).single()["c"]

    assert node_count == 1


def test_numeric_constraint_different_unit_creates_separate_nodes(driver):
    # FIX (было НАХОДКОЙ баг №2 module-tester, см. test_entity_dedup.py::
    # test_constraint_node_id_differs_by_unit): constraint_node_id теперь
    # учитывает unit — два РАЗНЫХ по смыслу ограничения с одинаковым
    # param/op/value, но разным unit, создают ДВА отдельных узла :NumericConstraint,
    # без потери данных.
    _make_test_chunks(driver, [CHUNK_1])
    c_mg = _nc(unit="мг/л", norm_unit="мг/л")
    c_kg = _nc(unit="кг", norm_unit="кг")
    assert constraint_node_id(CHUNK_1, c_mg) != constraint_node_id(CHUNK_1, c_kg)

    load_numeric_constraints(driver, [(CHUNK_1, c_mg), (CHUNK_1, c_kg)])

    with driver.session() as session:
        node_count = session.run(
            "MATCH (:Chunk {chunk_id: $cid})-[:HAS_CONSTRAINT]->(n:NumericConstraint) RETURN count(n) AS c",
            cid=CHUNK_1,
        ).single()["c"]
        units = {
            rec["n"]["norm_unit"]
            for rec in session.run(
                "MATCH (:Chunk {chunk_id: $cid})-[:HAS_CONSTRAINT]->(n:NumericConstraint) RETURN n", cid=CHUNK_1,
            )
        }

    assert node_count == 2  # два разных ограничения -> два узла в графе
    assert units == {"мг/л", "кг"}  # оба сохранены, ничего не потеряно


# ══════════════════════════ 4. load_mentioned_in ══════════════════════════

def test_load_mentioned_in_creates_edges_for_existing_pairs(driver):
    ensure_entity_constraints(driver)
    _make_test_chunks(driver, [CHUNK_1, CHUNK_2])
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)

    n = load_mentioned_in(driver, agg.mentioned_in)
    assert n == 3  # включая пару с CHUNK_2, где Chunk существует

    with driver.session() as session:
        n_edges = session.run(
            "MATCH (:Entity {id: $id})-[:MENTIONED_IN]->(c:Chunk) RETURN count(c) AS c", id=COPPER_ID,
        ).single()["c"]

    assert n_edges == 2  # CHUNK_1 и CHUNK_2


def test_load_mentioned_in_missing_chunk_or_entity_skipped_without_crash(driver):
    ensure_entity_constraints(driver)
    _make_test_chunks(driver, [CHUNK_1])
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)

    pairs = [(COPPER_ID, CHUNK_1), (COPPER_ID, CHUNK_MISSING), ("material:test-a09-no-such-entity", CHUNK_1)]
    n = load_mentioned_in(driver, pairs)
    assert n == 3  # функция не падает, даже если часть пар не резолвится

    with driver.session() as session:
        n_edges = session.run(
            "MATCH (:Entity {id: $id})-[:MENTIONED_IN]->(c:Chunk) RETURN count(c) AS c", id=COPPER_ID,
        ).single()["c"]

    assert n_edges == 1  # только реальная пара (copper, CHUNK_1)


def test_double_load_mentioned_in_is_idempotent(driver):
    ensure_entity_constraints(driver)
    _make_test_chunks(driver, [CHUNK_1, CHUNK_2])
    agg = _basic_agg()
    load_entities(driver, agg, DOC_GEO, TODAY)

    load_mentioned_in(driver, agg.mentioned_in)
    with driver.session() as session:
        first = session.run("MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS c").single()["c"]
        first_scoped = session.run(
            "MATCH (e:Entity)-[r:MENTIONED_IN]->() WHERE e.id CONTAINS 'test-a09' RETURN count(r) AS c"
        ).single()["c"]

    load_mentioned_in(driver, agg.mentioned_in)
    with driver.session() as session:
        second = session.run("MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS c").single()["c"]

    assert first == second
    assert first_scoped == 3


# ══════════════════════════ 5. ensure_entity_constraints ══════════════════════════

def test_ensure_entity_constraints_is_idempotent(driver):
    ensure_entity_constraints(driver)
    ensure_entity_constraints(driver)
    with driver.session() as session:
        names = {rec["name"] for rec in session.run("SHOW CONSTRAINTS YIELD name RETURN name")}
    assert "entity_id_unique" in names
    assert "numeric_constraint_id_unique" in names


# ══════════════════════════ 6. self_check на боевой базе ══════════════════════════

def test_self_check_on_live_db_does_not_raise_and_counts_are_consistent(driver):
    # Работает на РЕАЛЬНОЙ (боевой) базе — без тестовых данных этого модуля;
    # проверяет только внутренние ассерты self_check (сумма по label = total,
    # tech_solution <= Process). Полные боевые счётчики см. worklogs/graph.md#A-09.
    report = self_check(driver)

    assert report["n_entity_total"] > 0
    assert sum(report["n_by_label"].values()) == report["n_entity_total"]
    assert report["n_tech_solution"] <= report["n_by_label"][EntityType.PROCESS.value]
    assert set(report["n_by_label"]) == {et.value for et in EntityType}
    assert set(report["n_by_relation"]) == {rt.value.upper() for rt in RelationType}
