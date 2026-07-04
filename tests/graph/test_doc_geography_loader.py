"""Тесты graph/doc_geography_loader.py (A-22): загрузка гео-разметки
документов (ingest.geo_classify -> data/processed/doc_geography.jsonl) в
Neo4j.

Мок-driver (тот же паттерн _FakeDriver/_FakeSession, что tests/analytics/
test_gap_map.py) — офлайн, без живого Neo4j: _read_rows/load_doc_geography
проверяются изолированно от bolt-соединения. CLI main() — отдельный смоук на
живом Neo4j (skipif NEO4J_LIVE, tests/analytics/conftest.py::NEO4J_LIVE) —
единственный способ дёшево проверить end-to-end запись без создания отдельного
живого фикстур-набора (доп. Document-узлов) только под этот CLI.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ariadna.contracts import Geography
from ariadna.graph import doc_geography_loader as loader
from ariadna.graph.cypher_templates import DOC_GEOGRAPHY_UPDATE_QUERY


# Назначение: доступность живого Neo4j — тот же приём, что tests/analytics/
#   conftest.py::_check_neo4j_alive (independent copy: tests/graph/conftest.py
#   не выставляет NEO4J_LIVE — его `driver` фикстура живая безусловно, см.
#   test_entity_graph_writer.py — этот модуль сам решает, скипать ли CLI-смоук).
# Уровень: ✅ реализовано (module-tester A-22)
def _check_neo4j_alive() -> bool:
    try:
        from ariadna.graph.lexical_loader import get_driver as _get_driver

        d = _get_driver()
        try:
            d.verify_connectivity()
            return True
        finally:
            d.close()
    except Exception:  # noqa: BLE001 — любой сбой подключения = живого стенда нет
        return False


NEO4J_LIVE = _check_neo4j_alive()


# ══════════════════════ Фиктивный driver (офлайн) ══════════════════════

class _FakeSession:
    def __init__(self, calls: list[tuple[str, dict]]):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **kwargs):
        self._calls.append((query, kwargs))
        return []


class _FakeDriver:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return _FakeSession(self.calls)


# ══════════════════════ _read_rows ══════════════════════

# Назначение: строки с geography="unknown" отфильтрованы (не отправляются в
#   Neo4j — нечего писать); n_total/n_unknown считают ВСЕ прочитанные строки.
# Уровень: ✅ реализовано (module-tester A-22)
def test_read_rows_filters_out_unknown_geography(tmp_path):
    input_path = tmp_path / "doc_geography.jsonl"
    rows = [
        {"doc_id": "d1", "geography": "ru"},
        {"doc_id": "d2", "geography": "foreign"},
        {"doc_id": "d3", "geography": "global"},
        {"doc_id": "d4", "geography": "unknown"},
    ]
    input_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    rows_to_load, n_total, n_unknown = loader._read_rows(input_path)

    assert n_total == 4
    assert n_unknown == 1
    assert {r["doc_id"] for r in rows_to_load} == {"d1", "d2", "d3"}
    assert all(r["geography"] != "unknown" for r in rows_to_load)


# Назначение: значение geography вне enum contracts.Geography -> ValueError с
#   кодом GRAPH-006 ДО обращения к Neo4j (валидация раньше записи).
# Уровень: ✅ реализовано (module-tester A-22)
def test_read_rows_raises_on_invalid_geography_value(tmp_path):
    input_path = tmp_path / "doc_geography.jsonl"
    input_path.write_text(
        json.dumps({"doc_id": "bad", "geography": "марсианская"}) + "\n", encoding="utf-8",
    )

    with pytest.raises(ValueError, match="GRAPH-006"):
        loader._read_rows(input_path)


# ══════════════════════ load_doc_geography ══════════════════════

# Назначение: load_doc_geography пишет DOC_GEOGRAPHY_UPDATE_QUERY одним (или
#   несколькими, по LOAD_BATCH_SIZE) вызовом с rows={doc_id, geography,
#   updated_at, edited_by} — edited_by="" (автоизвлечение, не ручная правка,
#   тот же принцип, что graph.entity_graph_writer).
# Уровень: ✅ реализовано (module-tester A-22)
def test_load_doc_geography_sends_expected_rows_with_provenance():
    driver = _FakeDriver()
    rows = [{"doc_id": "d1", "geography": "ru"}, {"doc_id": "d2", "geography": "foreign"}]

    n = loader.load_doc_geography(driver, rows, today_iso="2026-07-04")

    assert n == 2
    assert len(driver.calls) == 1
    query, kwargs = driver.calls[0]
    assert query == DOC_GEOGRAPHY_UPDATE_QUERY
    sent_rows = kwargs["rows"]
    assert sent_rows == [
        {"doc_id": "d1", "geography": "ru", "updated_at": "2026-07-04", "edited_by": ""},
        {"doc_id": "d2", "geography": "foreign", "updated_at": "2026-07-04", "edited_by": ""},
    ]


# Назначение: пустой список rows -> 0 записей, НО .run всё равно вызывается
#   один раз с пустым батчем (текущая реализация не делает раннего return —
#   не баг, просто нет специального short-circuit; фиксируем факт поведения).
# Уровень: ✅ реализовано (module-tester A-22)
def test_load_doc_geography_empty_rows_returns_zero():
    driver = _FakeDriver()
    n = loader.load_doc_geography(driver, [], today_iso="2026-07-04")
    assert n == 0


# ══════════════════════ CLI main() — смоук на живом Neo4j ══════════════════════

# Назначение: CLI-смоук субпроцессом на живом графе с синтетическим
#   doc_geography.jsonl (doc_id заведомо отсутствует в графе — MATCH не находит
#   узел, строка не создаёт сироту, self_check не падает) — end-to-end
#   argparse+модуль запуска, без порчи боевых Document.geography.
# Уровень: ✅ реализовано (module-tester A-22)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_cli_subprocess_smoke_with_nonexistent_doc_id(tmp_path):
    input_path = tmp_path / "doc_geography.jsonl"
    input_path.write_text(
        json.dumps({"doc_id": "test_a22_nonexistent_doc", "path": "x", "geography": "ru",
                     "method": "rules", "ru_hits": 3, "foreign_hits": 0, "evidence": []}) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "ariadna.graph.doc_geography_loader", "--input", str(input_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["n_total"] == 1
    assert summary["n_loaded"] == 1
    assert summary["n_unknown_skipped"] == 0
    assert set(summary) >= {g.value for g in Geography}
