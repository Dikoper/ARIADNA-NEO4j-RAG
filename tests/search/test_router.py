"""Тесты search/router.py (A-10): вопрос -> QueryIntent, детерминированно,
офлайн (без Neo4j/Ollama — router.route() чистая функция строка -> QueryIntent).

Покрыто: 4 эталонных запроса TASK.md -> правильные template_id (приёмочный
критерий паспорта модуля); числа из вопроса (extract_constraints, переиспользован,
не дублируется — инвариант №3); год (диапазон/«последние N лет»/«с YYYY»);
compare_geography (У-2, регулярки _COMPARE_MARKERS_RE/_ONLY_FOREIGN_RE/_ONLY_RU_RE
разведены — фикс module-dev, geography=FOREIGN/RU без сравнения теперь достижимо,
«мировая/мировой практика» в любом падеже -> compare=True); канонизация слотов
через ontology (RU/EN синонимы) со стеммингом основы слова (_stem_word/
_term_pattern — фикс module-dev, словоформы вопроса матчят канон в им.падеже);
нерелевантный вопрос -> rag_fallback + SEARCH-001 в логе; QueryIntent.model_validate;
CLI-смоук `python -m ariadna.search.router`.

ИСТОРИЯ: изначально module-tester зафиксировал тестами 3 бага (гео-регулярки
недостижимые ветки, «мировая практика» не матчит предложный падеж, слоты не
матчат словоформы) — module-dev fixer их исправил (worklogs/search.md), тесты
ниже переписаны под ПРАВИЛЬНОЕ (пофикшенное) поведение.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ariadna.contracts import CompareOp, Geography, QueryIntent
from ariadna.logutil import LOG_DIR
from ariadna.search import router

REPO_ROOT = Path(__file__).resolve().parents[2]

# ─── 4 эталонных вопроса жюри (TASK.md, дословно) ──────────────────────────
Q1_DESALINATION = (
    "Какие методы обессоливания воды подходят для обогатительной фабрики, если "
    "исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, а "
    "требуемый сухой остаток — ≤1000 мг/дм³?"
)
Q2_CATHOLYTE = (
    "Какие технические решения организации циркуляции католита при "
    "электроэкстракции никеля описаны в мировой практике, и какая скорость "
    "потока считается оптимальной?"
)
Q3_EXPERIMENTS = (
    "Покажите все эксперименты и публикации по распределению Au, Ag и МПГ между "
    "медным/никелевым штейном и шлаком за последние 5 лет."
)
Q4_MINE_WATER = (
    "Какие способы закачки шахтных вод в глубокие горизонты применялись в России "
    "и за рубежом, и каковы их технико-экономические показатели?"
)


# ══════════════════════ 4 эталонных запроса -> template_id (п.А, приёмка) ══════════════════════

# Назначение: запрос №1 (обессоливание) -> template_id='desalination_methods';
#   QueryIntent проходит валидацию контракта.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_query1_desalination_returns_desalination_template():
    intent = router.route(Q1_DESALINATION, run_id="test_a10_q1")
    assert intent.template_id == "desalination_methods"
    QueryIntent.model_validate(intent.model_dump())


# Назначение: запрос №2 (католит/электроэкстракция) -> template_id='catholyte_circulation'.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_query2_catholyte_returns_catholyte_template():
    intent = router.route(Q2_CATHOLYTE, run_id="test_a10_q2")
    assert intent.template_id == "catholyte_circulation"
    QueryIntent.model_validate(intent.model_dump())


# Назначение: запрос №3 (Au/Ag/МПГ штейн-шлак) -> template_id=
#   'experiments_publications_by_topic'; «за последние 5 лет» -> year_from=2022
#   (текущий год 2026 - 5 + 1, см. _year_from_last_n).
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_query3_experiments_returns_correct_template_and_year_window():
    intent = router.route(Q3_EXPERIMENTS, run_id="test_a10_q3")
    assert intent.template_id == "experiments_publications_by_topic"
    assert intent.filters.year_from == 2022
    assert intent.filters.year_to is None
    QueryIntent.model_validate(intent.model_dump())


# Назначение: запрос №4 (закачка шахтных вод) -> template_id='mine_water_injection';
#   «в России и за рубежом» -> compare_geography=True (У-2).
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_query4_mine_water_returns_correct_template_and_compare_geography():
    intent = router.route(Q4_MINE_WATER, run_id="test_a10_q4")
    assert intent.template_id == "mine_water_injection"
    assert intent.compare_geography is True
    QueryIntent.model_validate(intent.model_dump())


# ══════════════════════ Числовые ограничения (extract_constraints reuse) ══════════════════════

# Назначение: запрос №1 несёт диапазон «200–300 мг/л» -> NumericConstraint
#   RANGE(200, 300) и «≤1000 мг/дм³» -> LE(1000); router не дублирует
#   регекспы extraction/rules, а переиспользует extract_constraints целиком.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_query1_numeric_filters_contain_range_and_le():
    intent = router.route(Q1_DESALINATION)
    numeric = intent.filters.numeric
    assert len(numeric) == 2

    range_c = next(c for c in numeric if c.op == CompareOp.RANGE)
    assert range_c.value == 200.0
    assert range_c.value_max == 300.0
    assert range_c.norm_unit == "мг/л"

    le_c = next(c for c in numeric if c.op == CompareOp.LE)
    assert le_c.value == 1000.0
    assert le_c.norm_unit == "мг/л"  # мг/дм³ канонизируется в мг/л


# ══════════════════════ Годы: диапазон / «последние N лет» / «с YYYY» ══════════════════════

# Назначение: «с 2020» -> year_from=2020, year_to=None.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_since_year_marker_sets_year_from_only():
    intent = router.route("Расскажи про обессоливание с 2020 года")
    assert intent.filters.year_from == 2020
    assert intent.filters.year_to is None


# Назначение: «2015–2020» -> year_from=2015, year_to=2020 (диапазон приоритетнее
#   «последних N лет»/«с YYYY»).
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_year_range_sets_both_bounds():
    intent = router.route("Расскажи про обессоливание за 2015-2020 годы")
    assert intent.filters.year_from == 2015
    assert intent.filters.year_to == 2020


# Назначение: «за последние 5 лет» -> year_from = текущий год - 5 + 1 (2026 -> 2022);
#   дубль приёмочного теста запроса №3, но как самостоятельный юнит-тест правила года.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_last_n_years_uses_current_year_minus_n_plus_one():
    intent = router.route("Что нового про флотацию за последние 5 лет?")
    assert intent.filters.year_from == 2022
    assert intent.filters.year_to is None


# Назначение: вопрос без упоминания года -> year_from/year_to оба None.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_no_year_marker_leaves_both_bounds_none():
    intent = router.route("Как работает флотация медно-никелевой руды?")
    assert intent.filters.year_from is None
    assert intent.filters.year_to is None


# ══════════════════════ compare_geography (У-2) ══════════════════════

# Назначение: явный маркер «в России и за рубежом» -> compare_geography=True,
#   geography=None (нужны обе стороны сравнения) — дубль запроса №4 отдельным юнитом.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_russia_and_abroad_marker_sets_compare_geography_true():
    intent = router.route("Какие методы флотации применяются в России и за рубежом?")
    assert intent.compare_geography is True
    assert intent.filters.geography is None


# Назначение: «мировая практика» (именительный падеж, точное совпадение с
#   _COMPARE_MARKERS_RE) -> compare_geography=True.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_world_practice_marker_sets_compare_geography_true():
    intent = router.route("Какая мировая практика применяется для флотации медно-никелевой руды?")
    assert intent.compare_geography is True


# Назначение: нейтральный вопрос без гео-маркеров -> compare_geography=False,
#   geography=None (никакого гео-фильтра не навязывается).
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_neutral_question_leaves_compare_geography_false():
    intent = router.route("Как работает флотация медно-никелевой руды?")
    assert intent.compare_geography is False
    assert intent.filters.geography is None


# Назначение: ФИКС — _detect_geography теперь ДОСТИГАЕТ geography=FOREIGN с
#   compare_geography=False («сужение без сравнения», заявленное в докстринге
#   функции): _ONLY_FOREIGN_RE больше не пересекается с _COMPARE_MARKERS_RE
#   («миров\w*\s+практик» убран из _ONLY_FOREIGN_RE) — «в зарубежной практике»
#   без упоминания России и без «мировая практика»/«сравни» даёт
#   geography=FOREIGN, compare_geography=False (было: (None, True) — баг,
#   зафиксированный module-tester, см. worklogs/search.md).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №3)
def test_route_foreign_only_marker_sets_geography_foreign_without_compare():
    intent = router.route("Какие методы обессоливания применяются в зарубежной практике?")
    assert intent.compare_geography is False
    assert intent.filters.geography == Geography.FOREIGN


# Назначение: симметричный фикс для «отечественн\w*» — geography=RU,
#   compare_geography=False (ветка была недостижима, см. фикс выше).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №3)
def test_route_ru_only_marker_sets_geography_ru_without_compare():
    intent = router.route("Какие методы обессоливания применяются в отечественной практике?")
    assert intent.compare_geography is False
    assert intent.filters.geography == Geography.RU


# Назначение: «только в России» (без «отечественн\w*», просто упоминание
#   России) -> тоже geography=RU, compare_geography=False — has_russia без
#   has_foreign достаточно (фикс №3, router.py: `if has_russia or has_ru_marker`).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №3)
def test_route_russia_only_mention_without_foreign_sets_geography_ru():
    intent = router.route("Какие методы флотации применяются только в России?")
    assert intent.compare_geography is False
    assert intent.filters.geography == Geography.RU


# Назначение: «за рубежом» (раздельно, не «зарубежн\w*») без упоминания
#   России -> geography=FOREIGN, compare_geography=False — _ONLY_FOREIGN_RE
#   расширен на `за\s+рубеж\w*` (фикс №3), не только слитное «зарубежный».
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №3)
def test_route_abroad_two_word_marker_without_russia_sets_geography_foreign():
    intent = router.route("Какие методы обессоливания применяются за рубежом?")
    assert intent.compare_geography is False
    assert intent.filters.geography == Geography.FOREIGN


# Назначение: ФИКС — дословный текст запроса №2 жюри («…описаны в мировой
#   практике…», предложный падеж) ТЕПЕРЬ матчит _COMPARE_MARKERS_RE (основа
#   «миров\w*\s+практик» вместо жёсткого именительного падежа «мировая\s+
#   практик») -> compare_geography=True для самого запроса №2, как и
#   ожидается по смыслу формулировки (У-2, различение отечественной и
#   зарубежной практики). Было: False — баг, зафиксированный module-tester.
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №4)
def test_route_query2_literal_wording_sets_compare_geography_true():
    intent = router.route(Q2_CATHOLYTE)
    assert intent.compare_geography is True
    assert intent.filters.geography is None


# ══════════════════════ Канонизация слотов через ontology (RU/EN) ══════════════════════

# Назначение: вопрос на EN-синонимах («catholyte», «electrowinning») ->
#   template_id по-прежнему 'catholyte_circulation' (топик матчится по стемам
#   RU/EN в _TOPIC_PATTERNS), а слоты заполняются КАНОНИЧЕСКИМИ RU-именами
#   («католит», «электроэкстракция»), а не сырыми английскими синонимами
#   вопроса — синонимия идёт через ontology.load_synonyms/_detect_slot_value.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_slots_canonicalized_from_english_synonyms():
    question = (
        "What catholyte circulation solutions for nickel electrowinning are "
        "described in world practice, and what is the optimal flow rate?"
    )
    intent = router.route(question)
    assert intent.template_id == "catholyte_circulation"
    assert intent.slots.get("material") == "католит"
    assert intent.slots.get("process") == "электроэкстракция"


# Назначение: вопрос с точным вхождением канонического RU-термина (именительный
#   падеж) -> слот заполняется этим же каноном напрямую (без синонимов) —
#   базовый позитивный случай канонизации, не зависящий от словоформ.
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_slots_filled_from_exact_canonical_term_match():
    intent = router.route("Какие есть публикации про обессоливание?")
    assert intent.template_id == "desalination_methods"
    assert intent.slots.get("process") == "обессоливание"


# Назначение: ФИКС — _detect_slot_value теперь матчит по ОСНОВЕ слова
#   (_stem_word/_term_pattern), а не литеральной подстрокой; словоформа
#   вопроса №1 («обессоливания», родительный падеж) матчит канон
#   («обессоливание», именительный) через общую основу «обессоливани» ->
#   слот 'process' заполняется каноном для дословного эталонного вопроса
#   жюри (было: слот пустой — баг, зафиксированный module-tester).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №2)
def test_route_query1_process_slot_filled_despite_inflected_word_form():
    intent = router.route(Q1_DESALINATION)
    assert intent.template_id == "desalination_methods"
    assert intent.slots.get("process") == "обессоливание"
    assert intent.slots.get("property") == "минерализация"


# Назначение: тот же фикс для запроса №4 — «закачки шахтных вод»/«шахтных
#   вод» (родительный падеж) матчатся по основе с канонами «закачка шахтных
#   вод»/«шахтные воды» (именительный) -> оба слота заполняются каноном (было:
#   оба слота пустые — баг, зафиксированный module-tester).
# Уровень: ✅ реализовано (module-dev fixer, worklogs/search.md — фикс №2)
def test_route_query4_slots_filled_despite_inflected_word_forms():
    intent = router.route(Q4_MINE_WATER)
    assert intent.template_id == "mine_water_injection"
    assert intent.slots.get("process") == "закачка шахтных вод"
    assert intent.slots.get("material") == "шахтные воды"


# ══════════════════════ Нерелевантный вопрос -> rag_fallback (SEARCH-001) ══════════════════════

# Назначение: вопрос вне покрытия тем -> template_id='rag_fallback', событие
#   SEARCH-001 пишется в лог прогона (docs/dev/ERRORS.md).
# Уровень: ✅ реализовано (module-tester A-10)
def test_route_irrelevant_question_returns_rag_fallback_and_logs_search001():
    run_id = "test_a10_irrelevant"
    intent = router.route("Какая погода в Норильске сегодня?", run_id=run_id)
    assert intent.template_id == "rag_fallback"
    assert intent.slots == {}
    QueryIntent.model_validate(intent.model_dump())

    log_text = (LOG_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "SEARCH-001" in log_text


# ══════════════════════ QueryIntent.model_validate — общий контракт ══════════════════════

# Назначение: все ветки route() (4 темы + rag_fallback) отдают структуру,
#   валидную против contracts.QueryIntent — единственного контракта между
#   router и retrieval/templates.
# Уровень: ✅ реализовано (module-tester A-10)
@pytest.mark.parametrize(
    "question",
    [Q1_DESALINATION, Q2_CATHOLYTE, Q3_EXPERIMENTS, Q4_MINE_WATER, "Какая погода в Норильске?"],
)
def test_route_output_always_validates_against_query_intent_contract(question):
    intent = router.route(question)
    validated = QueryIntent.model_validate(intent.model_dump())
    assert validated.question == question


# ══════════════════════ CLI-смоук (п.E) ══════════════════════

# Назначение: `python -m ariadna.search.router "вопрос"` — офлайн (router не
#   ходит в сеть/Neo4j), печатает валидный JSON QueryIntent в stdout, код возврата 0.
# Уровень: ✅ реализовано (module-tester A-10)
def test_cli_router_prints_valid_query_intent_json():
    result = subprocess.run(
        [sys.executable, "-m", "ariadna.search.router", Q2_CATHOLYTE],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["template_id"] == "catholyte_circulation"
    QueryIntent.model_validate(payload)


# Назначение: CLI без аргумента вопроса -> код возврата 2 (argparse-подобная
#   валидация в main()), без падения с трейсбеком.
# Уровень: ✅ реализовано (module-tester A-10)
def test_cli_router_without_question_argument_exits_with_code_2():
    result = subprocess.run(
        [sys.executable, "-m", "ariadna.search.router"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
