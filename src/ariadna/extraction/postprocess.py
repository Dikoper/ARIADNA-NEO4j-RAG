"""Пост-обработка сырого извлечения LLM — extraction/postprocess.py (A-08).

Вход: `contracts.Entity`/`contracts.Relation`, как их вернула LLM (llm_extract.py,
уже провалидированы pydantic-схемой, но имена не канонизированы и связи не
проверены на согласованность с списком сущностей чанка). Выход: те же модели
после канонизации имён (`graph.ontology.canonical_name`) и отбраковки связей,
чьи source/target не входят в сущности чанка.
Зависимости: `ariadna.graph.ontology.canonical_name` (словарь ontology/
synonyms.yaml), `ariadna.logutil` (WARNING-события EXTRACT-003), contracts.py.
Инвариант: не обращается к сети/LLM — чистая пост-обработка уже полученного
ответа модели.
Паспорт: docs/dev/modules/extraction.md.
"""
from __future__ import annotations

from ariadna.contracts import Entity, Relation
from ariadna.graph.ontology import canonical_name
from ariadna.logutil import log_event

# Связь отброшена: source/target не входят в сущности чанка — docs/dev/ERRORS.md.
EXTRACT_RELATION_DROPPED = "EXTRACT-003"


# Назначение: каноническое имя термина (ontology/synonyms.yaml) или исходное
#   имя без изменений, если термина нет в словаре синонимов (best-effort —
#   словарь покрывает только термины 4 эталонных тем, не весь домен).
# Уровень: ✅ реализовано (A-08)
def _canon_or_original(name: str) -> str:
    canon = canonical_name(name)
    return canon if canon else name


# ─── canonicalize_entities ──────────────────────────────────────────────
# Назначение: приводит имена сущностей к каноническим (ontology/synonyms.yaml);
#   при замене добавляет исходное имя в synonyms; сущности, схлопнувшиеся
#   в одно каноническое имя, объединяет (synonyms/attrs — union, тип — первый).
# Входные связи: list[contracts.Entity] — сырые сущности одного чанка от LLM
# Выходные данные: list[contracts.Entity] — канонизированные, без дублей по name
# Уровень: ✅ реализовано (A-08)
def canonicalize_entities(entities: list[Entity]) -> list[Entity]:
    merged: dict[str, Entity] = {}
    for entity in entities:
        original_name = entity.name.strip()
        canon = _canon_or_original(original_name)
        synonyms = list(entity.synonyms)
        if canon != original_name and original_name not in synonyms:
            synonyms.append(original_name)

        if canon in merged:
            existing = merged[canon]
            merged_synonyms = list(existing.synonyms)
            for syn in synonyms:
                if syn not in merged_synonyms:
                    merged_synonyms.append(syn)
            merged_attrs = {**existing.attrs, **entity.attrs}
            merged[canon] = existing.model_copy(update={"synonyms": merged_synonyms, "attrs": merged_attrs})
        else:
            merged[canon] = entity.model_copy(update={"name": canon, "synonyms": synonyms})
    return list(merged.values())


# ─── filter_relations ────────────────────────────────────────────────────
# Назначение: канонизирует source/target связи той же функцией, что и имена
#   сущностей, затем отбрасывает связи, чьи source/target не входят в множество
#   имён сущностей чанка (после канонизации) — WARNING EXTRACT-003 в лог.
# Входные связи: list[contracts.Relation] от LLM; entity_names — имена из
#   canonicalize_entities(...) того же чанка; логгер/run_id/chunk_id для события
# Выходные данные: list[contracts.Relation] — только связи с известными узлами
# Уровень: ✅ реализовано (A-08)
def filter_relations(
    relations: list[Relation],
    entity_names: set[str],
    *,
    logger,
    chunk_id: str,
    doc_id: str,
) -> list[Relation]:
    kept: list[Relation] = []
    for relation in relations:
        source = _canon_or_original(relation.source.strip())
        target = _canon_or_original(relation.target.strip())
        if source not in entity_names or target not in entity_names:
            log_event(
                logger, stage="extraction", event=EXTRACT_RELATION_DROPPED, level="WARNING",
                doc_id=doc_id,
                detail=f"chunk_id={chunk_id} type={relation.type.value} source={source!r} target={target!r} "
                       f"— нет среди сущностей чанка",
            )
            continue
        kept.append(relation.model_copy(update={"source": source, "target": target}))
    return kept
