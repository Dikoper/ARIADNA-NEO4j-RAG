"""Роутер запросов (A-10): вопрос пользователя -> `contracts.QueryIntent`.

Вход: вопрос (строка, RU/EN). Выход: `contracts.QueryIntent` — template_id
одного из `graph.templates.TEMPLATES` (или `'rag_fallback'`), заполненные
слоты (канонические термины через `graph.ontology.canonical_name`), фильтры
(гео/год/числа через `extraction.rules.extract_constraints` — ПЕРЕИСПОЛЬЗУЕТСЯ,
не дублируется, инвариант №3) и флаг `compare_geography` (У-2).

Зависимости: `ariadna.contracts` (QueryIntent/QueryFilters), `ariadna.
extraction.rules.extract_constraints`, `ariadna.graph.ontology.canonical_name`,
`ariadna.logutil`.
Инварианты: ДЕТЕРМИНИРОВАННЫЙ роутер — только ключевые слова/регулярки, никакой
LLM-классификации (нефункц. требование «ответ 3-5 с» не оставляет бюджета) и
никакого свободного text2cypher (инвариант №4, ARCHITECTURE.md). Модуль не
обращается к Neo4j/Ollama — чистая функция строка -> QueryIntent.
Паспорт: docs/dev/modules/search.md (A-10).
"""
from __future__ import annotations

import functools
import json
import re
import sys
from datetime import date

from ariadna.contracts import Geography, QueryFilters, QueryIntent
from ariadna.extraction.rules import extract_constraints
from ariadna.graph.ontology import load_synonyms
from ariadna.logutil import get_logger, log_event, new_run_id

# Роутер не подобрал шаблон — fallback на векторный RAG (rag_demo, M-01),
# см. docs/dev/ERRORS.md.
SEARCH_NO_TEMPLATE_MATCHED = "SEARCH-001"

# ─── Ключевые темы (RU/EN, регистронезависимо) -> template_id ─────────────
# Порядок словаря = порядок проверки = порядок 4 эталонных запросов TASK.md;
# первое совпадение побеждает (вопрос может случайно задеть 2 темы сразу —
# берём первую по важности постановки задачи).
_TOPIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "desalination_methods",
        re.compile(
            r"обессолив|десалинац|опреснен|обратн\w*\s+осмос|электродиализ|нанофильтрац|"
            r"demineraliz|desalinat|reverse\s+osmosis|electrodialysis|nanofiltration",
            re.IGNORECASE,
        ),
    ),
    (
        "catholyte_circulation",
        re.compile(
            r"католит|электроэкстракц|электровыделен\w*\s+никел|catholyte|electrowinning",
            re.IGNORECASE,
        ),
    ),
    (
        "experiments_publications_by_topic",
        re.compile(
            r"штейн|шлак|мпг\b|платино\w*\s+метал|платиноид|"
            r"\bmatte\b|\bslag\b|\bpgm\b|platinum\s+group",
            re.IGNORECASE,
        ),
    ),
    (
        "mine_water_injection",
        re.compile(
            r"шахтн\w*\s+вод|рудничн\w*\s+вод|закачк\w*.{0,20}(вод|горизонт|скважин)|"
            r"глубок\w*\s+горизонт|mine\s*.?water|underground\s+injection",
            re.IGNORECASE,
        ),
    ),
]

# ─── Сравнительные маркеры (У-2) — не завязаны на конкретную тему ─────────
# ФИКС (module-dev fixer, worklogs/search.md): раньше _COMPARE_MARKERS_RE
# дословно дублировал подстроки «отечественн\w*»/«зарубежн\w*» из
# _ONLY_RU_RE/_ONLY_FOREIGN_RE — любой гео-маркер сразу давал compare=True,
# ветки «гео-фильтр без сравнения» ниже были недостижимы (баг tester'а).
# Теперь _COMPARE_MARKERS_RE — ТОЛЬКО подлинно сравнительные формулировки:
# «в России и за рубежом», «мировая/мировой практика» (падежные формы —
# основа «миров\w*», фикс п.4: «в мировой практике» из запроса №2 жюри тоже
# матчится), «мировой опыт», явное «сравни». «мировая практика» трактуется
# как сравнительная формулировка по смыслу задания (интент — «показать и
# отечественную, и мировую практику»), а не просто гео-сужение на «зарубеж».
_COMPARE_MARKERS_RE = re.compile(
    r"в\s+росси\w*\s+и\s+за\s+рубеж|миров\w*\s+практик|миров\w*\s+опыт|сравни",
    re.IGNORECASE,
)
# «только зарубежная практика»/«за рубежом» без упоминания России — сузить
# сразу гео-фильтром (без сравнения); симметрично для «только отечественная».
# Разведены с _COMPARE_MARKERS_RE (см. выше) — «миров\w*\s+практик» здесь
# больше НЕ участвует, иначе «мировая практика» снова давала бы geography=
# FOREIGN вместо compare=True.
_ONLY_FOREIGN_RE = re.compile(r"зарубежн\w*|за\s+рубеж\w*", re.IGNORECASE)
_ONLY_RU_RE = re.compile(r"отечественн\w*|российск\w*\s+практик", re.IGNORECASE)
_RUSSIA_MENTION_RE = re.compile(r"\bросси\w*\b", re.IGNORECASE)

# ─── Год: диапазон, «последние N лет», «с YYYY» ───────────────────────────
_YEAR_RANGE_RE = re.compile(r"(?P<y1>(19|20)\d{2})\s*[–\-—]\s*(?P<y2>(19|20)\d{2})")
_LAST_N_YEARS_RE = re.compile(r"последн\w*\s+(?P<n>\d+)\s*(лет|год\w*)", re.IGNORECASE)
_SINCE_YEAR_RE = re.compile(r"\bс\s+(?P<y>(19|20)\d{2})\b")

# Дефолтные канонические термины для заполнения слотов по template_id —
# должны совпадать с graph.templates._TEMPLATE_DEFAULT_CANONICALS (описывают
# ОДНО и то же соответствие «шаблон -> роли слотов», но router строит
# QueryIntent.slots как dict[str, str] — контракт допускает только строку на
# ключ, поэтому здесь один (первый подходящий) канон на роль, а полное
# разворачивание в синонимы делает graph.templates._expand_terms).
_SLOT_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "desalination_methods": {
        "process": ["обессоливание", "обратный осмос", "электродиализ", "нанофильтрация"],
        "property": ["минерализация"],
    },
    "catholyte_circulation": {
        "material": ["католит"],
        "process": ["электроэкстракция", "электролиз"],
    },
    "experiments_publications_by_topic": {
        "material": ["штейн", "шлак", "платиноиды", "золото", "серебро"],
    },
    "mine_water_injection": {
        "process": ["закачка шахтных вод"],
        "material": ["шахтные воды"],
    },
}


# Назначение: грубая основа слова — усекает окончание, чтобы регулярка
#   `основа\w*` матчила словоформы («обессоливание»/«обессоливания»,
#   «шахтные»/«шахтных») — тот же приём, что уже используется вручную в
#   _TOPIC_PATTERNS («обессолив», «шахтн\w*\s+вод»). Короткие слова (≤3
#   символа, обычно уже основа/предлог) не усекаются.
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №2)
def _stem_word(word: str) -> str:
    if len(word) > 5:
        return word[:-2]
    if len(word) > 3:
        return word[:-1]
    return word


# Назначение: компилирует термин (канон или синоним, возможно многословный:
#   «закачка шахтных вод») в регулярку по основам слов, соединённым `\s+` —
#   допускает любые окончания каждого слова в исходном порядке. Кешируется
#   (термины повторяются между вызовами _detect_slot_value в одном прогоне).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №2)
@functools.lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern:
    words = term.strip().lower().split()
    stems = [re.escape(_stem_word(w)) + r"\w*" for w in words if w]
    return re.compile(r"\b" + r"\s+".join(stems), re.IGNORECASE)


# Назначение: находит в тексте вопроса упоминание одного из канонических
#   терминов-кандидатов — по самому канону ИЛИ любому его синониму из
#   synonyms.yaml (RU/EN, без учёта регистра), матчинг по ОСНОВАМ слов
#   (_term_pattern/_stem_word), а не литеральной подстрокой — словоформа
#   вопроса («обессоливания», родительный падеж) матчит канон
#   («обессоливание», именительный) через общую основу «обессоливани».
#   Первый подходящий кандидат по порядку списка; ничего не найдено -> ""
#   (шаблон подставит свой дефолт, см. graph.templates._TEMPLATE_DEFAULT_CANONICALS).
# Уровень: ✅ реализовано (A-10, worklogs/search.md; стемминг — фикс module-dev, №2)
def _detect_slot_value(question_lower: str, candidates: list[str]) -> str:
    synonyms_db = load_synonyms()
    for canon in candidates:
        record = synonyms_db.get(canon, {})
        terms = [canon, *(s.strip() for s in record.get("synonyms", []))]
        if any(_term_pattern(term).search(question_lower) for term in terms if term):
            return canon
    return ""


# Назначение: заполняет QueryIntent.slots для распознанного template_id —
#   по каждой роли (process/material/property) ищет явное упоминание среди
#   кандидатов шаблона в тексте вопроса.
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def _build_slots(template_id: str, question_lower: str) -> dict[str, str]:
    roles = _SLOT_CANDIDATES.get(template_id, {})
    slots: dict[str, str] = {}
    for role, candidates in roles.items():
        value = _detect_slot_value(question_lower, candidates)
        if value:
            slots[role] = value
    return slots


# Назначение: определяет template_id по ключевым темам вопроса — первое
#   совпадение регулярки из _TOPIC_PATTERNS; None — нет совпадения (fallback).
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def _detect_template(question: str) -> str | None:
    for template_id, pattern in _TOPIC_PATTERNS:
        if pattern.search(question):
            return template_id
    return None


# Назначение: год «начала окна» из «за последние N лет» — текущий год минус
#   N плюс единица (пример постановки: 2026-5+1=2022, включительно N лет).
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def _year_from_last_n(n: int, today: date | None = None) -> int:
    current_year = (today or date.today()).year
    return current_year - n + 1


# Назначение: извлекает QueryFilters.year_from/year_to из текста вопроса —
#   диапазон «YYYY–YYYY» приоритетнее «последних N лет», которое приоритетнее
#   «с YYYY»; ничего не найдено -> оба None.
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def _extract_year_filters(question: str) -> tuple[int | None, int | None]:
    m = _YEAR_RANGE_RE.search(question)
    if m:
        return int(m.group("y1")), int(m.group("y2"))
    m = _LAST_N_YEARS_RE.search(question)
    if m:
        return _year_from_last_n(int(m.group("n"))), None
    m = _SINCE_YEAR_RE.search(question)
    if m:
        return int(m.group("y")), None
    return None, None


# Назначение: гео-фильтр/флаг сравнения по маркерам текста. Явный сравнительный
#   маркер («в России и за рубежом», «мировая/мировой практика/опыт», «сравни»)
#   -> compare_geography=True, geography=None (нужны обе стороны), НЕЗАВИСИМО
#   от отдельных гео-упоминаний. Иначе: упомянуты и Россия(-йская практика), и
#   «зарубеж» одновременно (без явной сравнительной фразы) -> тоже compare=True
#   (обе стороны интересуют). Только «зарубеж»/«за рубежом» без России ->
#   geography=FOREIGN, compare=False. Только «отечественная практика»/просто
#   упоминание России без «зарубеж» -> geography=RU, compare=False. Иначе —
#   без гео-фильтра.
#   ФИКС (module-dev fixer): раньше _COMPARE_MARKERS_RE дублировал маркеры
#   _ONLY_FOREIGN_RE/_ONLY_RU_RE -> ветки geography=FOREIGN/RU ниже были
#   недостижимы (любой гео-маркер сразу давал compare=True). Регулярки
#   разведены (см. блок выше) — ветки теперь достижимы.
# Уровень: ✅ реализовано (A-10, worklogs/search.md; фикс мёртвого кода — module-dev fixer)
def _detect_geography(question: str) -> tuple[Geography | None, bool]:
    has_russia = bool(_RUSSIA_MENTION_RE.search(question))
    has_foreign = bool(_ONLY_FOREIGN_RE.search(question))
    has_ru_marker = bool(_ONLY_RU_RE.search(question))
    explicit_compare = bool(_COMPARE_MARKERS_RE.search(question))

    if explicit_compare:
        return None, True
    if has_foreign and (has_russia or has_ru_marker):
        return None, True
    if has_foreign:
        return Geography.FOREIGN, False
    if has_russia or has_ru_marker:
        return Geography.RU, False
    return None, False


# ─── route ──────────────────────────────────────────────────────────────
# Назначение: главная точка входа модуля — вопрос -> QueryIntent; детерминиро-
#   ванно (ключевые слова/регулярки), без обращения к LLM/сети (инвариант №4
#   и нефункц. требование «ответ 3-5 с»). Нет совпадения темы -> template_id
#   ='rag_fallback' + лог SEARCH-001 (чисто векторный путь, search/rag_demo).
# Входные связи: вопрос пользователя (строка); extraction.rules.
#   extract_constraints (числа); graph.ontology.canonical_name (слоты)
# Выходные данные: contracts.QueryIntent
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def route(question: str, *, run_id: str | None = None) -> QueryIntent:
    question_lower = question.lower()
    template_id = _detect_template(question)

    numeric = extract_constraints(question)
    year_from, year_to = _extract_year_filters(question)
    geography, compare_geography = _detect_geography(question)

    filters = QueryFilters(
        geography=geography,
        year_from=year_from,
        year_to=year_to,
        numeric=numeric,
    )

    if template_id is None:
        run_id = run_id or new_run_id("router_")
        logger = get_logger("search", run_id)
        log_event(
            logger, stage="router", event=SEARCH_NO_TEMPLATE_MATCHED, level="WARNING",
            detail=f"вопрос вне покрытия шаблонов, fallback=rag_fallback: {question[:200]}",
        )
        return QueryIntent(
            question=question, template_id="rag_fallback", slots={},
            filters=filters, compare_geography=compare_geography,
        )

    slots = _build_slots(template_id, question_lower)
    return QueryIntent(
        question=question, template_id=template_id, slots=slots,
        filters=filters, compare_geography=compare_geography,
    )


# ─── main ───────────────────────────────────────────────────────────────────
# Назначение: CLI-смоук — `python -m ariadna.search.router "вопрос"` печатает
#   QueryIntent в JSON.
# Входные связи: sys.argv[1] — вопрос
# Выходные данные: нет (печать JSON QueryIntent в stdout)
# Уровень: ✅ реализовано (A-10, worklogs/search.md)
def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python -m ariadna.search.router \"вопрос\"", file=sys.stderr)
        raise SystemExit(2)
    intent = route(sys.argv[1])
    print(intent.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
