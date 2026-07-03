"""Тесты ingest/convert.py: конвертация PDF/DOCX/DOCM/PPTX в RawDocument.

Проверяет: текст и мета извлекаются корректно из известных фикстур; спецсимволы
не искажаются на этапе конвертации; битые файлы поднимают ConversionError
(источник INGEST-001).
"""
from __future__ import annotations

import pytest

from ariadna.ingest.convert import (
    ConversionError,
    convert_legacy_doc,
    convert_ooxml_word,
    convert_pdf,
    convert_pptx,
)


# Назначение: DOCX с известными параграфами даёт весь текст и мета (title/author/year).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_ooxml_word_extracts_text_and_meta(docx_known_content, special_chars_sentence):
    raw = convert_ooxml_word(docx_known_content)
    assert "Введение в тему исследования." in raw.text
    assert special_chars_sentence in raw.text
    assert raw.pages is None
    assert raw.meta_title == "Отчёт о лабораторных испытаниях"
    assert raw.meta_authors == ["Иванов И.И.", "Петров П.П."]
    assert raw.meta_year == 2019


# Назначение: спецсимволы (индексы, ≤, °C, en-dash, мг/л, RU/EN) не искажаются в DOCX.
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_ooxml_word_preserves_special_chars(docx_known_content):
    raw = convert_ooxml_word(docx_known_content)
    for token in ("SO₂", "CO₂", "≤ 200 мг/л", "80°C", "200–300 мг/дм³", "electrowinning"):
        assert token in raw.text, f"токен {token!r} потерян/искажён при конвертации DOCX"


# Назначение: .docm (macro-enabled) обрабатывается тем же парсером без ошибок
#   (паспорт: python-docx падает на .docm, наш парсер — нет).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_ooxml_word_handles_docm(docm_macro_enabled):
    raw = convert_ooxml_word(docm_macro_enabled)
    assert "Текст документа с макросами." in raw.text


# Назначение: пустой DOCX (без параграфов) не падает, даёт пустой текст.
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_ooxml_word_empty_document(docx_empty):
    raw = convert_ooxml_word(docx_empty)
    assert raw.text == ""


# Назначение: битый .docx (не zip) поднимает ConversionError (INGEST-001).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_ooxml_word_corrupted_raises(docx_corrupted):
    with pytest.raises(ConversionError):
        convert_ooxml_word(docx_corrupted)


# Назначение: PPTX даёт текст обоих слайдов + мета (title/author), спецсимволы целы.
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_pptx_extracts_text_and_meta(pptx_known_content, special_chars_sentence):
    raw = convert_pptx(pptx_known_content)
    assert "Первый слайд доклада." in raw.text
    assert special_chars_sentence in raw.text
    assert raw.pages is None
    assert raw.meta_title == "Доклад на конференции"
    assert raw.meta_authors == ["Сидоров С.С."]


# Назначение: PDF даёт постраничный текст (pages != None), меты title/author/year.
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_pdf_extracts_pages_and_meta(pdf_special_chars):
    raw = convert_pdf(pdf_special_chars)
    assert raw.pages is not None
    assert len(raw.pages) == 2
    assert raw.meta_title == "Спецсимволы 2021"
    assert raw.meta_authors == ["Кузнецов К.К."]
    assert "серной кислоты" in raw.text


# Назначение: PDF без текстового слоя конвертируется без ошибки, но текст пуст
#   (порог INGEST-002 проверяется дальше в normalize.is_too_short, не здесь).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_pdf_no_text_layer_returns_empty_text(pdf_no_text_layer):
    raw = convert_pdf(pdf_no_text_layer)
    assert raw.text.strip() == ""


# Назначение: битый .pdf (не открывается PyMuPDF) поднимает ConversionError (INGEST-001).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_pdf_corrupted_raises(pdf_corrupted):
    with pytest.raises(ConversionError):
        convert_pdf(pdf_corrupted)


# Назначение: настоящий .doc (сконвертированный из docx_known_content тем же
#   soffice-путём, что и код) даёт текст известного содержимого через
#   convert_legacy_doc → convert_ooxml_word; закрывает открытый вопрос из
#   worklogs/ingest.md о доступности soffice (здесь: доступен, путь работает).
# Уровень: ✅ реализовано (A-02 tests)
def test_convert_legacy_doc_roundtrip_via_soffice(doc_legacy_from_soffice):
    raw = convert_legacy_doc(doc_legacy_from_soffice)
    assert "Введение в тему исследования" in raw.text
    assert "серной кислоты" in raw.text
    assert raw.pages is None


# Назначение: ДОКУМЕНТИРУЕТ найденный баг (не заявленное поведение): для .doc,
#   который не является реальным Word-документом, ожидание по ERRORS.md
#   (INGEST-001 «файл не сконвертирован в текст») НЕ выполняется — soffice
#   headless в autodetect-режиме молча импортирует произвольные байты как
#   plain-text и возвращает "успешный" .docx с этим же мусором внутри вместо
#   ошибки конвертации. convert_legacy_doc (convert.py) полагается только на
#   ненулевой exit code / отсутствие выходного файла — оба условия здесь не
#   срабатывают. См. отчёт module-tester: этот путь пропускает мусорный .doc
#   в pipeline как "converted" (перехватится дальше только если результат
#   короче MIN_TEXT_CHARS — не гарантировано для длинного мусора).
# Уровень: ✅ реализовано (A-02 tests) — тест фиксирует ФАКТИЧЕСКОЕ поведение
def test_convert_legacy_doc_corrupted_is_silently_accepted_by_soffice(tmp_path, has_soffice):
    if not has_soffice:
        pytest.skip("soffice недоступен в этом окружении")
    bad_doc = tmp_path / "corrupted.doc"
    bad_doc.write_bytes(b"not a real doc file at all, just garbage")
    raw = convert_legacy_doc(bad_doc)  # не поднимает ConversionError — см. комментарий выше
    assert raw.text == "not a real doc file at all, just garbage"
