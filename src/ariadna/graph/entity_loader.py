"""CLI-точка входа загрузки сущностного графа (8 типов узлов / 6 типов связей)
в Neo4j из `data/processed/extracted_haiku.jsonl`.

Вход: `contracts.ExtractionResult` (JSONL, построчно на чанк), `contracts.
DocumentMeta.geography` (`data/processed/meta.jsonl`). Выход: узлы `:Entity`
+ `:<EntityType>` с provenance (У-4: doc_id/chunk_id/confidence/updated_at/
edited_by), связи `:<RELATION_TYPE>` с constraints_json/c_param/c_op/
c_norm_value/c_norm_unit, узлы `:NumericConstraint` (chunk-level regex-
constraints) + `(:Chunk)-[:HAS_CONSTRAINT]->(:NumericConstraint)`, provenance
до чанка `(:Entity)-[:MENTIONED_IN]->(:Chunk)`, хаб TechSolution — свойство
`is_tech_solution` на узлах `:Process`. Чтение/агрегация — `graph.entity_dedup`
(чистые функции), запись в Neo4j — `graph.entity_graph_writer`.
Зависимости: `ariadna.contracts`, `ariadna.graph.entity_dedup`,
`ariadna.graph.entity_graph_writer`, `ariadna.graph.lexical_loader.get_driver`,
`ariadna.graph.config`, `ariadna.logutil`.
Инвариант: единственный модуль с правом записи в Neo4j (docs/dev/modules/
graph.md); не трогает существующие Document/Chunk/CatalogEntry — только
MERGE новых узлов/связей поверх них.
Точка входа: `python -m ariadna.graph.entity_loader [--input PATH] [--limit N]`.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from ariadna.contracts import DocumentMeta, ExtractionResult, Geography, Relation
from ariadna.graph.config import DEFAULT_EXTRACTED_PATH, DEFAULT_META_PATH
from ariadna.graph.entity_dedup import aggregate_from_rows
from ariadna.graph.entity_graph_writer import (
    ensure_entity_constraints,
    load_entities,
    load_mentioned_in,
    load_numeric_constraints,
    load_relations,
    self_check,
)
from ariadna.graph.lexical_loader import get_driver
from ariadna.logutil import get_logger, log_event, new_run_id

# Связь пропущена — source/target не резолвится среди сущностей того же чанка
# (после канонизации имён) — docs/dev/ERRORS.md.
GRAPH_RELATION_ENDPOINT_MISSING = "GRAPH-004"
# Конец связи резолвлен неоднозначно — несколько сущностей чанка совпали по
# имени, но разного типа; связь НЕ теряется (берётся первая по порядку
# появления в чанке) — docs/dev/ERRORS.md.
GRAPH_RELATION_ENDPOINT_AMBIGUOUS = "GRAPH-005"


# ─── _iter_extraction_results ────────────────────────────────────────────
# Назначение: построчно читает ExtractionResult из jsonl (опциональный лимит
#   строк для смоук-прогона).
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def _iter_extraction_results(path: Path, limit: int | None = None):
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            yield ExtractionResult.model_validate_json(line)


# Назначение: dict doc_id -> Geography из meta.jsonl — для гео-признака узлов.
# Уровень: ✅ реализовано (A-09, worklogs/graph.md)
def _load_doc_geography(meta_path: Path) -> dict[str, Geography]:
    result: dict[str, Geography] = {}
    with meta_path.open(encoding="utf-8") as f:
        for line in f:
            meta = DocumentMeta.model_validate_json(line)
            result[meta.doc_id] = meta.geography
    return result


# ─── main ───────────────────────────────────────────────────────────────────
# Назначение: CLI полной загрузки сущностного графа; перезапускаемо (MERGE
#   идемпотентен) — повторный запуск не увеличивает счётчики self_check.
#   Частичный прогон (--limit) поверх уже полностью загруженной базы также
#   безопасен — entity_graph_writer обновляет существующие узлы/связи
#   монотонно (ON MATCH SET), не деградирует n_mentions/confidence/
#   is_tech_solution/n_evidence (fix module-dev A-09 багрепорта).
# Входные связи: аргументы --input/--meta-path/--limit
# Выходные данные: нет (побочный эффект — Neo4j + печать JSON-отчёта в stdout)
# Уровень: ✅ реализовано (A-09, worklogs/graph.md; fix module-dev A-09 багрепорта)
def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка сущностного графа (A-09) в Neo4j")
    parser.add_argument("--input", type=Path, default=DEFAULT_EXTRACTED_PATH)
    parser.add_argument("--meta-path", type=Path, default=DEFAULT_META_PATH)
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число строк (смоук-прогон)")
    args = parser.parse_args()

    run_id = new_run_id("graph_entity_")
    logger = get_logger("graph", run_id)
    today_iso = date.today().isoformat()
    n_warnings = 0
    n_ambiguous = 0

    # Назначение: коллбек GRAPH-004 — считает и логирует связи, чей source/target
    #   не резолвится среди сущностей того же чанка (aggregate_from_rows).
    # Уровень: ✅ реализовано (A-09, worklogs/graph.md)
    def _on_warning(doc_id: str, chunk_id: str, rel: Relation) -> None:
        nonlocal n_warnings
        n_warnings += 1
        log_event(
            logger, stage="entity_load", event=GRAPH_RELATION_ENDPOINT_MISSING, level="WARNING",
            doc_id=doc_id,
            detail=(
                f"chunk_id={chunk_id} rel={rel.type.value} source={rel.source!r} "
                f"target={rel.target!r} — конец связи не найден среди сущностей чанка"
            ),
        )

    # Назначение: коллбек GRAPH-005 — считает и логирует неоднозначный резолв
    #   конца связи (несколько сущностей чанка совпали по имени, разного типа);
    #   связь НЕ теряется — берётся первый кандидат по порядку появления.
    # Уровень: ✅ реализовано (fix module-dev A-09 багрепорта)
    def _on_ambiguous_endpoint(doc_id: str, chunk_id: str, name: str, candidate_ids: list[str]) -> None:
        nonlocal n_ambiguous
        n_ambiguous += 1
        log_event(
            logger, stage="entity_load", event=GRAPH_RELATION_ENDPOINT_AMBIGUOUS, level="WARNING",
            doc_id=doc_id,
            detail=(
                f"chunk_id={chunk_id} name={name!r} candidates={candidate_ids} — взят первый по "
                f"порядку появления в чанке"
            ),
        )

    driver = get_driver()
    try:
        ensure_entity_constraints(driver)
        log_event(logger, stage="entity_load", event="constraints_ready")

        doc_geo = _load_doc_geography(args.meta_path)
        rows = _iter_extraction_results(args.input, limit=args.limit)
        agg = aggregate_from_rows(rows, doc_geo, on_warning=_on_warning, on_ambiguous_endpoint=_on_ambiguous_endpoint)
        log_event(
            logger, stage="entity_load", event="aggregated",
            detail=(
                f"n_entities={len(agg.entities)} n_relations={len(agg.relations)} "
                f"n_relation_warnings={n_warnings} n_ambiguous_endpoints={n_ambiguous} "
                f"n_chunk_constraints={len(agg.chunk_constraints)} "
                f"n_mentioned_in={len(agg.mentioned_in)} n_tech_solution={len(agg.tech_solution_ids)}"
            ),
        )

        n_entities = load_entities(driver, agg, doc_geo, today_iso)
        log_event(logger, stage="entity_load", event="entities_loaded", detail=f"n={n_entities}")

        n_relations = load_relations(driver, agg, today_iso)
        log_event(logger, stage="entity_load", event="relations_loaded", detail=f"n={n_relations}")

        n_constraints = load_numeric_constraints(driver, agg.chunk_constraints)
        log_event(logger, stage="entity_load", event="numeric_constraints_loaded", detail=f"n={n_constraints}")

        n_mentioned = load_mentioned_in(driver, agg.mentioned_in)
        log_event(logger, stage="entity_load", event="mentioned_in_loaded", detail=f"n={n_mentioned}")

        report = self_check(driver)
        log_event(logger, stage="entity_load", event="self_check", detail=json.dumps(report, ensure_ascii=False))

        summary = {"n_relation_warnings": n_warnings, "n_ambiguous_endpoints": n_ambiguous, **report}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
