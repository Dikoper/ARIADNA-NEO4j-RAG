"""Тесты A-09: `ariadna.graph.entity_dedup` — чистые функции подготовки
сущностного графа (без Neo4j). Независимая проверка контракта (module-tester,
не переписывает реализацию module-dev).

Проверяется: slugify/make_node_id (транслитерация, детерминированность,
коллизии типов, вырожденные строки), дедуп сущностей по (canon.lower(), type)
в `aggregate_from_rows` (слияние synonyms/attrs/n_mentions/provenance),
канонизация через ontology/synonyms.yaml, pick_name_en, majority_geography,
резолв концов связей внутри чанка (GRAPH-004), is_tech_solution, chunk-level
NumericConstraint (constraint_node_id), агрегация связей (confidence=max,
n_evidence).

Офлайн, без Neo4j — только чистые функции модуля + боевой ontology/synonyms.yaml.
"""
from __future__ import annotations

from ariadna.contracts import (
    CompareOp,
    Entity,
    EntityType,
    ExtractionResult,
    Geography,
    NumericConstraint,
    Relation,
    RelationType,
)
from ariadna.graph.entity_dedup import (
    aggregate_from_rows,
    constraint_node_id,
    majority_geography,
    make_node_id,
    pick_name_en,
    slugify,
)


# ─── helper ────────────────────────────────────────────────────────────
def _nc(param="temp", op=CompareOp.LE, value=60.0, unit="°C", norm_value=60.0, norm_unit="°C") -> NumericConstraint:
    return NumericConstraint(
        param=param, op=op, value=value, unit=unit,
        norm_value=norm_value, norm_unit=norm_unit, source_text=f"{param} {op.value} {value}{unit}",
    )


# ══════════════════════════ 1. slugify / make_node_id ══════════════════════════

def test_slugify_is_deterministic():
    assert slugify("Электролиз меди") == slugify("Электролиз меди")


def test_slugify_ascii_lowercased_and_hyphenated():
    assert slugify("Reverse Osmosis") == "reverse-osmosis"


def test_slugify_cyrillic_transliterated():
    assert slugify("медь") == "med"
    assert slugify("щёлочь") == "scheloch"


def test_slugify_special_chars_collapse_to_single_hyphen_no_edge_hyphens():
    assert slugify("Cu/Ni-сплав!!!") == "cu-ni-splav"


def test_slugify_underscore_becomes_hyphen():
    assert slugify("test_a09_copper") == "test-a09-copper"


def test_slugify_empty_string_falls_back_to_hash():
    slug = slugify("")
    assert slug != ""
    assert len(slug) == 12


def test_slugify_symbols_only_falls_back_to_hash_not_collapsed_to_dash():
    # "!!!" и "???" не должны схлопнуться в один и тот же слаг "-"/"" —
    # иначе разные сущности без буквенно-цифровых символов совпадут по id.
    slug_a = slugify("!!!")
    slug_b = slugify("???")
    assert slug_a != slug_b
    assert len(slug_a) == 12 and len(slug_b) == 12


def test_make_node_id_same_canon_different_type_gives_different_id():
    material_id = make_node_id(EntityType.MATERIAL, "хвосты")
    process_id = make_node_id(EntityType.PROCESS, "хвосты")
    assert material_id != process_id
    assert material_id.startswith("material:")
    assert process_id.startswith("process:")


def test_make_node_id_deterministic():
    a = make_node_id(EntityType.MATERIAL, "медь")
    b = make_node_id(EntityType.MATERIAL, "медь")
    assert a == b


# ══════════════════════════ 2. pick_name_en ══════════════════════════

def test_pick_name_en_cyrillic_canon_returns_latin_synonym():
    assert pick_name_en("электроэкстракция", ["electrowinning", "электроосаждение"]) == "electrowinning"


def test_pick_name_en_latin_canon_returns_cyrillic_synonym():
    assert pick_name_en("electrowinning", ["электроэкстракция", "EW"]) == "электроэкстракция"


def test_pick_name_en_no_opposite_script_returns_empty():
    assert pick_name_en("электроэкстракция", ["электроосаждение", "ЭЭ"]) == ""
    assert pick_name_en("electrowinning", ["EW", "e-winning"]) == ""


def test_pick_name_en_empty_pool_returns_empty():
    assert pick_name_en("медь", []) == ""


def test_pick_name_en_canon_without_letters_edge_case():
    # canon без букв (например код) — не считается «преимущественно кириллическим»,
    # функция уходит в ветку поиска кириллического синонима.
    assert pick_name_en("12345", ["медь"]) == "медь"
    assert pick_name_en("12345", []) == ""


# ══════════════════════════ 3. majority_geography ══════════════════════════

def test_majority_geography_simple_majority():
    doc_geo = {"d1": Geography.RU, "d2": Geography.RU, "d3": Geography.FOREIGN}
    assert majority_geography({"d1", "d2", "d3"}, doc_geo) == Geography.RU


def test_majority_geography_tie_returns_unknown():
    doc_geo = {"d1": Geography.RU, "d2": Geography.FOREIGN}
    assert majority_geography({"d1", "d2"}, doc_geo) == Geography.UNKNOWN


def test_majority_geography_empty_set_returns_unknown():
    assert majority_geography(set(), {}) == Geography.UNKNOWN


def test_majority_geography_missing_doc_id_defaults_to_unknown_bucket():
    # doc_id отсутствует в doc_geo (например, документ не попал в meta.jsonl) —
    # трактуется как unknown, не как исключение.
    doc_geo = {"d1": Geography.RU}
    assert majority_geography({"d1", "d_missing"}, doc_geo) == Geography.UNKNOWN


# ══════════════════════════ 4. constraint_node_id ══════════════════════════

def test_constraint_node_id_deterministic():
    c = _nc()
    assert constraint_node_id("chunk1", c) == constraint_node_id("chunk1", c)


def test_constraint_node_id_differs_by_chunk():
    c = _nc()
    assert constraint_node_id("chunk1", c) != constraint_node_id("chunk2", c)


def test_constraint_node_id_differs_by_param_op_value():
    base = _nc()
    assert constraint_node_id("c1", base) != constraint_node_id("c1", _nc(param="pH"))
    assert constraint_node_id("c1", base) != constraint_node_id("c1", _nc(op=CompareOp.GE))
    assert constraint_node_id("c1", base) != constraint_node_id("c1", _nc(value=61.0))


def test_constraint_node_id_differs_by_unit():
    # FIX (было НАХОДКОЙ баг №2 module-tester): id теперь учитывает unit —
    # два разных по смыслу ограничения ("300 мг/л" и "300 кг") с одинаковым
    # param/op/value в одном чанке больше НЕ схлопываются в один узел
    # :NumericConstraint (см. test_entity_graph_writer.py::
    # test_numeric_constraint_different_unit_creates_separate_nodes).
    c_mg = _nc(unit="мг/л", norm_unit="мг/л")
    c_kg = _nc(unit="кг", norm_unit="кг")
    assert constraint_node_id("chunk1", c_mg) != constraint_node_id("chunk1", c_kg)


def test_constraint_node_id_differs_by_value_max():
    # FIX: value_max (диапазоны вида «200–300 мг/л») теперь тоже участвует
    # в хеше — тот же param/op/value/unit с разным value_max больше не коллизит.
    base = _nc(op=CompareOp.RANGE)
    with_max = NumericConstraint(**{**base.model_dump(), "value_max": 100.0})
    assert constraint_node_id("chunk1", base) != constraint_node_id("chunk1", with_max)


# ══════════════════════════ 5. aggregate_from_rows: дедуп сущностей ══════════════════════════

def test_dedup_by_canon_lowercase_and_type_merges_mentions():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="Медь", type=EntityType.MATERIAL)],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="медь", type=EntityType.MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.entities) == 1
    (ent,) = agg.entities.values()
    assert ent.n_mentions == 2
    assert ent.doc_ids == {"d1", "d2"}


def test_dedup_by_case_alone_for_names_absent_from_ontology_dictionary():
    # node_id зависит от slugify(canon), а slugify всегда lower()-ит строку —
    # дедуп по регистру работает даже для имён, ОТСУТСТВУЮЩИХ в synonyms.yaml
    # (canon = ent.name как есть, но id всё равно совпадает).
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="Test_A09_НеизвестныйТермин", type=EntityType.MATERIAL)],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="test_a09_неизвестныйтермин", type=EntityType.MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.entities) == 1
    (ent,) = agg.entities.values()
    assert ent.n_mentions == 2


def test_dedup_same_name_different_type_creates_two_nodes():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[
                Entity(name="Хвосты", type=EntityType.MATERIAL),
                Entity(name="Хвосты", type=EntityType.PROCESS),
            ],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.entities) == 2


def test_dedup_preserves_canon_of_first_occurrence():
    # Второе упоминание другим регистром не переписывает уже сохранённый canon —
    # «provenance первого упоминания» (docs/dev/worklogs/graph.md). Имена вне
    # ontology/synonyms.yaml — иначе canon был бы нормализован словарём, а не
    # взят как есть из ent.name, и тест не проверял бы то, что заявлен.
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="Test_A09_НеизвестныйТермин", type=EntityType.MATERIAL)],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="TEST_A09_НЕИЗВЕСТНЫЙТЕРМИН", type=EntityType.MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    assert ent.canon == "Test_A09_НеизвестныйТермин"


def test_dedup_first_doc_and_chunk_id_from_first_row_only():
    rows = [
        ExtractionResult(doc_id="d1", chunk_id="d1#0", entities=[Entity(name="медь", type=EntityType.MATERIAL)]),
        ExtractionResult(doc_id="d2", chunk_id="d2#0", entities=[Entity(name="медь", type=EntityType.MATERIAL)]),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    assert (ent.first_doc_id, ent.first_chunk_id) == ("d1", "d1#0")


def test_dedup_merges_synonyms_as_union_without_duplicates():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="медь", type=EntityType.MATERIAL, synonyms=["copper"])],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="медь", type=EntityType.MATERIAL, synonyms=["Cu", "copper"])],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    # Синонимы + исходное имя (ent.name) каждой строки добавляются в пул один раз.
    assert ent.synonyms.count("copper") == 1
    assert "Cu" in ent.synonyms
    assert "медь" in ent.synonyms


def test_dedup_attrs_first_value_wins_on_conflict():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="медь", type=EntityType.MATERIAL, attrs={"purity": "99.9%"})],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="медь", type=EntityType.MATERIAL, attrs={"purity": "99.0%", "grade": "A"})],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    assert ent.attrs == {"purity": "99.9%", "grade": "A"}


# ══════════════════════════ 6. Канонизация через ontology/synonyms.yaml ══════════════════════════

def test_known_synonym_canonicalizes_to_ontology_canonical_name():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="electrowinning", type=EntityType.PROCESS)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    assert ent.canon == "электроэкстракция"
    assert ent.id == make_node_id(EntityType.PROCESS, "электроэкстракция")


def test_unknown_name_falls_back_to_original_as_canon():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="test_a09_совершенно неизвестный термин", type=EntityType.MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ent,) = agg.entities.values()
    assert ent.canon == "test_a09_совершенно неизвестный термин"


def test_canonicalization_merges_synonym_spelling_with_canonical_spelling():
    # "electrowinning" (синоним) и "электроэкстракция" (канон) в разных чанках —
    # должны схлопнуться в ОДИН узел.
    rows = [
        ExtractionResult(doc_id="d1", chunk_id="d1#0", entities=[Entity(name="electrowinning", type=EntityType.PROCESS)]),
        ExtractionResult(doc_id="d2", chunk_id="d2#0", entities=[Entity(name="электроэкстракция", type=EntityType.PROCESS)]),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.entities) == 1
    (ent,) = agg.entities.values()
    assert ent.n_mentions == 2


# ══════════════════════════ 7. Резолв концов связей внутри чанка (GRAPH-004) ══════════════════════════

def test_relation_resolves_between_entities_of_same_chunk():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[
                Entity(name="электролиз", type=EntityType.PROCESS),
                Entity(name="медь", type=EntityType.MATERIAL),
            ],
            relations=[Relation(source="электролиз", target="медь", type=RelationType.USES_MATERIAL, confidence=0.8)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.relations) == 1


def test_relation_with_missing_endpoint_triggers_warning_and_is_dropped():
    warnings = []
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="электролиз", type=EntityType.PROCESS)],
            relations=[Relation(source="электролиз", target="несуществующая сущность", type=RelationType.USES_MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {}, on_warning=lambda doc_id, chunk_id, rel: warnings.append((doc_id, chunk_id, rel)))
    assert agg.relations == {}
    assert len(warnings) == 1
    doc_id, chunk_id, rel = warnings[0]
    assert (doc_id, chunk_id) == ("d1", "d1#0")
    assert rel.target == "несуществующая сущность"


def test_relation_endpoint_must_be_mentioned_in_same_chunk_not_elsewhere_in_corpus():
    # "медь" упомянута в чанке d2#0, но НЕ в d1#0 — связь в d1#0, ссылающаяся
    # на "медь", не должна резолвиться через сущность из другого чанка.
    warnings = []
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="электролиз", type=EntityType.PROCESS)],
            relations=[Relation(source="электролиз", target="медь", type=RelationType.USES_MATERIAL)],
        ),
        ExtractionResult(doc_id="d2", chunk_id="d2#0", entities=[Entity(name="медь", type=EntityType.MATERIAL)]),
    ]
    agg = aggregate_from_rows(rows, {}, on_warning=lambda *a: warnings.append(a))
    assert agg.relations == {}
    assert len(warnings) == 1


def test_relation_endpoint_name_collision_within_chunk_resolves_to_first_entity_and_warns():
    # FIX (было НАХОДКОЙ баг №3 module-tester): local_map внутри чанка ключевался
    # ТОЛЬКО по name.lower(), без типа — второй Entity того же имени молча
    # перезаписывал первого. Теперь резолв идёт через кандидатов по имени
    # (без потери ни одного) — при нескольких кандидатах берётся ПЕРВЫЙ по
    # порядку появления в чанке, и вызывается on_ambiguous_endpoint (GRAPH-005,
    # связь не теряется).
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[
                Entity(name="хвосты", type=EntityType.MATERIAL),
                Entity(name="хвосты", type=EntityType.PROCESS),
            ],
            relations=[Relation(source="хвосты", target="хвосты", type=RelationType.DESCRIBED_IN)],
        ),
    ]
    ambiguous = []
    agg = aggregate_from_rows(
        rows, {}, on_ambiguous_endpoint=lambda doc_id, chunk_id, name, candidates: ambiguous.append(
            (doc_id, chunk_id, name, candidates)
        ),
    )
    assert len(agg.relations) == 1
    ((source_id, target_id, _rtype),) = agg.relations.keys()
    material_id = make_node_id(EntityType.MATERIAL, "хвосты")
    assert source_id == material_id
    assert target_id == material_id
    # source и target обе неоднозначны -> 2 предупреждения (по одному на конец).
    assert len(ambiguous) == 2
    doc_id, chunk_id, name, candidates = ambiguous[0]
    assert (doc_id, chunk_id, name) == ("d1", "d1#0", "хвосты")
    assert len(candidates) == 2


def test_relation_endpoint_single_candidate_does_not_trigger_ambiguous_callback():
    ambiguous = []
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[
                Entity(name="электролиз", type=EntityType.PROCESS),
                Entity(name="медь", type=EntityType.MATERIAL),
            ],
            relations=[Relation(source="электролиз", target="медь", type=RelationType.USES_MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(
        rows, {}, on_ambiguous_endpoint=lambda *a: ambiguous.append(a),
    )
    assert len(agg.relations) == 1
    assert ambiguous == []


# ══════════════════════════ 8. is_tech_solution ══════════════════════════

def test_process_source_of_uses_material_is_tech_solution():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    process_id = make_node_id(EntityType.PROCESS, "плавка")
    assert process_id in agg.tech_solution_ids


def test_process_source_of_produces_output_is_tech_solution():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="штейн", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="штейн", type=RelationType.PRODUCES_OUTPUT)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    process_id = make_node_id(EntityType.PROCESS, "плавка")
    assert process_id in agg.tech_solution_ids


def test_process_source_of_operates_at_condition_is_tech_solution():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="температура", type=EntityType.PROPERTY)],
            relations=[Relation(source="плавка", target="температура", type=RelationType.OPERATES_AT_CONDITION)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    process_id = make_node_id(EntityType.PROCESS, "плавка")
    assert process_id in agg.tech_solution_ids


def test_process_without_tech_relations_is_not_tech_solution():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="статья", type=EntityType.PUBLICATION)],
            relations=[Relation(source="плавка", target="статья", type=RelationType.DESCRIBED_IN)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    process_id = make_node_id(EntityType.PROCESS, "плавка")
    assert process_id not in agg.tech_solution_ids
    assert agg.tech_solution_ids == set()


def test_process_with_no_relations_at_all_is_not_tech_solution():
    rows = [ExtractionResult(doc_id="d1", chunk_id="d1#0", entities=[Entity(name="плавка", type=EntityType.PROCESS)])]
    agg = aggregate_from_rows(rows, {})
    assert agg.tech_solution_ids == set()


def test_non_process_source_of_tech_relation_type_never_marked():
    # Если (гипотетически, LLM-ошибка извлечения) source связи uses_material —
    # НЕ Process, узел не должен попасть в tech_solution_ids.
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="руда", type=EntityType.MATERIAL), Entity(name="концентрат", type=EntityType.MATERIAL)],
            relations=[Relation(source="руда", target="концентрат", type=RelationType.USES_MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    assert agg.tech_solution_ids == set()


# ══════════════════════════ 9. Агрегация связей по (source_id, target_id, type) ══════════════════════════

def test_relation_confidence_is_max_across_occurrences():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL, confidence=0.3)],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL, confidence=0.9)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    ((ragg),) = agg.relations.values()
    assert ragg.confidence == 0.9


def test_relation_n_evidence_counts_occurrences():
    rows = [
        ExtractionResult(
            doc_id=f"d{i}", chunk_id=f"d{i}#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL)],
        )
        for i in range(3)
    ]
    agg = aggregate_from_rows(rows, {})
    (ragg,) = agg.relations.values()
    assert ragg.n_evidence == 3


def test_relation_first_provenance_is_earliest_occurrence():
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL)],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="руда", type=EntityType.MATERIAL)],
            relations=[Relation(source="плавка", target="руда", type=RelationType.USES_MATERIAL)],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ragg,) = agg.relations.values()
    assert (ragg.first_doc_id, ragg.first_chunk_id) == ("d1", "d1#0")


def test_relation_constraints_merged_across_occurrences_with_dedup():
    # FIX (было НАХОДКОЙ баг №4 module-tester): RelationAgg.constraints раньше
    # заполнялся ТОЛЬКО при создании агрегата (первое появление ключа) — теперь
    # constraints ВСЕХ вхождений объединяются (union), с дедупом по содержанию
    # (param/op/value/value_max/unit) — повтор того же constraint в другом
    # чанке не даёт дубликат в списке.
    c_first = _nc(param="temp", value=60.0)
    c_second = _nc(param="pH", value=7.0)
    rows = [
        ExtractionResult(
            doc_id="d1", chunk_id="d1#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="температура", type=EntityType.PROPERTY)],
            relations=[Relation(
                source="плавка", target="температура", type=RelationType.OPERATES_AT_CONDITION,
                constraints=[c_first],
            )],
        ),
        ExtractionResult(
            doc_id="d2", chunk_id="d2#0",
            entities=[Entity(name="плавка", type=EntityType.PROCESS), Entity(name="температура", type=EntityType.PROPERTY)],
            relations=[Relation(
                source="плавка", target="температура", type=RelationType.OPERATES_AT_CONDITION,
                constraints=[c_second, c_first],  # c_first повторно — не должен задублироваться
            )],
        ),
    ]
    agg = aggregate_from_rows(rows, {})
    (ragg,) = agg.relations.values()
    assert ragg.n_evidence == 2
    assert [c.param for c in ragg.constraints] == ["temp", "pH"]


# ══════════════════════════ 10. MENTIONED_IN и chunk-level constraints ══════════════════════════

def test_mentioned_in_pairs_are_unique_and_sorted():
    rows = [
        ExtractionResult(doc_id="d1", chunk_id="d1#0", entities=[Entity(name="медь", type=EntityType.MATERIAL)]),
        ExtractionResult(doc_id="d1", chunk_id="d1#0", entities=[Entity(name="медь", type=EntityType.MATERIAL)]),
    ]
    agg = aggregate_from_rows(rows, {})
    assert agg.mentioned_in == sorted(set(agg.mentioned_in))
    assert len(agg.mentioned_in) == 1


def test_chunk_constraints_collected_per_row_without_dedup():
    # aggregate_from_rows НЕ дедуплицирует chunk_constraints сам по себе —
    # схлопывание одинаковых constraint_node_id происходит позже, на MERGE
    # в entity_graph_writer.load_numeric_constraints (Neo4j-слой).
    c = _nc()
    rows = [
        ExtractionResult(doc_id="d1", chunk_id="d1#0", constraints=[c]),
        ExtractionResult(doc_id="d1", chunk_id="d1#0", constraints=[c]),
    ]
    agg = aggregate_from_rows(rows, {})
    assert len(agg.chunk_constraints) == 2
    ids = {constraint_node_id(chunk_id, cc) for chunk_id, cc in agg.chunk_constraints}
    assert len(ids) == 1  # оба элемента дадут один и тот же id при загрузке
