"""Карта пробелов ⭐ (A-13) — чистые хелперы форматирования `contracts.GapReport`.

Вход: `GapReport` (cells + only_ru/only_foreign, см. contracts.py — заполняет
`analytics.gap_map.build_gap_report`, A-12). Выход: список строк для таблицы
(с флагом «пробел» при n_sources=0) и выбор блока «только в отеч./зарубеж.
литературе» по гео-фильтру сайдбара.

Зависимости: только `contracts.GapReport` (pydantic). Инвариант: `GapCell` не
несёт признака географии (контракт 🔒) — гео-фильтр сайдбара к самой матрице
пробелов не применяется (нет данных), только к блокам only_ru/only_foreign;
честно отражено в `geography_note`.
"""
from __future__ import annotations

from ariadna.contracts import GapReport

GAP_MATRIX_GEOGRAPHY_NOTE = (
    "Разбивка ячеек по географии появится после разметки документов по "
    "странам — пока фильтр применяется только к спискам «только в "
    "отечественной/зарубежной литературе» ниже."
)


# ─── build_gap_rows ──────────────────────────────────────────────────────
# Назначение: строки таблицы карты пробелов — добавляет флаг is_gap
#   (n_sources == 0) для подсветки в UI (st.dataframe/условное форматирование).
# Входные связи: GapReport.cells
# Выходные данные: list[dict] — material, process, condition, n_sources, is_gap
# Уровень: ✅ реализовано (A-13)
def build_gap_rows(report: GapReport) -> list[dict]:
    return [
        {
            "material": cell.material,
            "process": cell.process,
            "condition": cell.condition,
            "n_sources": cell.n_sources,
            "is_gap": cell.n_sources == 0,
        }
        for cell in report.cells
    ]


# ─── select_geography_topics ─────────────────────────────────────────────
# Назначение: по значению гео-фильтра сайдбара («ru» | «foreign» | «all»)
#   решает, какие из блоков only_ru/only_foreign показывать — «all» показывает
#   оба (с подписями), «ru»/«foreign» — только соответствующий список.
# Входные связи: GapReport, geography_filter ("ru"|"foreign"|"all")
# Выходные данные: dict[заголовок блока -> список тем]; пустой список темы —
#   блок опускается вызывающим кодом (пустых заголовков без данных не рисуем)
# Уровень: ✅ реализовано (A-13)
def select_geography_topics(report: GapReport, geography_filter: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    if geography_filter in ("ru", "all"):
        blocks["Только в отечественной литературе"] = list(report.only_ru)
    if geography_filter in ("foreign", "all"):
        blocks["Только в зарубежной литературе"] = list(report.only_foreign)
    return blocks
