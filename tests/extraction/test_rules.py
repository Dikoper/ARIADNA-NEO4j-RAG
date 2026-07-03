"""Тесты extraction/rules.py: числовые ограничения на эталонных запросах жюри,
единицы/канонизация, операторы, диапазоны, числа, параметры, отрицательные случаи,
чистота/идемпотентность. Известные границы распознавания (worklog A-07, блок
«Открыто») зафиксированы как xfail, а не потеряны молча.
"""
from __future__ import annotations

import pytest

from ariadna.contracts import CompareOp, NumericConstraint
from ariadna.extraction.rules import extract_constraints


# ══════════════════════ 1. Эталонные запросы жюри (TASK.md) ══════════════════════

# Назначение: запрос №1 жюри целиком — диапазон 200–300 мг/л (Ca/Mg/Na/сульфаты/
#   хлориды) + одиночное ограничение «сухой остаток ≤1000 мг/дм³» — оба найдены,
#   без дублирования диапазона как одиночных чисел.
# Уровень: ✅ реализовано (A-07 tests)
def test_query1_full_text_finds_range_and_single():
    text = (
        "Какие методы обессоливания воды подходят для обогатительной фабрики, "
        "если исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, "
        "а требуемый сухой остаток — ≤1000 мг/дм³?"
    )
    result = extract_constraints(text)
    assert len(result) == 2

    range_c, single_c = result[0], result[1]
    assert range_c.op == CompareOp.RANGE
    assert range_c.value == 200.0
    assert range_c.value_max == 300.0
    assert range_c.unit == "мг/л"
    assert range_c.norm_value == 200.0
    assert range_c.norm_unit == "мг/л"

    assert single_c.param == "сухой остаток"
    assert single_c.op == CompareOp.LE
    assert single_c.value == 1000.0
    assert single_c.value_max is None
    assert single_c.unit == "мг/дм³"
    assert single_c.norm_value == 1000.0
    assert single_c.norm_unit == "мг/л"


# Назначение: подстрока запроса №1 — явный знак ≤ перед числом, единица мг/дм³
#   канонизируется в мг/л (множитель 1), параметр «сухой остаток» находится слева.
# Уровень: ✅ реализовано (A-07 tests)
def test_query1_sukhoy_ostatok_le_explicit_sign():
    result = extract_constraints("требуемый сухой остаток — ≤1000 мг/дм³")
    assert len(result) == 1
    c = result[0]
    assert c.param == "сухой остаток"
    assert c.op == CompareOp.LE
    assert c.value == 1000.0
    assert c.value_max is None
    assert c.unit == "мг/дм³"
    assert c.norm_value == 1000.0
    assert c.norm_unit == "мг/л"
    assert "1000" in c.source_text


# Назначение: подстрока запроса №1 — диапазон «200–300 мг/л» после перечисления
#   параметров; параметр НЕ должен разворачиваться в список (известная граница —
#   см. test_param_enumeration_known_limitation_xfail ниже), но диапазон должен
#   остаться одним RANGE-constraint'ом, не парой одиночных чисел.
# Уровень: ✅ реализовано (A-07 tests)
def test_query1_range_200_300_mgl_single_constraint():
    result = extract_constraints("сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л")
    assert len(result) == 1
    c = result[0]
    assert c.op == CompareOp.RANGE
    assert c.value == 200.0
    assert c.value_max == 300.0
    assert c.unit == "мг/л"
    assert c.norm_value == 200.0
    assert c.norm_unit == "мг/л"


# Назначение: запрос №2 жюри (циркуляция католита/электроэкстракция) не содержит
#   числовых значений в самом тексте вопроса — извлечение не должно ничего
#   выдумывать: пустой список.
# Уровень: ✅ реализовано (A-07 tests)
def test_query2_full_text_no_numbers_empty_result():
    text = (
        "Какие технические решения организации циркуляции католита при "
        "электроэкстракции никеля описаны в мировой практике, и какая скорость "
        "потока считается оптимальной?"
    )
    assert extract_constraints(text) == []


# Назначение: запрос №3 жюри содержит голое число «5 лет» без единицы измерения
#   из UNIT_TABLE — период не должен ошибочно распознаваться как физическая
#   величина: пустой список (см. также раздел «Отрицательные случаи»).
# Уровень: ✅ реализовано (A-07 tests)
def test_query3_full_text_bare_years_no_unit_empty_result():
    text = (
        "Покажите все эксперименты и публикации по распределению Au, Ag и МПГ "
        "между медным/никелевым штейном и шлаком за последние 5 лет."
    )
    assert extract_constraints(text) == []


# Назначение: запрос №4 жюри (закачка шахтных вод) не содержит чисел — пустой
#   список.
# Уровень: ✅ реализовано (A-07 tests)
def test_query4_full_text_no_numbers_empty_result():
    text = (
        "Какие способы закачки шахтных вод в глубокие горизонты применялись в "
        "России и за рубежом, и каковы их технико-экономические показатели?"
    )
    assert extract_constraints(text) == []


# ══════════════════════ 2. Единицы и канонизация ══════════════════════

# Назначение: параметризованный набор единиц — сырая запись → (norm_unit, factor)
#   относительно value=200 (или 25 для температуры/влажности), сверено с решением
#   worklog A-07 (концентрации канонизируются в мг/л).
# Уровень: ✅ реализовано (A-07 tests)
@pytest.mark.parametrize(
    "text, expected_unit, expected_norm_unit, expected_norm_value",
    [
        ("сульфаты 200 мг/дм³", "мг/дм³", "мг/л", 200.0),
        ("сульфаты 200 мг/л", "мг/л", "мг/л", 200.0),
        ("сульфаты 200 г/дм³", "г/дм³", "мг/л", 200_000.0),
        ("сульфаты 200 г/л", "г/л", "мг/л", 200_000.0),
        ("температура 25 °C", "°C", "°C", 25.0),
        ("температура 25 °С", "°С", "°C", 25.0),  # кириллическая «С»
        ("температура 25 градусов", "градусов", "°C", 25.0),
        ("расход 200 м³/ч", "м³/ч", "м³/ч", 200.0),
        ("расход 200 м3/ч", "м3/ч", "м³/ч", 200.0),
        ("расход 200 т/сут", "т/сут", "т/сут", 200.0),
        ("влажность 25%", "%", "%", 25.0),
        ("влажность 25 процентов", "процентов", "%", 25.0),
    ],
)
def test_unit_canonization(text, expected_unit, expected_norm_unit, expected_norm_value):
    result = extract_constraints(text)
    assert len(result) == 1
    c = result[0]
    assert c.unit == expected_unit
    assert c.norm_unit == expected_norm_unit
    assert c.norm_value == expected_norm_value


# Назначение: г/дм³ и г/л — множитель ×1000 в мг/л, зафиксирован явным тестом
#   отдельно от параметризации (по заданию — сверить фактическое решение).
# Уровень: ✅ реализовано (A-07 tests)
def test_g_per_l_and_g_per_dm3_factor_is_1000():
    for text in ("реагент 1,5 г/дм³", "реагент 1,5 г/л"):
        result = extract_constraints(text)
        assert len(result) == 1
        assert result[0].value == 1.5
        assert result[0].norm_value == 1500.0
        assert result[0].norm_unit == "мг/л"


# Назначение: мг/дм³ — синоним мг/л, множитель ×1 (без пересчёта).
# Уровень: ✅ реализовано (A-07 tests)
def test_mg_per_dm3_factor_is_1():
    result = extract_constraints("сульфаты 300 мг/дм³")
    assert len(result) == 1
    assert result[0].value == 300.0
    assert result[0].norm_value == 300.0
    assert result[0].norm_unit == "мг/л"


# ══════════════════════ 3. Операторы ══════════════════════

# Назначение: словесные операторы → CompareOp, включая отрицания, где смысл
#   переворачивается («не ниже» = GE, а не LT; «не выше» = LE, а не GT).
# Уровень: ✅ реализовано (A-07 tests)
@pytest.mark.parametrize(
    "text, expected_op",
    [
        ("температура не более 60 °C", CompareOp.LE),
        ("минерализация до 10000 мг/л", CompareOp.LE),
        ("температура не выше 60 °C", CompareOp.LE),
        ("расход не превышает 200 м3/ч", CompareOp.LE),
        ("температура менее 60 °C", CompareOp.LT),
        ("температура ниже 60 °C", CompareOp.LT),
        ("температура не менее 60 °C", CompareOp.GE),
        ("температура не ниже 60 °C", CompareOp.GE),
        ("минерализация от 200 мг/л", CompareOp.GE),
        ("расход свыше 200 м³/ч", CompareOp.GT),
        ("расход более 200 м³/ч", CompareOp.GT),
        ("расход выше 200 м³/ч", CompareOp.GT),
    ],
)
def test_word_operators(text, expected_op):
    result = extract_constraints(text)
    assert len(result) == 1
    assert result[0].op == expected_op


# Назначение: явные знаки сравнения имеют приоритет над словесными правилами.
# Уровень: ✅ реализовано (A-07 tests)
@pytest.mark.parametrize(
    "sign, expected_op",
    [("<", CompareOp.LT), (">", CompareOp.GT), ("≤", CompareOp.LE), ("≥", CompareOp.GE)],
)
def test_explicit_sign_operators(sign, expected_op):
    result = extract_constraints(f"температура {sign}5 °C")
    assert len(result) == 1
    assert result[0].op == expected_op


# Назначение: число с единицей без явного оператора/фразы-сравнения → EQ
#   по умолчанию (констатация значения).
# Уровень: ✅ реализовано (A-07 tests)
def test_default_operator_is_eq():
    result = extract_constraints("влажность 5%")
    assert len(result) == 1
    assert result[0].op == CompareOp.EQ


# Назначение: «составляет»/«равен(на)» — явная констатация равенства → EQ.
# Уровень: ✅ реализовано (A-07 tests)
def test_ravno_sostavlyaet_is_eq():
    result = extract_constraints("расход составляет 150 т/сут")
    assert len(result) == 1
    assert result[0].op == CompareOp.EQ


# ══════════════════════ 4. Диапазоны ══════════════════════

# Назначение: дефис и тире как разделитель диапазона дают одинаковый результат
#   (RANGE, value/value_max), диапазон не дублируется как два одиночных числа.
# Уровень: ✅ реализовано (A-07 tests)
@pytest.mark.parametrize("dash", ["-", "–", "—"])
def test_range_dash_variants(dash):
    result = extract_constraints(f"скорость потока 200{dash}300 м³/ч")
    assert len(result) == 1
    c = result[0]
    assert c.op == CompareOp.RANGE
    assert c.value == 200.0
    assert c.value_max == 300.0
    assert c.unit == "м³/ч"


# Назначение: форма «от X до Y единица» — тоже RANGE, а не GE-одиночное + число.
# Уровень: ✅ реализовано (A-07 tests)
def test_range_ot_do_form():
    result = extract_constraints("от 200 до 300 мг/л")
    assert len(result) == 1
    c = result[0]
    assert c.op == CompareOp.RANGE
    assert c.value == 200.0
    assert c.value_max == 300.0
    assert c.norm_unit == "мг/л"


# Назначение: диапазон не должен породить дополнительные одиночные constraint'ы
#   для чисел 200 и 300 внутри него — итоговый список содержит РОВНО один элемент.
# Уровень: ✅ реализовано (A-07 tests)
def test_range_not_duplicated_as_singles():
    result = extract_constraints("минерализация 200–300 мг/л в пробе")
    assert len(result) == 1
    assert result[0].op == CompareOp.RANGE


# ══════════════════════ 5. Числа: десятичные разделители, тысячи ══════════════════════

# Назначение: десятичная запятая парсится как разделитель дробной части.
# Уровень: ✅ реализовано (A-07 tests)
def test_decimal_comma():
    result = extract_constraints("реагент 1,5 г/дм³")
    assert len(result) == 1
    assert result[0].value == 1.5


# Назначение: десятичная точка тоже поддержана.
# Уровень: ✅ реализовано (A-07 tests)
def test_decimal_dot():
    result = extract_constraints("реагент 1.5 мг/л")
    assert len(result) == 1
    assert result[0].value == 1.5


# Назначение: тысячи через обычный пробел разбираются в единое число.
# Уровень: ✅ реализовано (A-07 tests)
def test_thousands_with_space():
    result = extract_constraints("минерализация до 10 000 мг/л")
    assert len(result) == 1
    assert result[0].value == 10000.0
    assert result[0].norm_value == 10000.0


# Назначение: тысячи через неразрывный пробел (частый артефакт PDF/Word).
# Уровень: ✅ реализовано (A-07 tests)
def test_thousands_with_nbsp():
    result = extract_constraints("минерализация до 10 000 мг/л")
    assert len(result) == 1
    assert result[0].value == 10000.0


# ══════════════════════ 6. Параметр слева от числа ══════════════════════

# Назначение: параметр, стоящий слева от числа в пределах окна поиска,
#   определяется корректно.
# Уровень: ✅ реализовано (A-07 tests)
def test_param_found_to_the_left():
    result = extract_constraints("минерализация до 10 000 мг/л")
    assert len(result) == 1
    assert result[0].param == "минерализация"


# Назначение: если слово-параметр не найдено в окне слева — param="",
#   constraint всё равно возвращается (число/единица важнее параметра).
# Уровень: ✅ реализовано (A-07 tests)
def test_param_not_found_gives_empty_string_but_constraint_returned():
    result = extract_constraints("зафиксировано значение до 10 000 мг/л")
    assert len(result) == 1
    assert result[0].param == ""
    assert result[0].value == 10000.0


# ══════════════════════ 7. Отрицательные случаи ══════════════════════

# Назначение: голое число без единицы измерения (год) не порождает constraint.
# Уровень: ✅ реализовано (A-07 tests)
def test_bare_number_year_no_unit_empty():
    assert extract_constraints("исследование проведено в 2024 году") == []


# Назначение: номер (образец №5) без единицы измерения — тоже не constraint.
# Уровень: ✅ реализовано (A-07 tests)
def test_bare_number_reference_no_unit_empty():
    assert extract_constraints("образец №5 был отобран из скважины") == []


# Назначение: пустая строка → пустой список, без исключений.
# Уровень: ✅ реализовано (A-07 tests)
def test_empty_string_returns_empty_list():
    assert extract_constraints("") == []


# Назначение: связный текст без единственного числа → пустой список.
# Уровень: ✅ реализовано (A-07 tests)
def test_text_without_numbers_returns_empty_list():
    text = "Метод обратного осмоса применяется для очистки шахтных вод от солей."
    assert extract_constraints(text) == []


# Назначение: единица измерения, не входящая в UNIT_TABLE (атмосферы), не
#   должна порождать constraint — число рядом с неизвестной единицей игнорируется.
# Уровень: ✅ реализовано (A-07 tests)
def test_unsupported_unit_atm_gives_empty():
    assert extract_constraints("давление не менее 5 атм") == []


# ══════════════════════ 8. Чистота и идемпотентность ══════════════════════

# Назначение: повторный вызов на одном и том же тексте даёт идентичный
#   результат (пайплайн перезапускаемый, функция чистая — Инвариант №3).
# Уровень: ✅ реализовано (A-07 tests)
def test_idempotent_repeated_call():
    text = (
        "температура не ниже 60 °C, минерализация до 10 000 мг/л, "
        "сульфаты 200–300 мг/л"
    )
    first = extract_constraints(text)
    second = extract_constraints(text)
    assert first == second
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


# Назначение: функция не пишет на диск/в сеть — вызов не должен требовать
#   файловой системы; проверяем отсутствием побочных эффектов через builtins.open,
#   который не должен вызываться в основном пути.
# Уровень: ✅ реализовано (A-07 tests)
def test_no_file_io_side_effects(monkeypatch):
    def _forbidden_open(*args, **kwargs):
        raise AssertionError("extract_constraints не должен открывать файлы")

    monkeypatch.setattr("builtins.open", _forbidden_open)
    result = extract_constraints("температура не ниже 60 °C, минерализация до 10 000 мг/л")
    assert len(result) == 2


# ══════════════════════ 9. Контракт pydantic ══════════════════════

# Назначение: все возвращённые объекты — валидные NumericConstraint, source_text
#   непуст и содержит фрагмент, где найдено совпадение (число присутствует).
# Уровень: ✅ реализовано (A-07 tests)
def test_all_results_are_valid_numeric_constraint_with_nonempty_source_text():
    text = (
        "Какие методы обессоливания воды подходят для обогатительной фабрики, "
        "если исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, "
        "а требуемый сухой остаток — ≤1000 мг/дм³?"
    )
    result = extract_constraints(text)
    assert len(result) > 0
    for c in result:
        assert isinstance(c, NumericConstraint)
        assert c.source_text != ""
        # re-валидация через pydantic не должна падать
        NumericConstraint.model_validate(c.model_dump())
        if c.op == CompareOp.RANGE:
            assert str(int(c.value)) in c.source_text or "200" in c.source_text
        else:
            assert any(part in c.source_text for part in (str(int(c.value)), "1000"))


# ══════════════════════ 10. Известные границы (worklog A-07 «Открыто») ══════════════════════

# Назначение: перечисление параметров («сульфаты, хлориды, Ca, Mg, Na по
#   200–300 мг/л») — эвристика берёт только ближайшее слово (Na → «натрий»),
#   а не разворачивает диапазон на все перечисленные параметры. Зафиксировано
#   как известная граница, а не баг: xfail, чтобы не потерять требование.
# Уровень: 📋 известная граница (A-07, worklog «Открыто»)
@pytest.mark.xfail(reason="A-07 известная граница: param для перечислений не разворачивается в список", strict=True)
def test_param_enumeration_known_limitation_xfail():
    result = extract_constraints("сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л")
    assert len(result) == 1
    assert result[0].param in {"сульфаты", "хлориды", "Ca", "Mg", "Na"}


# Назначение: параметр справа от числа («от 200 до 300 мг/л сульфатов») не
#   подхватывается — эвристика ищет только влево. Известная граница (worklog).
# Уровень: 📋 известная граница (A-07, worklog «Открыто»)
@pytest.mark.xfail(reason="A-07 известная граница: param справа от числа не распознаётся", strict=True)
def test_param_to_the_right_of_number_known_limitation_xfail():
    result = extract_constraints("от 200 до 300 мг/л сульфатов")
    assert len(result) == 1
    assert result[0].param == "сульфаты"


# Назначение: отрицательные числа («−5 °C») не поддержаны — знак минуса
#   неотличим от тире диапазона. Известная граница (worklog).
# Уровень: 📋 известная граница (A-07, worklog «Открыто»)
@pytest.mark.xfail(reason="A-07 известная граница: отрицательные числа не отличимы от тире диапазона", strict=True)
def test_negative_number_known_limitation_xfail():
    result = extract_constraints("температура −5 °C")
    assert len(result) == 1
    assert result[0].op == CompareOp.EQ
    assert result[0].value == -5.0
