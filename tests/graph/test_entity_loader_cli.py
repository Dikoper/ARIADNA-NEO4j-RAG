"""Тесты A-09: `ariadna.graph.entity_loader` — CLI-обвязка (`--input`/
`--meta-path`/`--limit`) поверх `entity_dedup`/`entity_graph_writer`
(оба уже покрыты отдельно, здесь — только сборка CLI).

Два уровня:
1. Офлайн, БЕЗ записи в Neo4j: `--limit` реально ограничивает число строк,
   прочитанных из БОЕВОГО data/processed/extracted_haiku.jsonl (только чтение
   файла + чистая агрегация — без риска для боевой Neo4j).
2. Полный end-to-end через subprocess (`python -m ariadna.graph.entity_loader`)
   против живого Neo4j, но на ИЗОЛИРОВАННОЙ фикстуре с префиксом test_a09_cli_
   (не на боевом extracted_haiku.jsonl — см. «Проблемы» в worklogs/graph.md:
   ручной прогон `--limit 5` на боевом входе обнаружил, что CLI НЕ идемпотентен
   против уже наполненной боевой базы при частичном (--limit) чтении корпуса —
   гонять такой прогон автоматически на каждый pytest небезопасно).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ariadna.contracts import ExtractionResult
from ariadna.graph.config import DEFAULT_EXTRACTED_PATH
from ariadna.graph.entity_dedup import aggregate_from_rows
from ariadna.graph.entity_loader import _iter_extraction_results

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ══════════════════════════ 1. --limit на боевом файле, без Neo4j ══════════════════════════

# ─── test_limit_restricts_rows_read_from_live_extracted_file ────────────
# Назначение: --limit действительно ограничивает число прочитанных строк
#   боевого extracted_haiku.jsonl (3500 строк, docs/dev/worklogs/graph.md) —
#   офлайн-версия CLI-смоука, безопасная для боевой Neo4j (файл только читается).
# Уровень: ✅ реализовано (module-tester A-09)
def test_limit_restricts_rows_read_from_live_extracted_file():
    path = REPO_ROOT / DEFAULT_EXTRACTED_PATH
    assert path.exists(), "боевой data/processed/extracted_haiku.jsonl должен существовать для этого теста"

    rows = list(_iter_extraction_results(path, limit=5))
    assert len(rows) == 5
    assert all(isinstance(r, ExtractionResult) for r in rows)


# ─── test_aggregate_from_rows_does_not_crash_on_live_limited_subset ──────
# Назначение: агрегация первых 5 строк боевого файла не падает и даёт
#   непустой результат — проверка совместимости чистой логики с реальными
#   данными (без записи в Neo4j).
# Уровень: ✅ реализовано (module-tester A-09)
def test_aggregate_from_rows_does_not_crash_on_live_limited_subset():
    path = REPO_ROOT / DEFAULT_EXTRACTED_PATH
    rows = _iter_extraction_results(path, limit=5)
    agg = aggregate_from_rows(rows, {})
    assert len(agg.entities) > 0


# ══════════════════════════ 2. Полный CLI end-to-end на изолированной фикстуре ══════════════════════════

# ─── _run_cli ─────────────────────────────────────────────────────────────
# Назначение: запускает entity_loader.main() как подпроцесс на фикстурах
#   test_a09_cli_* и парсит JSON-отчёт из stdout.
# Уровень: ✅ реализовано (module-tester A-09)
def _run_cli(extra_args: list[str] | None = None) -> dict:
    args = [
        sys.executable, "-m", "ariadna.graph.entity_loader",
        "--input", str(FIXTURES_DIR / "entity_extracted.jsonl"),
        "--meta-path", str(FIXTURES_DIR / "entity_meta.jsonl"),
    ] + (extra_args or [])
    result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"CLI упал: stdout={result.stdout!r} stderr={result.stderr[-2000:]!r}"
    return json.loads(result.stdout)


# ─── test_cli_smoke_on_isolated_fixture_runs_without_errors ─────────────
# Назначение: CLI отрабатывает end-to-end (констрейнты, агрегация, запись
#   в Neo4j, self_check) на изолированной фикстуре без ошибок, отчёт —
#   валидный JSON с ожидаемыми ключами self_check.
# Уровень: ✅ реализовано (module-tester A-09)
def test_cli_smoke_on_isolated_fixture_runs_without_errors(driver):
    report = _run_cli()
    assert report["n_relation_warnings"] == 0
    assert "n_entity_total" in report
    assert "n_by_label" in report
    assert "n_tech_solution" in report

    with driver.session() as session:
        n_material = session.run(
            "MATCH (e:Material) WHERE e.id CONTAINS 'test-a09-cli' RETURN count(e) AS c"
        ).single()["c"]
        n_process = session.run(
            "MATCH (e:Process) WHERE e.id CONTAINS 'test-a09-cli' RETURN count(e) AS c"
        ).single()["c"]
    assert n_material == 1
    assert n_process == 1


# ─── test_cli_double_run_on_isolated_fixture_is_idempotent ──────────────
# Назначение: повторный запуск CLI на той же фикстуре не меняет счётчики
#   тестовых узлов/связей (MERGE, не CREATE) — заявленное свойство CLI
#   (entity_loader.main пре-комментарий «перезапускаемо»).
# Уровень: ✅ реализовано (module-tester A-09)
def test_cli_double_run_on_isolated_fixture_is_idempotent(driver):
    _run_cli()
    with driver.session() as session:
        first = session.run(
            "MATCH (e:Entity) WHERE e.id CONTAINS 'test-a09-cli' RETURN count(e) AS c"
        ).single()["c"]

    _run_cli()
    with driver.session() as session:
        second = session.run(
            "MATCH (e:Entity) WHERE e.id CONTAINS 'test-a09-cli' RETURN count(e) AS c"
        ).single()["c"]

    assert first == second == 2
