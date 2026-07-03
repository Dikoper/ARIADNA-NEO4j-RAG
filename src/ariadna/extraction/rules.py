"""Извлечение числовых ограничений regex-правилами (без LLM) — extraction/rules.py.

Вход: сырой текст (str) — фрагмент чанка/вопроса пользователя (RU).
Выход: `list[contracts.NumericConstraint]` — единственный публичный контракт модуля
на сегодня. Потребители: extraction-пайплайн A-08 (кладёт результат в
`ExtractionResult.constraints`) и search/router A-10 (числовые условия из вопроса,
`QueryFilters.numeric`).
Зависимости: только re (stdlib) + units.py (таблицы/скомпилированные паттерны) +
contracts.py (NumericConstraint, CompareOp). Никаких файлов/сети/логгера в основном
пути — функция чистая (Инвариант №3: числа извлекаются ТОЛЬКО правилами).
Паспорт: docs/dev/modules/extraction.md.
"""
from __future__ import annotations

import re

from ariadna.contracts import CompareOp, NumericConstraint
from ariadna.extraction.units import (
    OPERATOR_RULES,
    PARAM_KEYWORDS,
    RANGE_DASH_RE,
    RANGE_OT_DO_RE,
    SINGLE_RE,
    UNIT_TABLE,
)

# Ширина окна поиска оператора сравнения слева от числа (слов обычно 1-3).
_WINDOW_OP = 30
# Ширина окна поиска слова-параметра слева от числа (существительное может
# стоять дальше оператора — «минерализация ... не более 10 000 мг/л»).
_WINDOW_PARAM = 60
# Границы фрагмента source_text вокруг найденного числа/диапазона.
_CTX_BEFORE = 30
_CTX_AFTER = 15
# Символ-заглушка при маскировании уже использованного диапазоном участка
# текста (не цифра — не даёт одиночному паттерну повторно найти число внутри).
_MASK_CHAR = "#"


# Назначение: строка вида «10 000», «1,5» → float (нормализует пробел/НБП/запятую).
# Уровень: ✅ реализовано (A-07)
def _parse_number(raw: str) -> float:
    cleaned = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    return float(cleaned)


# Назначение: сырая единица из текста → (каноническая единица, множитель к канону)
#   по UNIT_TABLE; None — единица не опознана (не должно случаться, т.к. текст
#   уже прошёл через UNIT_ALT_STR, но проверка защищает от рассинхрона таблиц).
# Уровень: ✅ реализовано (A-07)
def _canon_unit(raw_unit: str) -> tuple[str, float] | None:
    for pattern, norm_unit, factor in UNIT_TABLE:
        if re.fullmatch(pattern, raw_unit, re.IGNORECASE):
            return norm_unit, factor
    return None


# Назначение: обрезает окно контекста по последней границе предложения
#   (.!?;\n), чтобы оператор/параметр из соседнего предложения не подмешался.
# Уровень: ✅ реализовано (A-07)
def _trim_window(window: str) -> str:
    cut = -1
    for i, ch in enumerate(window):
        if ch in ".!?;\n":
            cut = i
    return window[cut + 1:] if cut >= 0 else window


# Назначение: явный знак (<,>,≤,≥) в приоритете; иначе — ближайшая к числу
#   фраза-оператор из OPERATOR_RULES в окне слева; иначе EQ по умолчанию
#   (число без явного оператора трактуется как констатация значения).
# Уровень: ✅ реализовано (A-07)
def _detect_operator(text: str, num_start: int, sign: str | None) -> CompareOp:
    signs = {"<": CompareOp.LT, ">": CompareOp.GT, "≤": CompareOp.LE, "≥": CompareOp.GE}
    if sign:
        return signs[sign]
    window = _trim_window(text[max(0, num_start - _WINDOW_OP):num_start])
    best_end, best_op = -1, None
    for pattern, op in OPERATOR_RULES:
        last = None
        for m in pattern.finditer(window):
            last = m
        if last is not None and last.end() > best_end:
            best_end, best_op = last.end(), op
    return best_op if best_op is not None else CompareOp.EQ


# Назначение: ближайшее к числу слово-параметр из PARAM_KEYWORDS в окне слева;
#   "" — параметр не определён (constraint всё равно возвращается, п.3 задачи).
# Уровень: ✅ реализовано (A-07)
def _find_param(text: str, num_start: int) -> str:
    window = _trim_window(text[max(0, num_start - _WINDOW_PARAM):num_start])
    best_end, best_val = -1, ""
    for pattern, resolve in PARAM_KEYWORDS:
        last = None
        for m in pattern.finditer(window):
            last = m
        if last is not None and last.end() > best_end:
            best_end, best_val = last.end(), resolve(last)
    return best_val


# Назначение: фрагмент исходного текста вокруг совпадения — для верификации
#   (NumericConstraint.source_text).
# Уровень: ✅ реализовано (A-07)
def _context_window(text: str, start: int, end: int) -> str:
    lo = max(0, start - _CTX_BEFORE)
    hi = min(len(text), end + _CTX_AFTER)
    return text[lo:hi].strip()


# ─── extract_constraints ────────────────────────────────────────────────
# Назначение: находит в русском тексте числовые ограничения (единицы мг/л,
#   мг/дм³, г/л, г/дм³, °C/градусы, м³/ч, м3/ч, т/сут, %) и нормализует их
#   к NumericConstraint. Диапазоны («200–300 мг/л», «от 200 до 300 мг/л»,
#   дефис/тире/минус) имеют приоритет над одиночными числами — участок текста
#   диапазона маскируется перед поиском одиночных значений, пересечения
#   исключены. Инвариант №3: числа — только эти правила, LLM не участвует.
# Входные связи: units.py (таблицы/regex), contracts.CompareOp/NumericConstraint
# Выходные данные: list[NumericConstraint], в порядке появления в тексте
# Уровень: ✅ реализовано (A-07)
def extract_constraints(text: str) -> list[NumericConstraint]:
    if not text:
        return []

    found: list[tuple[int, NumericConstraint]] = []
    consumed: list[tuple[int, int]] = []

    range_matches = sorted(
        list(RANGE_OT_DO_RE.finditer(text)) + list(RANGE_DASH_RE.finditer(text)),
        key=lambda m: m.start(),
    )
    last_end = -1
    for m in range_matches:
        if m.start() < last_end:
            continue  # пересечение с уже принятым диапазоном — пропускаем
        canon = _canon_unit(m.group("unit"))
        if canon is None:
            continue
        norm_unit, factor = canon
        v1 = _parse_number(m.group("v1"))
        v2 = _parse_number(m.group("v2"))
        constraint = NumericConstraint(
            param=_find_param(text, m.start()),
            op=CompareOp.RANGE,
            value=v1,
            value_max=v2,
            unit=m.group("unit").strip(),
            norm_value=round(v1 * factor, 6),
            norm_unit=norm_unit,
            source_text=_context_window(text, m.start(), m.end()),
        )
        found.append((m.start(), constraint))
        consumed.append((m.start(), m.end()))
        last_end = m.end()

    masked = list(text)
    for start, end in consumed:
        for i in range(start, end):
            if masked[i].isdigit():
                masked[i] = _MASK_CHAR
    masked_text = "".join(masked)

    for m in SINGLE_RE.finditer(masked_text):
        canon = _canon_unit(m.group("unit"))
        if canon is None:
            continue
        norm_unit, factor = canon
        value = _parse_number(m.group("num"))
        num_start = m.start("num")
        constraint = NumericConstraint(
            param=_find_param(text, num_start),
            op=_detect_operator(text, num_start, m.group("sign")),
            value=value,
            value_max=None,
            unit=m.group("unit").strip(),
            norm_value=round(value * factor, 6),
            norm_unit=norm_unit,
            source_text=_context_window(text, m.start(), m.end()),
        )
        found.append((m.start(), constraint))

    found.sort(key=lambda pair: pair[0])
    return [constraint for _, constraint in found]
