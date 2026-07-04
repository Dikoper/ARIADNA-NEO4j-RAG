"""Тесты analytics/gap_map.py (A-12): карта пробелов ⭐ — GapReport с ячейками
material x process (n_sources=0 первыми) и темами only_ru/only_foreign.

Офлайновые тесты (мок driver — _FakeDriver/_FakeSession, тот же паттерн, что
tests/search/test_retrieval.py): дефолтные тематические пулы терминов (через
реальный ontology/synonyms.yaml — чтение локального YAML, не Neo4j), condition
из batched-контекста, сортировка n_sources ASC + срез до `limit`, классификация
only_ru/only_foreign, владение driver'ом (driver=None открывает и закрывает свой,
переданный driver — не закрывается), CLI main() (build_gap_report замокан).

Живые интеграционные тесты (skipif NEO4J_LIVE=False, tests/analytics/conftest.py):
build_gap_report на боевом графе — форма GapReport, сортировка, приёмочный кейс
TASK.md «холодный климат + кучное выщелачивание + никелевая руда». Один
module-scoped вызов build_gap_report(limit=45000) переиспользуется всеми
проверками (не дешёвая операция — см. worklog A-12).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from ariadna.analytics import gap_map
from ariadna.contracts import GapCell, GapReport

from conftest import NEO4J_LIVE


# ══════════════════════ Фиктивный driver (офлайн) ══════════════════════

class _FakeSession:
    """Фиктивная neo4j.Session — .run(query, **kwargs) отдаёт заранее заданный
    список dict-строк по тексту запроса (без реального bolt-соединения)."""

    def __init__(self, rows_by_query: dict[str, list[dict]], calls: list[tuple[str, dict]]):
        self._rows_by_query = rows_by_query
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **kwargs):
        self._calls.append((query, kwargs))
        return [dict(r) for r in self._rows_by_query.get(query, [])]


class _FakeDriver:
    """Фиктивный neo4j.Driver — .session() отдаёт _FakeSession с заготовленными
    строками по тексту запроса; .close() считает вызовы (владение driver'ом)."""

    def __init__(self, rows_by_query: dict[str, list[dict]] | None = None):
        self._rows_by_query = rows_by_query or {}
        self.calls: list[tuple[str, dict]] = []
        self.n_close_calls = 0

    def session(self):
        return _FakeSession(self._rows_by_query, self.calls)

    def close(self):
        self.n_close_calls += 1


# ══════════════════════ Дефолтные тематические пулы терминов ══════════════════════

# Назначение: _default_terms(_DEFAULT_MATERIAL_TOPICS/_DEFAULT_PROCESS_TOPICS)
#   покрывает темы жюри (TASK.md) + случай "холодный климат/кучное выщелачивание/
#   никелевая руда" (общие требования TASK.md) через реальный ontology/synonyms.yaml.
# Уровень: ✅ реализовано (module-tester A-12)
def test_default_material_terms_cover_jury_topics_and_cold_climate_case():
    terms = gap_map._default_terms(gap_map._DEFAULT_MATERIAL_TOPICS)
    for expected in ("никель", "католит", "штейн", "шлак", "платиноиды", "шахтные воды", "медно-никелевая руда"):
        assert expected in terms, f"{expected} отсутствует в дефолтном пуле material_terms"


def test_default_process_terms_cover_jury_topics_and_cold_climate_case():
    terms = gap_map._default_terms(gap_map._DEFAULT_PROCESS_TOPICS)
    for expected in ("обессоливание", "электроэкстракция", "закачка шахтных вод", "кучное выщелачивание"):
        assert expected in terms, f"{expected} отсутствует в дефолтном пуле process_terms"


# Назначение: короткие аббревиатуры онтологии (< MIN_TERM_LEN символов, напр.
#   "ni"/"ca"/"au"/"cu"/"co") отфильтрованы — CONTAINS-подстрока такой длины
#   массово ловит случайные совпадения в именах узлов (см. cypher_templates.py
#   комментарий gap_matrix).
# Уровень: ✅ реализовано (module-tester A-12)
def test_default_terms_filter_out_short_abbreviations():
    terms = gap_map._default_terms(gap_map._DEFAULT_MATERIAL_TOPICS)
    assert all(len(t) >= gap_map.MIN_TERM_LEN for t in terms)
    assert "ni" not in terms and "au" not in terms and "ca" not in terms


# Назначение: неизвестный термин (не найден в ontology/synonyms.yaml) — не
#   падает, используется как есть (canonical_name() -> None -> term сам себе канон).
# Уровень: ✅ реализовано (module-tester A-12)
def test_expand_topic_term_unknown_term_falls_back_to_itself():
    result = gap_map._expand_topic_term("совершенно неизвестный термин", {})
    assert result == {"совершенно неизвестный термин"}


# ══════════════════════ _condition_for_row ══════════════════════

# Назначение: до MAX_CONDITION_ITEMS РАЗЛИЧНЫХ условий из condition_by_chunk,
#   в порядке chunk_ids строки, дубли не повторяются.
# Уровень: ✅ реализовано (module-tester A-12)
def test_condition_for_row_joins_up_to_max_items_deduped():
    row = {"chunk_ids": ["c1", "c2", "c3", "c4"]}
    condition_by_chunk = {"c1": "температура = 20.0 °C", "c2": "температура = 20.0 °C", "c3": "давление >= 5.0 атм"}
    result = gap_map._condition_for_row(row, condition_by_chunk)
    assert result == "температура = 20.0 °C; давление >= 5.0 атм"


# Назначение: ни одного chunk_id с условием (или chunk_ids пуст, n_sources=0 —
#   самый частый случай) -> "" (не изобретать условие).
# Уровень: ✅ реализовано (module-tester A-12)
def test_condition_for_row_returns_empty_string_when_no_context():
    assert gap_map._condition_for_row({"chunk_ids": []}, {}) == ""
    assert gap_map._condition_for_row({"chunk_ids": ["c1"]}, {}) == ""


# ══════════════════════ _fetch_conditions ══════════════════════

# Назначение: пустой chunk_ids -> {} БЕЗ обращения к driver (защита от лишнего
#   Cypher-запроса, когда gap_matrix ничего не вернул).
# Уровень: ✅ реализовано (module-tester A-12)
def test_fetch_conditions_empty_chunk_ids_returns_empty_dict_without_driver_call():
    driver = _FakeDriver()
    result = gap_map._fetch_conditions(driver, [])
    assert result == {}
    assert driver.calls == []


# Назначение: constraint_texts/property_names с висячими пробелами (param
#   пуст в извлечении, см. worklogs/graph.md A-09/A-10) -> strip() убирает
#   пустые/пробельные элементы, берётся первый непустой.
# Уровень: ✅ реализовано (module-tester A-12)
def test_fetch_conditions_strips_blank_param_and_picks_first_nonempty():
    rows = [{"chunk_id": "c1", "constraint_texts": [" = 80.0 %", "температура <= 20.0 °C"], "property_names": []}]
    driver = _FakeDriver({gap_map.GAP_CELL_CONTEXT_QUERY: rows})
    result = gap_map._fetch_conditions(driver, ["c1"])
    assert result == {"c1": "= 80.0 %"}


# ══════════════════════ _build_geography_themes ══════════════════════

# Назначение: geos == {"ru"} -> only_ru; == {"foreign"} -> only_foreign;
#   смешанное/{"unknown"}/пусто -> ни в один из списков (строгое "только").
# Уровень: ✅ реализовано (module-tester A-12)
def test_build_geography_themes_classifies_ru_foreign_mixed_and_unknown():
    rows = [
        {"node_id": "m1", "name": "тема-ru", "entity_type": "Material", "doc_geographies": ["ru"]},
        {"node_id": "m2", "name": "тема-foreign", "entity_type": "Process", "doc_geographies": ["foreign"]},
        {"node_id": "m3", "name": "тема-смешанная", "entity_type": "Material", "doc_geographies": ["ru", "foreign"]},
        {"node_id": "m4", "name": "тема-unknown", "entity_type": "Process", "doc_geographies": ["unknown"]},
        {"node_id": "m5", "name": "тема-без-документов", "entity_type": "Material", "doc_geographies": []},
    ]
    driver = _FakeDriver({gap_map.GEOGRAPHY_THEMES_QUERY: rows})
    only_ru, only_foreign = gap_map._build_geography_themes(driver)
    assert only_ru == ["тема-ru"]
    assert only_foreign == ["тема-foreign"]


# Назначение: A-22 — geos == {"global"} (тема встречается ТОЛЬКО в документах
#   с обеими практиками в одном источнике, contracts.Geography.GLOBAL) -> ни в
#   один из списков «только…» (не строго "ru", не строго "foreign"); смешанное
#   ru+global (или foreign+global) — та же логика, множество не равно {"ru"}/
#   {"foreign"} -> исключено. Строгое равенство сета doc_geographies уже
#   реализует это корректно (_build_geography_themes не менялся под A-22,
#   только тест — проверка постановки задачи A-22, worklogs/ingest.md).
# Уровень: ✅ реализовано (module-tester A-22)
def test_build_geography_themes_excludes_global_only_and_mixed_with_global():
    rows = [
        {"node_id": "m1", "name": "тема-глобальная", "entity_type": "Material", "doc_geographies": ["global"]},
        {"node_id": "m2", "name": "тема-ru-и-глобальная", "entity_type": "Process", "doc_geographies": ["ru", "global"]},
        {"node_id": "m3", "name": "тема-foreign-и-глобальная", "entity_type": "Material", "doc_geographies": ["foreign", "global"]},
        {"node_id": "m4", "name": "тема-чистая-ru", "entity_type": "Process", "doc_geographies": ["ru"]},
    ]
    driver = _FakeDriver({gap_map.GEOGRAPHY_THEMES_QUERY: rows})
    only_ru, only_foreign = gap_map._build_geography_themes(driver)
    assert only_ru == ["тема-чистая-ru"]
    assert only_foreign == []


# ══════════════════════ _build_cells ══════════════════════

_GAP_ROWS = [
    {"material_name": "штейн", "process_name": "флотация", "n_sources": 5, "chunk_ids": ["c1"]},
    {"material_name": "шлак", "process_name": "флотация", "n_sources": 0, "chunk_ids": []},
    {"material_name": "золото", "process_name": "флотация", "n_sources": 0, "chunk_ids": []},
    {"material_name": "серебро", "process_name": "флотация", "n_sources": 2, "chunk_ids": ["c2"]},
]


# Назначение: ячейки пересортированы n_sources ASC (n_sources=0 первыми — паспорт
#   модуля), при равенстве n_sources — по material_name/process_name (стабильный
#   тай-брейк); срез строго до публичного `limit`, шаблон gap_matrix вызван с
#   $limit=GAP_DB_FETCH_LIMIT (не с публичным `limit` — см. комментарий в коде).
# Уровень: ✅ реализовано (module-tester A-12)
def test_build_cells_sorts_zero_sources_first_and_respects_public_limit():
    driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: _GAP_ROWS, gap_map.GAP_CELL_CONTEXT_QUERY: []})
    cells = gap_map._build_cells(driver, limit=2)
    assert len(cells) == 2
    assert [c.n_sources for c in cells] == [0, 0]
    assert {c.material for c in cells} == {"шлак", "золото"}

    gap_matrix_calls = [kwargs for query, kwargs in driver.calls if query == gap_map.TEMPLATES["gap_matrix"]]
    assert len(gap_matrix_calls) == 1
    assert gap_matrix_calls[0]["limit"] == gap_map.GAP_DB_FETCH_LIMIT


# Назначение: GapCell.condition заполняется по chunk_ids строки через batched
#   _fetch_conditions/_condition_for_row (n_sources>0 -> непустые chunk_ids ->
#   condition, если контекст найден).
# Уровень: ✅ реализовано (module-tester A-12)
def test_build_cells_attaches_condition_from_batched_context():
    rows = [{"material_name": "штейн", "process_name": "флотация", "n_sources": 1, "chunk_ids": ["c1"]}]
    context_rows = [{"chunk_id": "c1", "constraint_texts": ["температура = 20.0 °C"], "property_names": []}]
    driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: rows, gap_map.GAP_CELL_CONTEXT_QUERY: context_rows})
    cells = gap_map._build_cells(driver, limit=10)
    assert cells[0] == GapCell(material="штейн", process="флотация", condition="температура = 20.0 °C", n_sources=1)


# ══════════════════════ Диверсификация (дефект №1 tester-отчёта) ══════════════════════

# Назначение: не более MAX_CELLS_PER_MATERIAL ячеек одного материала в итоговом
#   срезе — синтетика с ОДНИМ материалом × 5 процессов, все n_sources=0
#   (равный приоритет/сумма n_mentions после сортировки — material_name/
#   process_name финальный тай-брейк, алфавитно первые MAX_CELLS_PER_MATERIAL
#   выживают, остальные отброшены _apply_material_diversity_cap).
# Уровень: ✅ реализовано (fixer A-12)
def test_build_cells_caps_at_most_n_cells_per_material():
    rows = [
        {"material_name": "материал-X", "process_name": p, "n_sources": 0, "chunk_ids": []}
        for p in ("процесс-A", "процесс-B", "процесс-C", "процесс-D", "процесс-E")
    ]
    driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: rows, gap_map.GAP_CELL_CONTEXT_QUERY: []})

    cells = gap_map._build_cells(driver, limit=50)

    assert len(cells) == gap_map.MAX_CELLS_PER_MATERIAL
    assert all(c.material == "материал-X" for c in cells)
    assert [c.process for c in cells] == ["процесс-A", "процесс-B", "процесс-C"]


# Назначение: диверсификация считает материалы НЕЗАВИСИМО — разные материалы
#   не режут друг друга, только повторы одного и того же материала.
# Уровень: ✅ реализовано (fixer A-12)
def test_build_cells_diversity_cap_does_not_affect_distinct_materials():
    rows = [
        {"material_name": f"материал-{i}", "process_name": "процесс-Z", "n_sources": 0, "chunk_ids": []}
        for i in range(5)
    ]
    driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: rows, gap_map.GAP_CELL_CONTEXT_QUERY: []})

    cells = gap_map._build_cells(driver, limit=50)

    assert len(cells) == 5


# ══════════════════════ Тай-брейк по сумме n_mentions (дефект №1, п.1) ══════════════════════

# Назначение: при равном n_sources и равном приоритете тай-брейк — сумма
#   material_n_mentions+process_n_mentions DESC (не алфавит) — строка с
#   БОЛЬШЕЙ суммой упоминаний идёт первой, даже если она "позже" алфавита.
# Уровень: ✅ реализовано (fixer A-12)
def test_build_cells_tie_break_by_mentions_sum_descending_not_alphabetical():
    rows = [
        {"material_name": "яблоко", "process_name": "процесс", "n_sources": 0, "chunk_ids": [],
         "material_n_mentions": 5, "process_n_mentions": 5},
        {"material_name": "абрикос", "process_name": "процесс", "n_sources": 0, "chunk_ids": [],
         "material_n_mentions": 1, "process_n_mentions": 1},
    ]
    driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: rows, gap_map.GAP_CELL_CONTEXT_QUERY: []})

    cells = gap_map._build_cells(driver, limit=10)

    assert [c.material for c in cells] == ["яблоко", "абрикос"]


# ══════════════════════ limit валидация (дефект №2 tester-отчёта) ══════════════════════

# Назначение: limit < 0 -> ValueError с кодом ANALYTICS-002 ДО обращения к
#   Neo4j (driver=None — get_driver НЕ монкипатчен, падение до открытия driver
#   доказывает, что проверка происходит раньше владения driver'ом).
# Уровень: ✅ реализовано (fixer A-12)
def test_build_gap_report_negative_limit_raises_value_error_before_driver():
    with pytest.raises(ValueError, match="ANALYTICS-002"):
        gap_map.build_gap_report(driver=None, limit=-1)


# Назначение: limit=0 — валидное значение, НЕ ошибка — пустой отчёт (cells=[],
#   only_ru/only_foreign считаются как обычно — они не зависят от `limit`).
# Уровень: ✅ реализовано (fixer A-12)
def test_build_gap_report_limit_zero_returns_empty_cells():
    fake_driver = _FakeDriver({
        gap_map.TEMPLATES["gap_matrix"]: [{"material_name": "штейн", "process_name": "флотация",
                                            "n_sources": 0, "chunk_ids": []}],
        gap_map.GEOGRAPHY_THEMES_QUERY: [],
    })

    report = gap_map.build_gap_report(driver=fake_driver, limit=0)

    assert report.cells == []


# ══════════════════════ build_gap_report — владение driver'ом ══════════════════════

_EMPTY_ROWS = {}


# Назначение: driver=None -> открывает свой driver (get_driver, монкипатч) и
#   ЗАКРЫВАЕТ его по завершении (владение — build_gap_report).
# Уровень: ✅ реализовано (module-tester A-12)
def test_build_gap_report_driver_none_opens_and_closes_own_driver(monkeypatch):
    fake_driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: [], gap_map.GEOGRAPHY_THEMES_QUERY: []})
    monkeypatch.setattr("ariadna.graph.lexical_loader.get_driver", lambda: fake_driver)

    report = gap_map.build_gap_report(driver=None, limit=10)

    assert isinstance(report, GapReport)
    assert report.cells == []
    assert fake_driver.n_close_calls == 1


# Назначение: driver передан явно -> НЕ закрывается (владение у вызывающей
#   стороны, например tests/analytics/conftest.py::driver — session-scoped).
# Уровень: ✅ реализовано (module-tester A-12)
def test_build_gap_report_with_explicit_driver_does_not_close_it():
    fake_driver = _FakeDriver({gap_map.TEMPLATES["gap_matrix"]: [], gap_map.GEOGRAPHY_THEMES_QUERY: []})

    gap_map.build_gap_report(driver=fake_driver, limit=10)

    assert fake_driver.n_close_calls == 0


# ══════════════════════ CLI main() (офлайн, build_gap_report замокан) ══════════════════════

_SAMPLE_REPORT = GapReport(
    cells=[GapCell(material="штейн", process="флотация", condition="", n_sources=0)],
    only_ru=["тема-ru"],
    only_foreign=["тема-foreign"],
)


# Назначение: --json печатает model_dump_json() полного отчёта.
# Уровень: ✅ реализовано (module-tester A-12)
def test_main_json_flag_prints_full_gap_report_json(monkeypatch, capsys):
    monkeypatch.setattr(gap_map, "build_gap_report", lambda limit=50: _SAMPLE_REPORT)
    monkeypatch.setattr(sys, "argv", ["gap_map", "--json"])

    gap_map.main()

    out = capsys.readouterr().out
    assert '"material": "штейн"' in out
    assert '"тема-ru"' in out


# Назначение: без --json — человекочитаемая сводка (счётчики + топ-пробелы +
#   only_ru/only_foreign).
# Уровень: ✅ реализовано (module-tester A-12)
def test_main_default_prints_human_readable_summary(monkeypatch, capsys):
    monkeypatch.setattr(gap_map, "build_gap_report", lambda limit=50: _SAMPLE_REPORT)
    monkeypatch.setattr(sys, "argv", ["gap_map"])

    gap_map.main()

    out = capsys.readouterr().out
    assert "штейн × флотация" in out
    assert "тема-ru" in out
    assert "тема-foreign" in out


# Назначение: CLI-смоук субпроцессом на живом графе (`python -m ariadna.analytics.
#   gap_map --limit 5 --json`) — end-to-end проверка argparse+модуля запуска.
# Уровень: ✅ реализовано (module-tester A-12)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_cli_subprocess_smoke_with_small_limit():
    result = subprocess.run(
        [sys.executable, "-m", "ariadna.analytics.gap_map", "--limit", "5", "--json"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert '"cells"' in result.stdout
    assert '"only_ru"' in result.stdout


# ══════════════════════ Интеграция на живом графе (skipif NEO4J_LIVE) ══════════════════════

# Назначение: ОДИН module-scoped вызов build_gap_report(limit=45000) — покрывает
#   весь топик-пул дефолтных материалов/процессов (~42.5k пар на боевом графе,
#   см. worklog A-12) — переиспользуется всеми проверками ниже (не дешёвая
#   операция, ~20-25с, независимо от `limit`, см. GAP_DB_FETCH_LIMIT).
# Уровень: ✅ реализовано (module-tester A-12)
@pytest.fixture(scope="module")
def report(driver) -> GapReport:
    return gap_map.build_gap_report(driver=driver, limit=45000)


@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_live_report_is_gap_report_with_nonempty_cells(report):
    assert isinstance(report, GapReport)
    assert len(report.cells) > 0
    assert all(isinstance(c, GapCell) and c.material and c.process for c in report.cells)


@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_live_report_cells_sorted_n_sources_ascending(report):
    n_sources_seq = [c.n_sources for c in report.cells]
    assert n_sources_seq == sorted(n_sources_seq)
    assert n_sources_seq[0] == 0  # настоящие пробелы — первыми


# Назначение: приёмочный кейс TASK.md — «нет экспериментов для комбинации:
#   холодный климат + кучное выщелачивание + никелевая руда» — должен
#   находиться как ячейка n_sources=0 (climate — не отдельный узел графа,
#   condition пуст ожидаемо; материал/процесс без прямой связи — это и есть
#   искомый пробел, см. паспорт модуля).
# Уровень: ✅ реализовано (module-tester A-12)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_live_cold_climate_heap_leaching_nickel_ore_case_found(report):
    hits = [c for c in report.cells if c.process == "кучное выщелачивание" and "никел" in c.material.lower()]
    assert hits, "не нашлась ни одна пара «никель-содержащий материал x кучное выщелачивание»"
    assert any(c.n_sources == 0 for c in hits), "ожидался хотя бы один настоящий пробел (n_sources=0)"


# Назначение: тот же приёмочный кейс TASK.md, но на ДЕФОЛТНОМ limit=50 (не
# limit=45000 фикстуры `report` выше) — именно это видит жюри при демо без
# явного указания --limit. Отдельный (не переиспользующий фикстуру `report`)
# вызов build_gap_report() — дефект №1 tester-отчёта: на limit=45000 кейс
# находился (позиция ~1119 без диверсификации), но на дефолтном limit=50 был
# невидим — алфавитный тай-брейк выталкивал его материалом "(NH4)2SO4".
# Уровень: ✅ реализовано (fixer A-12)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_live_cold_climate_heap_leaching_nickel_ore_case_found_at_default_limit(driver):
    default_report = gap_map.build_gap_report(driver=driver)  # limit=50 по умолчанию

    assert len(default_report.cells) <= 50
    hits = [c for c in default_report.cells
            if c.process == "кучное выщелачивание" and "никел" in c.material.lower()]
    assert hits, "приёмочный кейс TASK.md не найден в GapReport.cells на дефолтном limit=50"
    assert any(c.n_sources == 0 for c in hits), "ожидался хотя бы один настоящий пробел (n_sources=0)"

    material_counts: dict[str, int] = {}
    for cell in default_report.cells:
        material_counts[cell.material] = material_counts.get(cell.material, 0) + 1
    assert max(material_counts.values()) <= gap_map.MAX_CELLS_PER_MATERIAL, (
        "диверсификация нарушена в топ-10 дефолтного отчёта: "
        f"{[m for m, n in material_counts.items() if n > gap_map.MAX_CELLS_PER_MATERIAL]}"
    )


# Назначение: only_ru/only_foreign — списки строк, НЕПУСТЫЕ на текущих боевых
#   данных с момента A-22 (ingest.geo_classify + graph.doc_geography_loader
#   разметили Document.geography правилами: 56 ru/55 foreign/49 global/17
#   unknown из 177 — до A-22 все 177 были "unknown" и оба списка были пусты,
#   это фиксировалось предыдущей версией теста; поведение изменилось МЕТОДОМ
#   этой задачи, не багом analytics, см. worklogs/ingest.md#A-22).
# Уровень: ✅ реализовано (module-tester A-12; обновлено A-22)
@pytest.mark.skipif(not NEO4J_LIVE, reason="нужен живой стенд Neo4j (NEO4J_LIVE)")
def test_live_only_ru_only_foreign_are_string_lists(report):
    assert isinstance(report.only_ru, list) and all(isinstance(x, str) for x in report.only_ru)
    assert isinstance(report.only_foreign, list) and all(isinstance(x, str) for x in report.only_foreign)
    assert len(report.only_ru) > 0
    assert len(report.only_foreign) > 0
