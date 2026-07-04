"""Загрузка гео-разметки документов (A-22) в Neo4j — единственный писатель
Document.geography после ingest/geo_classify.py (или последующей Haiku-волны
оркестратора поверх того же файла).

Вход: `data/processed/doc_geography.jsonl` (по строке на документ: doc_id,
path, geography, method, ru_hits, foreign_hits, evidence[, snippet]) — формат
`ingest.geo_classify.write_doc_geography`. Выход: обновлённое свойство
`Document.geography` (+ updated_at/edited_by, У-4) в Neo4j — только строки с
geography != "unknown" (`contracts.Geography`), узел Document уже существует
(graph.lexical_loader.load_documents).
Зависимости: neo4j (bolt-драйвер), `ariadna.contracts.Geography`,
`ariadna.graph.cypher_templates.DOC_GEOGRAPHY_UPDATE_QUERY`,
`ariadna.graph.lexical_loader.get_driver`, `ariadna.graph.config.LOAD_BATCH_SIZE`.
Инвариант: единственный модуль с правом записи в Neo4j (docs/dev/modules/
graph.md) — MATCH по doc_id, не MERGE (не создаёт узлы Document); идемпотентен —
повторный запуск с тем же входом даёт тот же результат (см. лог events).
Точка входа: `python -m ariadna.graph.doc_geography_loader --input PATH`.
Паспорт: docs/dev/modules/graph.md (A-22).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from neo4j import Driver

from ariadna.contracts import Geography
from ariadna.graph.config import DEFAULT_META_PATH, LOAD_BATCH_SIZE
from ariadna.graph.cypher_templates import DOC_GEOGRAPHY_UPDATE_QUERY
from ariadna.graph.lexical_loader import get_driver
from ariadna.logutil import get_logger, log_event, new_run_id

DEFAULT_DOC_GEOGRAPHY_PATH = Path("data/processed/doc_geography.jsonl")

# Строка doc_geography.jsonl несёт значение geography, не входящее в
# contracts.Geography (опечатка/рассинхрон с Haiku-волной оркестратора) —
# ERRORS.md.
GRAPH_INVALID_GEOGRAPHY = "GRAPH-006"

_VALID_GEOGRAPHIES = {g.value for g in Geography}


# ─── _read_rows ────────────────────────────────────────────────────────────
# Назначение: читает doc_geography.jsonl построчно, валидирует geography по
#   enum contracts.Geography (GRAPH_INVALID_GEOGRAPHY при нарушении — сразу
#   ValueError, не тихий пропуск: рассинхрон формата входа не должен молча
#   недогружать граф), возвращает только строки с geography != "unknown"
#   (unknown — ещё не решено, нечего писать в граф).
# Входные связи: путь к doc_geography.jsonl (ingest.geo_classify)
# Выходные данные: (rows_to_load: list[dict], n_total: int, n_unknown: int)
# Уровень: ✅ реализовано (A-22, worklogs/graph.md)
def _read_rows(input_path: Path) -> tuple[list[dict], int, int]:
    rows_to_load: list[dict] = []
    n_total = 0
    n_unknown = 0
    with input_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            n_total += 1
            geography = row["geography"]
            if geography not in _VALID_GEOGRAPHIES:
                raise ValueError(
                    f"{GRAPH_INVALID_GEOGRAPHY}: doc_id={row.get('doc_id')!r} "
                    f"geography={geography!r} не входит в contracts.Geography"
                )
            if geography == Geography.UNKNOWN.value:
                n_unknown += 1
                continue
            rows_to_load.append({"doc_id": row["doc_id"], "geography": geography})
    return rows_to_load, n_total, n_unknown


# ─── load_doc_geography ─────────────────────────────────────────────────────
# Назначение: пишет Document.geography батчами UNWIND+SET (DOC_GEOGRAPHY_
#   UPDATE_QUERY) — только строки с geography != "unknown"; documents, которых
#   нет в графе (doc_id не резолвится), тихо пропускаются MATCH-ом (не создают
#   сирот — см. cypher_templates.py).
# Входные связи: neo4j.Driver, list[dict] {doc_id, geography} из _read_rows,
#   today_iso — дата актуализации (У-4)
# Выходные данные: int — число строк, отправленных на запись (не обязательно
#   все нашли Document в графе — self_check показывает фактическое покрытие)
# Уровень: ✅ реализовано (A-22, worklogs/graph.md)
def load_doc_geography(driver: Driver, rows: list[dict], today_iso: str) -> int:
    payload = [
        {"doc_id": row["doc_id"], "geography": row["geography"], "updated_at": today_iso, "edited_by": ""}
        for row in rows
    ]
    with driver.session() as session:
        for i in range(0, len(payload), LOAD_BATCH_SIZE):
            session.run(DOC_GEOGRAPHY_UPDATE_QUERY, rows=payload[i : i + LOAD_BATCH_SIZE])
    return len(payload)


# ─── self_check ──────────────────────────────────────────────────────────────
# Назначение: самопроверка после загрузки — распределение Document.geography
#   по значениям enum (сколько ru/foreign/global/unknown сейчас в графе).
# Входные связи: neo4j.Driver (после load_doc_geography)
# Выходные данные: dict[geography_value, count] — в лог и в отчёт агента
# Уровень: ✅ реализовано (A-22, worklogs/graph.md)
def self_check(driver: Driver) -> dict:
    report: dict = {}
    with driver.session() as session:
        for geography in Geography:
            report[geography.value] = session.run(
                "MATCH (d:Document {geography: $g}) RETURN count(d) AS c", g=geography.value,
            ).single()["c"]
    return report


# ─── main ─────────────────────────────────────────────────────────────────────
# Назначение: CLI полной загрузки гео-разметки документов; перезапускаемо
#   (SET идемпотентен — повторный запуск с тем же входом не меняет self_check).
# Входные связи: аргументы --input/--meta-path (не используется, оставлен для
#   единообразия сигнатур с другими loader'ами графа)
# Выходные данные: нет (побочный эффект — Neo4j + печать JSON-отчёта в stdout)
# Уровень: ✅ реализовано (A-22, worklogs/graph.md)
def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка гео-разметки документов (A-22) в Neo4j")
    parser.add_argument("--input", type=Path, default=DEFAULT_DOC_GEOGRAPHY_PATH)
    parser.add_argument("--meta-path", type=Path, default=DEFAULT_META_PATH, help="не используется, для единообразия CLI")
    args = parser.parse_args()

    run_id = new_run_id("graph_doc_geo_")
    logger = get_logger("graph", run_id)
    today_iso = date.today().isoformat()

    rows, n_total, n_unknown = _read_rows(args.input)
    log_event(
        logger, stage="doc_geography_load", event="read",
        detail=f"n_total={n_total} n_to_load={len(rows)} n_unknown_skipped={n_unknown}",
    )

    driver = get_driver()
    try:
        n_loaded = load_doc_geography(driver, rows, today_iso)
        log_event(logger, stage="doc_geography_load", event="loaded", detail=f"n={n_loaded}")

        report = self_check(driver)
        log_event(logger, stage="doc_geography_load", event="self_check", detail=json.dumps(report, ensure_ascii=False))

        summary = {"n_total": n_total, "n_loaded": n_loaded, "n_unknown_skipped": n_unknown, **report}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
