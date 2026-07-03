"""Таблицы единиц/операторов/параметров и скомпилированные regex для rules.py.

Вход: не имеет — чистые данные + regex, скомпилированные один раз при импорте.
Выход: константы, потребляемые ТОЛЬКО `extraction/rules.py` (внутренний модуль пакета,
не публичный API extraction — публичная точка входа — `rules.extract_constraints`).
Зависимости: re (stdlib), contracts.CompareOp.
Инвариант №3: числа/единицы — только эти правила, никакой LLM в пути извлечения.
Паспорт: docs/dev/modules/extraction.md.
"""
from __future__ import annotations

import re
from typing import Callable

from ariadna.contracts import CompareOp

# ─── NUMBER_RE_STR ─────────────────────────────────────────────────────
# Назначение: число — целое/десятичное (запятая ИЛИ точка), тысячи через
#   пробел/неразрывный пробел («10 000», «1,5», «300»). Альтернатива с
#   разрядами ставится первой: без неё «2024 2025» ошибочно склеилось бы
#   в «2024202» (после «2024» разряд «5» неполный по длине — не 3 цифры,
#   поэтому первая альтернатива на нём не срабатывает и берётся вторая).
NUMBER_RE_STR = r"\d{1,3}(?:[ \u00A0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"

# ─── UNIT_TABLE ─────────────────────────────────────────────────────────
# Назначение: сырая единица (regex) → (каноническая единица, множитель к канону).
# Решение (worklog A-07, 2026-07-03): концентрации канонизируются в мг/л —
#   мг/дм³ есть синоним мг/л (дм³ = л, множитель 1), г/л и г/дм³ пересчитываются
#   ×1000 в мг/л. Остальные единицы канон = как в тексте (унифицируется только
#   написание: «м3/ч» → «м³/ч», «градусов» → «°C»).
UNIT_TABLE: list[tuple[str, str, float]] = [
    (r"мг\s*/\s*дм\s*3|мг\s*/\s*дм\s*³", "мг/л", 1.0),
    (r"мг\s*/\s*л", "мг/л", 1.0),
    (r"г\s*/\s*дм\s*3|г\s*/\s*дм\s*³", "мг/л", 1000.0),
    (r"г\s*/\s*л", "мг/л", 1000.0),
    (r"°\s*[CС]|градус(?:ов|а|ы)?(?:\s+Цельси\w*)?", "°C", 1.0),
    (r"м\s*3\s*/\s*ч|м\s*³\s*/\s*ч", "м³/ч", 1.0),
    (r"т\s*/\s*сут", "т/сут", 1.0),
    (r"%|процент(?:ов|а|ы)?", "%", 1.0),
]
UNIT_ALT_STR = "|".join(f"(?:{pattern})" for pattern, _, _ in UNIT_TABLE)

# ─── RANGE_DASH_RE / RANGE_OT_DO_RE / SINGLE_RE ─────────────────────────
# Назначение: диапазон «200–300 мг/л» (дефис/тире/минус, опц. «от» спереди),
#   диапазон «от 200 до 300 мг/л», одиночное значение с опц. явным знаком
#   сравнения (<,>,≤,≥) — все требуют единицу из UNIT_TABLE рядом с числом.
# Ограничение: отрицательные числа (например «−5 °C») не поддержаны — знак
#   «минус» перед числом неотличим от тире диапазона (известная граница).
RANGE_DASH_RE = re.compile(
    rf"(?:от\s+)?(?P<v1>{NUMBER_RE_STR})\s*[-–—−]\s*(?P<v2>{NUMBER_RE_STR})\s*(?P<unit>{UNIT_ALT_STR})",
    re.IGNORECASE,
)
RANGE_OT_DO_RE = re.compile(
    rf"от\s+(?P<v1>{NUMBER_RE_STR})\s+до\s+(?P<v2>{NUMBER_RE_STR})\s*(?P<unit>{UNIT_ALT_STR})",
    re.IGNORECASE,
)
SINGLE_RE = re.compile(
    rf"(?P<sign>[<>≤≥])?\s*(?P<num>{NUMBER_RE_STR})\s*(?P<unit>{UNIT_ALT_STR})",
    re.IGNORECASE,
)

# ─── OPERATOR_RULES ──────────────────────────────────────────────────────
# Назначение: фраза-оператор слева от числа → CompareOp. Отрицательные формы
#   («не более», «не ниже» и т.п.) идут ПЕРЕД базовыми словами: при равном
#   правом крае совпадения (оператор «до» вложен как суффикс в «не более» —
#   нет, но «более»/«ниже» — суффиксы «не более»/«не ниже») побеждает первая
#   найденная по списку запись — см. _detect_operator (rules.py).
# Решение: «не ниже»/«не выше» — отрицание переворачивает направление
#   («не ниже 60°C» = «не менее 60°C» = GE), это и требует эталонный пример.
OPERATOR_RULES: list[tuple[re.Pattern[str], CompareOp]] = [
    (re.compile(r"не\s+более", re.IGNORECASE), CompareOp.LE),
    (re.compile(r"не\s+менее", re.IGNORECASE), CompareOp.GE),
    (re.compile(r"не\s+ниже", re.IGNORECASE), CompareOp.GE),
    (re.compile(r"не\s+выше", re.IGNORECASE), CompareOp.LE),
    (re.compile(r"не\s+превыша\w*", re.IGNORECASE), CompareOp.LE),
    (re.compile(r"\bдо\b", re.IGNORECASE), CompareOp.LE),
    (re.compile(r"\bменее\b", re.IGNORECASE), CompareOp.LT),
    (re.compile(r"\bниже\b", re.IGNORECASE), CompareOp.LT),
    (re.compile(r"\bсвыше\b", re.IGNORECASE), CompareOp.GT),
    (re.compile(r"\bболее\b", re.IGNORECASE), CompareOp.GT),
    (re.compile(r"\bвыше\b", re.IGNORECASE), CompareOp.GT),
    (re.compile(r"\bот\b", re.IGNORECASE), CompareOp.GE),
    (re.compile(r"составля\w*|равн\w*", re.IGNORECASE), CompareOp.EQ),
]

# ─── PARAM_KEYWORDS ───────────────────────────────────────────────────────
# Назначение: ключевые слова параметра слева от числа → каноническое имя
#   параметра (эвристика по ближайшему существительному-параметру).
ParamResolver = Callable[[re.Match[str]], str]
PARAM_KEYWORDS: list[tuple[re.Pattern[str], ParamResolver]] = [
    # «содержание X» — первым: при равном правом крае совпадения с «X» (см.
    # _find_param, rules.py) должна победить более информативная форма.
    (
        re.compile(r"содержани\w*\s+([A-Za-zА-Яа-яЁё]+)", re.IGNORECASE),
        lambda m: f"содержание {m.group(1)}",
    ),
    (re.compile(r"минерализаци\w*", re.IGNORECASE), lambda m: "минерализация"),
    (re.compile(r"сульфат\w*", re.IGNORECASE), lambda m: "сульфаты"),
    (re.compile(r"хлорид\w*", re.IGNORECASE), lambda m: "хлориды"),
    (re.compile(r"\bCa\b|кальци\w*", re.IGNORECASE), lambda m: "кальций"),
    (re.compile(r"\bMg\b|магни\w*", re.IGNORECASE), lambda m: "магний"),
    (re.compile(r"\bNa\b|натри\w*", re.IGNORECASE), lambda m: "натрий"),
    (re.compile(r"сухо(?:й|го)\s+остат\w*", re.IGNORECASE), lambda m: "сухой остаток"),
    (re.compile(r"температур\w*", re.IGNORECASE), lambda m: "температура"),
    (re.compile(r"скорост\w*\s+потока", re.IGNORECASE), lambda m: "скорость потока"),
    (re.compile(r"расход\w*", re.IGNORECASE), lambda m: "расход"),
    (re.compile(r"давлени\w*", re.IGNORECASE), lambda m: "давление"),
    (re.compile(r"влажност\w*", re.IGNORECASE), lambda m: "влажность"),
    (re.compile(r"плотност\w*", re.IGNORECASE), lambda m: "плотность"),
    (re.compile(r"производительност\w*", re.IGNORECASE), lambda m: "производительность"),
    (re.compile(r"глубин\w*", re.IGNORECASE), lambda m: "глубина"),
    (re.compile(r"концентраци\w*", re.IGNORECASE), lambda m: "концентрация"),
]
