"""Блок «Как система поняла вопрос» (A-24) — чипы разбора вопроса из роутера.

Вход: текст вопроса пользователя. Выход: `contracts.QueryIntent` (через
`ariadna.search.router.route` — детерминированный, без LLM/сети, мгновенно)
и список готовых строк-чипов для отображения: материал/процесс/свойство,
числовые условия («сульфаты ≤ 300 мг/л»), география, период, бейдж
сравнительного режима «Россия vs зарубеж» (У-2).

Зависимости: `ariadna.contracts` (QueryIntent/Geography/CompareOp),
`ariadna.search.router.route` — ленивым импортом с честной деградацией
(недоступен/упал -> intent=None, чипов нет, блок не рисуется — UI-005,
docs/dev/ERRORS.md). Инвариант: чипы строятся ТОЛЬКО из полей QueryIntent —
ничего не выдумывается; пустой разбор -> пустой список (блок опускается).
Паспорт: docs/dev/modules/ui.md.
"""
from __future__ import annotations

from ariadna.contracts import CompareOp, Geography, QueryIntent
from ariadna.logutil import get_logger, log_event, new_run_id

# Русские подписи ролей слотов шаблона (ключи — как в QueryIntent.slots).
_SLOT_ROLE_LABELS_RU: dict[str, str] = {
    "material": "материал",
    "process": "процесс",
    "property": "свойство",
}

# Гео-чипы: только реальные значения фильтра вопроса (GLOBAL/UNKNOWN в
# QueryFilters роутер не выставляет).
_GEOGRAPHY_LABELS_RU: dict[Geography, str] = {
    Geography.RU: "🇷🇺 отечественная практика",
    Geography.FOREIGN: "🌍 зарубежная практика",
}

COMPARE_CHIP = "⚖ сравнение: Россия vs зарубеж"

# Операторы сравнения — типографские знаки для чипов («≤ 300 мг/л», как в
# самом вопросе жюри), а не ASCII-значения CompareOp («<=»).
_OP_SIGNS: dict[CompareOp, str] = {
    CompareOp.LE: "≤",
    CompareOp.GE: "≥",
    CompareOp.LT: "<",
    CompareOp.GT: ">",
}


# ─── _log_chips_unavailable ──────────────────────────────────────────────
# Назначение: лог UI-005 ТОЛЬКО в ветках сбоя — get_logger создаёт файл
#   logs/pipeline/<run_id>.jsonl и держит его открытым; get_intent зовётся
#   на каждый rerun Streamlit, безусловный логгер плодил бы пустые файлы и
#   тёк дескрипторами (находка ревью A-24).
# Уровень: ✅ реализовано (A-24, ревью)
def _log_chips_unavailable(level: str, detail: str) -> None:
    logger = get_logger("ui", new_run_id("ui_"))
    log_event(logger, stage="question_chips", event="UI-005", level=level, detail=detail)


# ─── get_intent ──────────────────────────────────────────────────────────
# Назначение: вопрос -> QueryIntent через детерминированный роутер (A-10);
#   роутер недоступен/упал — None + лог UI-005 (блок чипов честно опускается,
#   ответ чата от этого не зависит). Вызывающая сторона (app.py) обязана
#   кэшировать результат по вопросу — иначе каждый rerun зовёт route(), а тот
#   на вопросах вне шаблонов пишет лог SEARCH-001 с новым run_id (файл на
#   каждый клик).
# Входные связи: текст вопроса
# Выходные данные: contracts.QueryIntent | None
# Уровень: ✅ реализовано (A-24, логгер в ветках сбоя — ревью)
def get_intent(question: str) -> QueryIntent | None:
    try:
        from ariadna.search.router import route  # noqa: PLC0415 — ленивый импорт, как в ui.backend
    except ImportError as exc:
        _log_chips_unavailable("WARNING", f"router недоступен ({str(exc)[:200]}) — блок разбора вопроса скрыт")
        return None
    try:
        return route(question)
    except Exception as exc:  # noqa: BLE001 — разбор вопроса не должен ронять чат
        _log_chips_unavailable("ERROR", f"route упал: {str(exc)[:300]} — блок разбора вопроса скрыт")
        return None


# ─── _format_number ──────────────────────────────────────────────────────
# Назначение: число без лишних хвостов формата («300.0» -> «300», «1.5» — как есть).
# Уровень: ✅ реализовано (A-24)
def _format_number(value: float) -> str:
    return f"{value:g}"


# ─── _numeric_chip ───────────────────────────────────────────────────────
# Назначение: один чип числового условия из NumericConstraint — как в тексте
#   вопроса («сульфаты ≤ 300 мг/л»); диапазон — «параметр 200–300 ед.».
# Входные связи: contracts.NumericConstraint
# Выходные данные: str
# Уровень: ✅ реализовано (A-24)
def _numeric_chip(constraint) -> str:
    value = _format_number(constraint.value)
    if constraint.op == CompareOp.RANGE and constraint.value_max is not None:
        bound = f"{value}–{_format_number(constraint.value_max)}"
    else:
        sign = _OP_SIGNS.get(constraint.op, "")
        bound = f"{sign} {value}" if sign else value
    unit = f" {constraint.unit}" if constraint.unit else ""
    return f"{constraint.param} {bound}{unit}".strip()


# ─── chips_from_intent ───────────────────────────────────────────────────
# Назначение: QueryIntent -> список строк-чипов в фиксированном порядке:
#   слоты (материал/процесс/свойство) -> числовые условия -> география ->
#   период -> бейдж сравнения. Ничего не распознано — пустой список
#   (вызывающий код опускает блок целиком, не рисуя пустую рамку).
# Входные связи: contracts.QueryIntent (route)
# Выходные данные: list[str]
# Уровень: ✅ реализовано (A-24)
def chips_from_intent(intent: QueryIntent) -> list[str]:
    chips: list[str] = []
    for role in _SLOT_ROLE_LABELS_RU:
        value = intent.slots.get(role, "").strip()
        if value:
            chips.append(f"{_SLOT_ROLE_LABELS_RU[role]}: {value}")

    chips.extend(_numeric_chip(c) for c in intent.filters.numeric)

    if intent.filters.geography in _GEOGRAPHY_LABELS_RU:
        chips.append(_GEOGRAPHY_LABELS_RU[intent.filters.geography])

    year_from, year_to = intent.filters.year_from, intent.filters.year_to
    if year_from and year_to:
        chips.append(f"период: {year_from}–{year_to}")
    elif year_from:
        chips.append(f"период: с {year_from} года")
    elif year_to:
        chips.append(f"период: по {year_to} год")

    if intent.compare_geography:
        chips.append(COMPARE_CHIP)
    return chips


# ─── build_question_chips ────────────────────────────────────────────────
# Назначение: точка входа для app.py — вопрос -> (чипы, флаг сравнительного
#   режима). Флаг нужен отдельно: по нему левая колонка добавляет вкладку
#   «Россия vs зарубеж». Роутер недоступен — ([], False).
# Входные связи: текст вопроса
# Выходные данные: (list[str], bool)
# Уровень: ✅ реализовано (A-24)
def build_question_chips(question: str) -> tuple[list[str], bool]:
    intent = get_intent(question)
    if intent is None:
        return [], False
    return chips_from_intent(intent), intent.compare_geography
