"""Тесты карты пробелов (A-13): строки таблицы с флагом is_gap, выбор блоков
only_ru/only_foreign по гео-фильтру сайдбара."""
from __future__ import annotations

from ariadna.contracts import GapCell, GapReport
from ui.gap_view import build_gap_rows, select_geography_topics


def _sample_report():
    return GapReport(
        cells=[
            GapCell(material="Штейн", process="Флотация", condition="холодный климат", n_sources=0),
            GapCell(material="Никель", process="Электроэкстракция", condition="", n_sources=5),
        ],
        only_ru=["Циркуляция католита"],
        only_foreign=["Обратный осмос при обессоливании"],
    )


def test_build_gap_rows_flags_zero_sources_as_gap():
    rows = build_gap_rows(_sample_report())
    assert rows[0]["n_sources"] == 0
    assert rows[0]["is_gap"] is True
    assert rows[1]["n_sources"] == 5
    assert rows[1]["is_gap"] is False


def test_build_gap_rows_preserves_fields():
    rows = build_gap_rows(_sample_report())
    assert rows[0]["material"] == "Штейн"
    assert rows[0]["process"] == "Флотация"
    assert rows[0]["condition"] == "холодный климат"


def test_build_gap_rows_empty_report():
    assert build_gap_rows(GapReport()) == []


def test_select_geography_topics_all_returns_both_blocks():
    blocks = select_geography_topics(_sample_report(), "all")
    assert "Только в отечественной литературе" in blocks
    assert "Только в зарубежной литературе" in blocks
    assert blocks["Только в отечественной литературе"] == ["Циркуляция католита"]


def test_select_geography_topics_ru_returns_only_ru_block():
    blocks = select_geography_topics(_sample_report(), "ru")
    assert list(blocks) == ["Только в отечественной литературе"]


def test_select_geography_topics_foreign_returns_only_foreign_block():
    blocks = select_geography_topics(_sample_report(), "foreign")
    assert list(blocks) == ["Только в зарубежной литературе"]


def test_select_geography_topics_does_not_invent_data():
    report = GapReport(cells=[], only_ru=[], only_foreign=[])
    blocks = select_geography_topics(report, "all")
    assert blocks["Только в отечественной литературе"] == []
    assert blocks["Только в зарубежной литературе"] == []
