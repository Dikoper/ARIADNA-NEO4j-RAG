"""Тесты ingest/normalize.py: чистка колонтитулов PDF, пробелов, порог INGEST-002."""
from __future__ import annotations

from ariadna.ingest.convert import convert_pdf
from ariadna.ingest.normalize import (
    clean_whitespace,
    is_too_short,
    normalize_document,
    strip_pdf_boilerplate,
)


# Назначение: повторяющийся заголовок/футер-номер убирается со всех страниц,
#   уникальный текст страницы остаётся.
# Уровень: ✅ реализовано (A-02 tests)
def test_strip_pdf_boilerplate_removes_repeated_header_and_page_number():
    header = "Журнал металлургических исследований №3"
    pages = [f"{header}\nУникальный текст страницы {i}.\n{i + 1}" for i in range(6)]
    cleaned = strip_pdf_boilerplate(pages)
    assert header not in cleaned
    for i in range(6):
        assert f"Уникальный текст страницы {i}." in cleaned
    # номера страниц (чистые цифры-строки, footer) тоже вычищены
    assert "\n1\n" not in ("\n" + cleaned + "\n")


# Назначение: колонтитул с малой долей повторения (< порога) не считается
#   боилерплейтом и должен остаться — иначе рискуем срезать полезный текст.
# Уровень: ✅ реализовано (A-02 tests)
def test_strip_pdf_boilerplate_keeps_non_repeating_lines():
    pages = [
        "Строка про материал А. Далее текст страницы один.",
        "Совсем другая строка. Далее текст страницы два.",
        "Третья непохожая строка. Далее текст страницы три.",
    ]
    cleaned = strip_pdf_boilerplate(pages)
    for p in pages:
        first_line = p.splitlines()[0]
        assert first_line in cleaned


# Назначение: end-to-end на реальном PDF-фикстуре с колонтитулом — после
#   normalize_document в тексте нет ни одного вхождения заголовка журнала.
# Уровень: ✅ реализовано (A-02 tests)
def test_normalize_document_strips_boilerplate_from_real_pdf(pdf_with_boilerplate):
    raw = convert_pdf(pdf_with_boilerplate)
    text = normalize_document(raw)
    assert "Журнал металлургических исследований" not in text
    assert "Уникальный текст страницы номер 0" in text
    assert "Уникальный текст страницы номер 5" in text


# Назначение: множественные пробелы схлопываются в один, висячие пробелы обрезаются.
# Уровень: ✅ реализовано (A-02 tests)
def test_clean_whitespace_collapses_spaces_and_blank_lines():
    text = "Слово1    слово2  \n\n\n\n   Слово3   \n\t\tСлово4"
    cleaned = clean_whitespace(text)
    assert "  " not in cleaned  # нет двойных пробелов
    assert "\n\n\n" not in cleaned  # не более одной пустой строки подряд
    assert cleaned.startswith("Слово1")
    assert not cleaned.endswith(" ")


# Назначение: спецсимволы не искажаются при схлопывании пробелов.
# Уровень: ✅ реализовано (A-02 tests)
def test_clean_whitespace_preserves_special_chars(special_chars_sentence):
    cleaned = clean_whitespace(special_chars_sentence)
    for token in ("SO₂", "CO₂", "≤ 200 мг/л", "80°C", "200–300 мг/дм³"):
        assert token in cleaned


# Назначение: текст короче MIN_TEXT_CHARS помечается как «слишком короткий» (INGEST-002).
# Уровень: ✅ реализовано (A-02 tests)
def test_is_too_short_below_threshold():
    assert is_too_short("х" * 199) is True
    assert is_too_short("х" * 200) is False


# Назначение: пустой текст (0 символов) тоже считается слишком коротким.
# Уровень: ✅ реализовано (A-02 tests)
def test_is_too_short_empty_text():
    assert is_too_short("") is True
