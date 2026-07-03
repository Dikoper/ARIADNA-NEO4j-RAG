"""Тесты extraction/postprocess.py (A-08): canonicalize_entities (синонимы ->
каноническое имя, сохранение исходного в synonyms, слияние дублей) и
filter_relations (отбраковка связей с source/target вне сущностей чанка,
EXTRACT-003 в лог).
"""
from __future__ import annotations

import logging

from ariadna.contracts import Entity, EntityType, Relation, RelationType
from ariadna.extraction.postprocess import (
    EXTRACT_RELATION_DROPPED,
    canonicalize_entities,
    filter_relations,
)


# ─── Простой логгер-шпион (интерфейс logging.LoggerAdapter.log) ─────────────
class _SpyLogger:
    def __init__(self):
        self.calls: list[dict] = []

    def log(self, level, msg, extra=None, **kwargs):
        self.calls.append({"level": level, "msg": msg, "extra": extra or {}})


# ══════════════════════ canonicalize_entities ══════════════════════

# Назначение: синоним «electrowinning» приводится к каноническому RU имени
#   «электроэкстракция» (ontology/synonyms.yaml), исходное имя добавляется
#   в synonyms результирующей сущности.
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_maps_known_synonym_to_canonical():
    entities = [Entity(name="electrowinning", type=EntityType.PROCESS, synonyms=[], attrs={})]
    result = canonicalize_entities(entities)
    assert len(result) == 1
    assert result[0].name == "электроэкстракция"
    assert "electrowinning" in result[0].synonyms


# Назначение: имя, которого нет в словаре синонимов, проходит без изменений
#   (best-effort — словарь покрывает только термины эталонных тем, не весь домен).
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_unknown_term_passthrough():
    entities = [Entity(name="Совершенно неизвестный термин XYZ", type=EntityType.MATERIAL, synonyms=[], attrs={})]
    result = canonicalize_entities(entities)
    assert len(result) == 1
    assert result[0].name == "Совершенно неизвестный термин XYZ"
    assert result[0].synonyms == []


# Назначение: уже каноническое имя не дублируется в собственных synonyms.
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_already_canonical_no_self_synonym():
    entities = [Entity(name="электроэкстракция", type=EntityType.PROCESS, synonyms=[], attrs={})]
    result = canonicalize_entities(entities)
    assert result[0].name == "электроэкстракция"
    assert "электроэкстракция" not in result[0].synonyms


# Назначение: две сущности, схлопывающиеся в одно каноническое имя (синоним +
#   каноническое имя явно в тексте) объединяются: synonyms — union без дублей,
#   attrs — union (последняя запись перекрывает совпадающие ключи), тип — первый.
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_merges_duplicates_after_canonicalization():
    entities = [
        Entity(name="электроэкстракция", type=EntityType.PROCESS, synonyms=["EW"], attrs={"geo": "ru"}),
        Entity(name="electrowinning", type=EntityType.PROCESS, synonyms=["EW"], attrs={"climate": "cold"}),
    ]
    result = canonicalize_entities(entities)
    assert len(result) == 1
    merged = result[0]
    assert merged.name == "электроэкстракция"
    assert set(merged.synonyms) == {"EW", "electrowinning"}
    assert merged.attrs == {"geo": "ru", "climate": "cold"}
    assert merged.type == EntityType.PROCESS


# Назначение: пустой список сущностей -> пустой список на выходе, без исключений.
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_empty_list():
    assert canonicalize_entities([]) == []


# Назначение: ведущие/хвостовые пробелы в имени не мешают канонизации (strip
#   перед поиском в словаре и перед сравнением с original_name).
# Уровень: ✅ реализовано (module-tester A-08)
def test_canonicalize_entities_strips_whitespace_in_name():
    entities = [Entity(name="  electrowinning  ", type=EntityType.PROCESS, synonyms=[], attrs={})]
    result = canonicalize_entities(entities)
    assert result[0].name == "электроэкстракция"


# ══════════════════════ filter_relations ══════════════════════

# Назначение: связь, чьи source/target входят в множество имён сущностей чанка,
#   сохраняется (с канонизированными source/target).
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_keeps_relation_with_known_entities():
    relations = [Relation(source="A", target="B", type=RelationType.USES_MATERIAL, confidence=0.8)]
    logger = _SpyLogger()
    result = filter_relations(relations, {"A", "B"}, logger=logger, chunk_id="c#0", doc_id="d")
    assert len(result) == 1
    assert result[0].source == "A"
    assert result[0].target == "B"
    assert logger.calls == []


# Назначение: связь с target, отсутствующим среди сущностей чанка, отбрасывается
#   и порождает WARNING-событие EXTRACT-003 с chunk_id в detail.
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_drops_relation_with_unknown_target_and_logs():
    relations = [Relation(source="A", target="НетТакойСущности", type=RelationType.USES_MATERIAL, confidence=0.8)]
    logger = _SpyLogger()
    result = filter_relations(relations, {"A"}, logger=logger, chunk_id="doc1#3", doc_id="doc1")
    assert result == []
    assert len(logger.calls) == 1
    call = logger.calls[0]
    assert call["extra"]["event"] == EXTRACT_RELATION_DROPPED
    assert call["level"] == logging.WARNING
    assert "doc1#3" in call["extra"]["detail"]
    assert "НетТакойСущности" in call["extra"]["detail"]


# Назначение: связь с неизвестным source тоже отбрасывается (не только target).
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_drops_relation_with_unknown_source():
    relations = [Relation(source="НетТакойСущности", target="A", type=RelationType.USES_MATERIAL, confidence=0.8)]
    logger = _SpyLogger()
    result = filter_relations(relations, {"A"}, logger=logger, chunk_id="c#0", doc_id="d")
    assert result == []


# Назначение: source/target связи канонизируются той же функцией, что имена
#   сущностей — синоним в связи находит каноническую сущность из entity_names.
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_canonicalizes_source_target_before_matching():
    relations = [Relation(source="electrowinning", target="медь", type=RelationType.USES_MATERIAL, confidence=0.6)]
    logger = _SpyLogger()
    # entity_names уже содержит канонический "электроэкстракция" (как после canonicalize_entities)
    result = filter_relations(relations, {"электроэкстракция", "медь"}, logger=logger, chunk_id="c#0", doc_id="d")
    assert len(result) == 1
    assert result[0].source == "электроэкстракция"
    assert result[0].target == "медь"


# Назначение: пустой список связей -> пустой список, логгер не вызывается.
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_empty_list():
    logger = _SpyLogger()
    result = filter_relations([], {"A"}, logger=logger, chunk_id="c#0", doc_id="d")
    assert result == []
    assert logger.calls == []


# Назначение: несколько связей — валидные сохраняются, невалидные отбрасываются
#   по отдельности (не всё-или-ничего для одного чанка).
# Уровень: ✅ реализовано (module-tester A-08)
def test_filter_relations_mixed_valid_and_invalid():
    relations = [
        Relation(source="A", target="B", type=RelationType.USES_MATERIAL, confidence=0.9),
        Relation(source="A", target="Ghost", type=RelationType.PRODUCES_OUTPUT, confidence=0.5),
    ]
    logger = _SpyLogger()
    result = filter_relations(relations, {"A", "B"}, logger=logger, chunk_id="c#0", doc_id="d")
    assert len(result) == 1
    assert result[0].target == "B"
    assert len(logger.calls) == 1
