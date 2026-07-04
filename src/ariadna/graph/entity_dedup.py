"""Чистые функции подготовки сущностного графа: дедуп сущностей по каноническому
имени, детерминированный id узла, выбор name_en, гео-признак узла по большинству,
однопроходная агрегация ExtractionResult в узлы/связи графа (без Neo4j).

Вход: `contracts.ExtractionResult` (построчно из `data/processed/extracted_haiku.jsonl`),
`contracts.Geography` по doc_id (из meta.jsonl, читает entity_loader). Выход:
`AggregationResult` — агрегаты узлов (`EntityAgg`) и связей (`RelationAgg`),
provenance до чанка (`mentioned_in`-пары), chunk-level `NumericConstraint`,
множество id узлов Process, отмеченных как хаб TechSolution.
Зависимости: `ariadna.contracts`, `ariadna.graph.ontology.canonical_name`.
Инвариант: не пишет в Neo4j (это делает `entity_loader.py`) — чистые функции,
пригодные для юнит-тестов без БД. Паспорт: docs/dev/modules/graph.md (A-09).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ariadna.contracts import (
    EntityType,
    ExtractionResult,
    Geography,
    NumericConstraint,
    Relation,
    RelationType,
)
from ariadna.graph.ontology import canonical_name

# Типы связей, для которых Process-источник считается «техническим решением»
# (хаб TechSolution — ARCHITECTURE.md, не отдельный EntityType, только роль).
TECH_SOLUTION_RELATION_TYPES = frozenset({
    RelationType.USES_MATERIAL,
    RelationType.PRODUCES_OUTPUT,
    RelationType.OPERATES_AT_CONDITION,
})

_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")

# Упрощённая транслитерация кириллицы для slug — не для отображения, только id.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


# ─── slugify ───────────────────────────────────────────────────────────
# Назначение: детерминированный, URL/Cypher-безопасный слаг строки —
#   транслитерация кириллицы + только [a-z0-9-], без пустого результата.
# Входные связи: произвольная строка (Entity.name/канон)
# Выходные данные: str — непустой слаг
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def slugify(text: str) -> str:
    lowered = text.strip().lower()
    transliterated = "".join(_TRANSLIT.get(ch, ch) for ch in lowered)
    slug = _SLUG_INVALID_RE.sub("-", transliterated).strip("-")
    if not slug:
        # Вырожденный случай (имя без букв/цифр после очистки) — хеш вместо пустоты,
        # иначе разные сущности схлопнутся в один узел с id "<type>:".
        slug = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    return slug


# ─── make_node_id ────────────────────────────────────────────────────────
# Назначение: строит уникальный детерминированный id узла "<type>:<slug>" —
#   зависит только от (type, canon), не от порядка агрегации по корпусу.
# Входные связи: contracts.EntityType, каноническое имя (canonical_name или исходное)
# Выходные данные: str
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def make_node_id(entity_type: EntityType, canon: str) -> str:
    return f"{entity_type.value.lower()}:{slugify(canon)}"


# Назначение: (число латинских букв, число кириллических букв) в строке.
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def _script_counts(text: str) -> tuple[int, int]:
    return len(_LATIN_RE.findall(text)), len(_CYRILLIC_RE.findall(text))


# Назначение: True, если латинских букв в строке строго больше, чем кириллических.
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def _is_mostly_latin(text: str) -> bool:
    n_latin, n_cyr = _script_counts(text)
    return n_latin > 0 and n_latin > n_cyr


# Назначение: True, если кириллических букв в строке не меньше латинских (и есть хотя бы одна).
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def _is_mostly_cyrillic(text: str) -> bool:
    n_latin, n_cyr = _script_counts(text)
    return n_cyr > 0 and n_cyr >= n_latin


# ─── pick_name_en ────────────────────────────────────────────────────────
# Назначение: name_en — первый синоним из пула, состоящий в основном из
#   латиницы, если каноническое имя кириллическое, и наоборот (первый
#   преимущественно кириллический синоним — если имя латинское/смешанное).
# Входные связи: каноническое имя узла, пул синонимов (включая исходный
#   Entity.name — см. aggregate_from_rows)
# Выходные данные: str — найденный синоним или "" (противоположный вариант не найден)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def pick_name_en(canon: str, synonym_pool: list[str]) -> str:
    if _is_mostly_cyrillic(canon):
        for syn in synonym_pool:
            if _is_mostly_latin(syn):
                return syn
        return ""
    for syn in synonym_pool:
        if _is_mostly_cyrillic(syn):
            return syn
    return ""


# ─── majority_geography ──────────────────────────────────────────────────
# Назначение: гео-признак узла — большинство среди Geography документов,
#   в которых сущность упоминалась; при равенстве самых частых значений —
#   unknown (нет надёжного большинства).
# Входные связи: множество doc_id упоминаний сущности; dict doc_id->Geography (meta.jsonl)
# Выходные данные: contracts.Geography
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def majority_geography(doc_ids: set[str], doc_geo: dict[str, Geography]) -> Geography:
    counts = Counter(doc_geo.get(d, Geography.UNKNOWN) for d in doc_ids)
    if not counts:
        return Geography.UNKNOWN
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return Geography.UNKNOWN
    return ranked[0][0]


# ─── constraint_node_id ───────────────────────────────────────────────────
# Назначение: детерминированный id узла :NumericConstraint — хеш chunk_id +
#   param/op/value/value_max/unit, чтобы повторный запуск MERGE-ил тот же узел
#   (идемпотентность). unit и value_max ОБЯЗАТЕЛЬНЫ в хеше (module-tester A-09,
#   worklogs/graph.md): без них «300 мг/л» и «300 кг» в одном чанке (одинаковые
#   param/op/value, разный unit) схлопывались в один узел, поздняя запись молча
#   затирала раннюю (silent data loss).
# Входные связи: chunk_id, contracts.NumericConstraint (chunk-level, из regex-нормализатора)
# Выходные данные: str (16 hex-символов)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def constraint_node_id(chunk_id: str, constraint: NumericConstraint) -> str:
    raw = (
        f"{chunk_id}|{constraint.param}|{constraint.op.value}|{constraint.value}|"
        f"{constraint.value_max}|{constraint.unit}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class EntityAgg:
    """Агрегат одной канонической сущности по всему корпусу (до записи в Neo4j)."""

    id: str
    type: EntityType
    canon: str
    attrs: dict[str, str] = field(default_factory=dict)
    n_mentions: int = 0
    first_doc_id: str = ""
    first_chunk_id: str = ""
    doc_ids: set[str] = field(default_factory=set)
    synonyms: list[str] = field(default_factory=list)


@dataclass
class RelationAgg:
    """Агрегат одной связи (source_id, target_id, type) по всему корпусу."""

    source_id: str
    target_id: str
    type: RelationType
    confidence: float = 0.0
    n_evidence: int = 0
    first_doc_id: str = ""
    first_chunk_id: str = ""
    constraints: list[NumericConstraint] = field(default_factory=list)


@dataclass
class AggregationResult:
    """Результат однопроходной агрегации корпуса ExtractionResult."""

    entities: dict[str, EntityAgg]
    relations: dict[tuple[str, str, RelationType], RelationAgg]
    chunk_constraints: list[tuple[str, NumericConstraint]]
    mentioned_in: list[tuple[str, str]]
    tech_solution_ids: set[str]


# Назначение: ключ дедупликации NumericConstraint по содержанию (не по
#   происхождению) — используется при слиянии constraints одной связи между
#   разными вхождениями в корпусе (aggregate_from_rows).
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def _constraint_content_key(c: NumericConstraint) -> tuple:
    return (c.param, c.op.value, c.value, c.value_max, c.unit)


# ─── _resolve_endpoint ───────────────────────────────────────────────────
# Назначение: резолвит имя конца связи (source/target) в id узла среди
#   кандидатов ТОГО ЖЕ чанка. Тип конца связи неизвестен (Relation несёт
#   только имя) — кандидаты собраны по имени независимо от типа. Ровно один
#   кандидат — берётся он; несколько (два разных Entity одного чанка с
#   совпадающим по регистру именем, но разными EntityType) — берётся первый
#   по порядку появления в чанке, вызывается on_ambiguous (GRAPH-005,
#   docs/dev/ERRORS.md) — связь НЕ теряется (в отличие от GRAPH-004, где
#   кандидатов нет вообще).
# Входные связи: имя конца связи, name_candidates (name.lower() -> id узлов
#   чанка в порядке первого появления), doc_id/chunk_id — для логирования
# Выходные данные: str | None — id узла или None (кандидатов нет вообще)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def _resolve_endpoint(
    name: str,
    name_candidates: dict[str, list[str]],
    doc_id: str,
    chunk_id: str,
    on_ambiguous: Callable[[str, str, str, list[str]], None] | None,
) -> str | None:
    candidates = name_candidates.get(name.strip().lower())
    if not candidates:
        return None
    if len(candidates) > 1 and on_ambiguous is not None:
        on_ambiguous(doc_id, chunk_id, name, candidates)
    return candidates[0]


# ─── aggregate_from_rows ───────────────────────────────────────────────────
# Назначение: единый проход по ExtractionResult корпуса — дедуп сущностей по
#   (canon.lower(), type) через make_node_id, агрегация связей по (source_id,
#   target_id, type), provenance до первого упоминания, MENTIONED_IN-пары,
#   chunk-level NumericConstraint, разметка tech-solution узлов Process
#   (Process — source хотя бы одной uses_material/produces_output/
#   operates_at_condition). Связь, чей source/target не резолвится СРЕДИ
#   сущностей того же чанка вообще — GRAPH-004 через on_warning, пропускается;
#   резолв конца связи по имени — через _resolve_endpoint (фолбэк на
#   неоднозначность нескольких типов с тем же именем — GRAPH-005 через
#   on_ambiguous_endpoint, связь не теряется). Constraints связи — объединение
#   (union с дедупом по содержанию) по ВСЕМ вхождениям (source_id, target_id,
#   type) в корпусе, не только по первому.
# Входные связи: contracts.ExtractionResult (Iterable — один проход по jsonl),
#   doc_geo: dict[doc_id, Geography] (meta.jsonl); on_warning(doc_id, chunk_id,
#   relation) — коллбек GRAPH-004; on_ambiguous_endpoint(doc_id, chunk_id, name,
#   candidate_ids) — коллбек GRAPH-005 (логирование — забота вызывающего)
# Выходные данные: AggregationResult — без обращения к Neo4j
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def aggregate_from_rows(
    rows: Iterable[ExtractionResult],
    doc_geo: dict[str, Geography],
    on_warning: Callable[[str, str, Relation], None] | None = None,
    on_ambiguous_endpoint: Callable[[str, str, str, list[str]], None] | None = None,
) -> AggregationResult:
    entities: dict[str, EntityAgg] = {}
    relations: dict[tuple[str, str, RelationType], RelationAgg] = {}
    chunk_constraints: list[tuple[str, NumericConstraint]] = []
    mentioned_in: set[tuple[str, str]] = set()

    for row in rows:
        # Кандидаты резолва концов связи: имя(lower) -> id узлов этого чанка,
        # в порядке первого появления (см. _resolve_endpoint).
        name_candidates: dict[str, list[str]] = {}
        for ent in row.entities:
            canon = canonical_name(ent.name) or ent.name
            node_id = make_node_id(ent.type, canon)
            ids_for_name = name_candidates.setdefault(ent.name.strip().lower(), [])
            if node_id not in ids_for_name:
                ids_for_name.append(node_id)

            agg = entities.get(node_id)
            if agg is None:
                agg = EntityAgg(id=node_id, type=ent.type, canon=canon)
                entities[node_id] = agg
            agg.n_mentions += 1
            agg.doc_ids.add(row.doc_id)
            if not agg.first_doc_id:
                agg.first_doc_id = row.doc_id
                agg.first_chunk_id = row.chunk_id
            for k, v in ent.attrs.items():
                agg.attrs.setdefault(k, v)  # первое значение выигрывает при конфликте
            for syn in (*ent.synonyms, ent.name):
                syn = syn.strip()
                if syn and syn not in agg.synonyms:
                    agg.synonyms.append(syn)
            mentioned_in.add((node_id, row.chunk_id))

        for rel in row.relations:
            source_id = _resolve_endpoint(rel.source, name_candidates, row.doc_id, row.chunk_id, on_ambiguous_endpoint)
            target_id = _resolve_endpoint(rel.target, name_candidates, row.doc_id, row.chunk_id, on_ambiguous_endpoint)
            if source_id is None or target_id is None:
                if on_warning is not None:
                    on_warning(row.doc_id, row.chunk_id, rel)
                continue
            key = (source_id, target_id, rel.type)
            ragg = relations.get(key)
            if ragg is None:
                ragg = RelationAgg(
                    source_id=source_id, target_id=target_id, type=rel.type,
                    first_doc_id=row.doc_id, first_chunk_id=row.chunk_id,
                    constraints=list(rel.constraints),
                )
                relations[key] = ragg
            else:
                existing_keys = {_constraint_content_key(c) for c in ragg.constraints}
                for c in rel.constraints:
                    ck = _constraint_content_key(c)
                    if ck not in existing_keys:
                        ragg.constraints.append(c)
                        existing_keys.add(ck)
            ragg.n_evidence += 1
            ragg.confidence = max(ragg.confidence, rel.confidence)

        for c in row.constraints:
            chunk_constraints.append((row.chunk_id, c))

    tech_solution_ids = {
        ragg.source_id
        for ragg in relations.values()
        if ragg.type in TECH_SOLUTION_RELATION_TYPES
        and entities[ragg.source_id].type == EntityType.PROCESS
    }

    return AggregationResult(
        entities=entities,
        relations=relations,
        chunk_constraints=chunk_constraints,
        mentioned_in=sorted(mentioned_in),
        tech_solution_ids=tech_solution_ids,
    )
