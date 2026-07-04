"""Экспорт результатов демо в Markdown (A-23) — чистые функции без Streamlit.

Вход: `contracts.Answer` (ответ чата с цитатами/противоречиями) или
`contracts.GapReport` (карта пробелов). Выход: готовый Markdown-текст для
кнопки скачивания в `ui/app.py` — закрывает пункт задания «экспорт результатов
с возможностью вставки в презентации и технические задания».

Зависимости: только контракты (pydantic), `ui.citations_view.format_citation`
(единая точка форматирования цитат — тот же вид, что на экране) и
`ui.recommendations_view` (группировка/заголовки/иконки видов рекомендаций —
тот же порядок и подписи, что на экране, A-15). Инвариант: никакой бизнес-
логики и обращений к стенду — форматирование уже полученных данных; время
формирования передаётся снаружи (generated_at), модуль не трогает системные
часы сам (детерминированность для тестов). Секция «Рекомендации» строится из
`Answer.recommendations` — вызывающая сторона (app.py) обязана подложить туда
свежий результат `ui.backend.get_recommendations`, если хочет видеть их в
экспорте (рекомендации считаются на лету и не хранятся в кэше ответов).
"""
from __future__ import annotations

from ariadna.contracts import Answer, GapReport
from ui.citations_view import format_citation
from ui.recommendations_view import KIND_ICONS, KIND_TITLES_RU, group_recommendations

_FOOTER_PREFIX = "_Сформировано системой «Ариадна»"
_NOT_FOUND_NOTE = (
    "> В корпусе не найдено прямого ответа на этот вопрос — возможный пробел "
    "в изученных данных (см. раздел «Карта пробелов»)."
)


# ─── _footer ────────────────────────────────────────────────────────────
# Назначение: единая подпись внизу экспортируемого отчёта; дата — только
#   если передана вызывающим кодом (пустая строка = без даты).
# Уровень: ✅ реализовано (A-23)
def _footer(generated_at: str) -> str:
    suffix = f" · {generated_at}" if generated_at else ""
    return f"{_FOOTER_PREFIX}{suffix}_"


# ─── answer_to_markdown ─────────────────────────────────────────────────
# Назначение: Answer -> Markdown-отчёт: вопрос заголовком, честная пометка
#   «не найдено» (found=False), текст ответа, нумерованные источники (в том
#   же виде, что на экране — format_citation), противоречия с их источниками,
#   рекомендации (У-1, A-15) — те же группы/порядок/иконки, что на экране.
# Входные связи: contracts.Answer (recommendations — см. докстринг модуля);
#   generated_at — строка даты от вызывающего
# Выходные данные: str (Markdown)
# Уровень: ✅ реализовано (A-23, секция рекомендаций — A-15)
def answer_to_markdown(answer: Answer, *, generated_at: str = "") -> str:
    lines: list[str] = [f"# {answer.question}", ""]
    if not answer.found:
        lines += [_NOT_FOUND_NOTE, ""]
    lines += [answer.text, ""]

    if answer.citations:
        lines += ["## Источники", ""]
        lines += [f"{i}. {format_citation(cit)}" for i, cit in enumerate(answer.citations, start=1)]
        lines.append("")

    if answer.contradictions:
        lines += ["## Противоречия в источниках", ""]
        for i, c in enumerate(answer.contradictions, start=1):
            lines.append(f"{i}. «{c.claim_a}» **противоречит** «{c.claim_b}»")
            lines += [f"   - {format_citation(cit)}" for cit in c.citations]
        lines.append("")

    if answer.recommendations:
        lines += ["## Рекомендации", ""]
        for kind, items in group_recommendations(answer.recommendations):
            lines.append(f"### {KIND_TITLES_RU[kind]}")
            for rec in items:
                lines.append(f"- {KIND_ICONS[kind]} **{rec.title}**" + (f" — {rec.reason}" if rec.reason else ""))
                lines += [f"   - {format_citation(cit)}" for cit in rec.citations]
        lines.append("")

    lines.append(_footer(generated_at))
    return "\n".join(lines)


# ─── gap_report_to_markdown ─────────────────────────────────────────────
# Назначение: GapReport -> Markdown-отчёт: таблица ячеек «материал-процесс-
#   условие» (пробелы n_sources=0 помечены), затем списки тем «только в
#   отечественной/зарубежной литературе». Пустые секции честно опускаются.
# Входные связи: contracts.GapReport; generated_at — строка даты
# Выходные данные: str (Markdown)
# Уровень: ✅ реализовано (A-23)
def gap_report_to_markdown(report: GapReport, *, generated_at: str = "") -> str:
    lines: list[str] = ["# Карта пробелов — неизученные комбинации", ""]

    if report.cells:
        lines += [
            "| Материал | Процесс | Условие | Источников | Пробел |",
            "|---|---|---|---|---|",
        ]
        for cell in report.cells:
            gap_mark = "**да**" if cell.n_sources == 0 else "нет"
            lines.append(
                f"| {cell.material} | {cell.process} | {cell.condition or '—'} "
                f"| {cell.n_sources} | {gap_mark} |"
            )
        lines.append("")

    if report.only_ru:
        lines += ["## Темы только в отечественной литературе", ""]
        lines += [f"- {topic}" for topic in report.only_ru]
        lines.append("")

    if report.only_foreign:
        lines += ["## Темы только в зарубежной литературе", ""]
        lines += [f"- {topic}" for topic in report.only_foreign]
        lines.append("")

    lines.append(_footer(generated_at))
    return "\n".join(lines)
