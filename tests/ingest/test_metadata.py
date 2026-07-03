"""Тесты ingest/metadata.py: detect_lang и сборка DocumentMeta (title/year/lang приоритеты)."""
from __future__ import annotations

from ariadna.contracts import Geography, Lang
from ariadna.ingest.convert import RawDocument
from ariadna.ingest.discover import DiscoveredFile
from ariadna.ingest.metadata import build_document_meta, detect_lang
from pathlib import Path


# Назначение: преимущественно кириллический текст -> Lang.RU.
# Уровень: ✅ реализовано (A-02 tests)
def test_detect_lang_russian():
    assert detect_lang("Это полностью русский текст без иностранных слов совсем.") == Lang.RU


# Назначение: преимущественно латинский текст -> Lang.EN.
# Уровень: ✅ реализовано (A-02 tests)
def test_detect_lang_english():
    assert detect_lang("This is a fully English sentence with no other alphabet at all.") == Lang.EN


# Назначение: сопоставимая доля кириллицы и латиницы -> Lang.MIXED.
# Уровень: ✅ реализовано (A-02 tests)
def test_detect_lang_mixed():
    text = "Электроэкстракция electrowinning процесс process метод method анализ analysis"
    assert detect_lang(text) == Lang.MIXED


# Назначение: текст без букв (только цифры/символы) -> дефолт Lang.RU по контракту.
# Уровень: ✅ реализовано (A-02 tests)
def test_detect_lang_no_letters_defaults_to_ru():
    assert detect_lang("123 456 -- 200–300 ≤ ₂ ₃") == Lang.RU


# Назначение: пустая строка не падает, даёт дефолт RU.
# Уровень: ✅ реализовано (A-02 tests)
def test_detect_lang_empty_string():
    assert detect_lang("") == Lang.RU


def _fake_item(stem: str) -> DiscoveredFile:
    return DiscoveredFile(
        path=Path(f"/data/Обзоры/{stem}.pdf"),
        source_folder="Обзоры",
        ext=".pdf",
        doc_id="deadbeef00000001",
        rel_path=f"Обзоры/{stem}.pdf",
    )


# Назначение: title берётся из свойств документа, если не заглушка (не имя файла).
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_title_from_doc_properties():
    item = _fake_item("Some_File_Name_2020")
    raw = RawDocument(text="", pages=None, meta_title="Настоящее название", meta_authors=["А. Б."], meta_year=2020)
    meta = build_document_meta(item, raw, "текст документа " * 20)
    assert meta.title == "Настоящее название"


# Назначение: заглушка-title («Document», «Microsoft Word - Document1») игнорируется,
#   используется имя файла (с подчёркиваниями -> пробелы).
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_title_falls_back_to_filename_when_generic():
    item = _fake_item("Otchet_o_rabote_2020")
    raw = RawDocument(text="", pages=None, meta_title="Document", meta_authors=None, meta_year=None)
    meta = build_document_meta(item, raw, "текст документа " * 20)
    assert meta.title == "Otchet o rabote 2020"


# Назначение: приоритет года — имя файла > мета документа > текст.
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_year_priority_filename_over_meta_and_text():
    item = _fake_item("Report_2015")
    raw = RawDocument(text="", pages=None, meta_title="", meta_authors=None, meta_year=2018)
    text = "В тексте упоминается год 2010 в качестве примера. " * 5
    meta = build_document_meta(item, raw, text)
    assert meta.year == 2015


# Назначение: если года нет ни в имени, ни в мета — берём первое упоминание в тексте.
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_year_falls_back_to_text():
    item = _fake_item("Otchet_bez_goda")
    raw = RawDocument(text="", pages=None, meta_title="", meta_authors=None, meta_year=None)
    text = "Исследование проведено в 2017 году совместно с институтом. " * 5
    meta = build_document_meta(item, raw, text)
    assert meta.year == 2017


# Назначение: отсутствие года везде — Optional-поле год = None, а не ошибка.
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_year_none_when_not_found():
    item = _fake_item("Bez_nazvaniya_i_goda")
    raw = RawDocument(text="", pages=None, meta_title="", meta_authors=None, meta_year=None)
    meta = build_document_meta(item, raw, "Текст совсем без чисел похожих на год публикации. " * 5)
    assert meta.year is None


# Назначение: is_core=True и geography=UNKNOWN всегда для документов ingest (вне скоупа геолокации).
# Уровень: ✅ реализовано (A-02 tests)
def test_build_document_meta_defaults():
    item = _fake_item("Doc")
    raw = RawDocument(text="", pages=None, meta_title="", meta_authors=None, meta_year=None)
    meta = build_document_meta(item, raw, "Текст документа. " * 20)
    assert meta.is_core is True
    assert meta.geography == Geography.UNKNOWN
    assert meta.doc_id == item.doc_id
    assert meta.path == item.rel_path
    assert meta.authors == []
