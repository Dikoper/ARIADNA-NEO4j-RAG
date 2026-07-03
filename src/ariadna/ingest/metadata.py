"""Извлечение метаданных документа: title/authors/year/lang → DocumentMeta.

Вход: `discover.DiscoveredFile` (путь, doc_id, папка), `convert.RawDocument`
(свойства офисного документа/PDF), нормализованный текст из `normalize.py`.
Выход: `contracts.DocumentMeta`. Порядок приоритета источников — паспорт
модуля: имя файла → свойства документа → первая страница текста; отсутствие
значения — не ошибка (поля Optional в контракте).
"""
from __future__ import annotations

import re
from datetime import datetime

from ariadna.contracts import DocumentMeta, Geography, Lang
from ariadna.ingest.config import GENERIC_TITLES
from ariadna.ingest.convert import RawDocument
from ariadna.ingest.discover import DiscoveredFile

_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
_MIN_YEAR = 1950
_MAX_YEAR = datetime.now().year + 1
_LANG_SAMPLE_CHARS = 5000  # первых N символов достаточно для оценки языка
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")
_LANG_RATIO_RU = 0.7  # доля кириллицы выше — документ русскоязычный
_LANG_RATIO_EN = 0.3  # доля кириллицы ниже — документ англоязычный


# ─── build_document_meta ──────────────────────────────────────────────
# Назначение: собирает DocumentMeta из имени файла, свойств документа и
#   нормализованного текста; is_core=True — все обрабатываемые файлы взяты
#   из трёх папок ядра (паспорт модуля, ~180 документов).
# Входные связи: DiscoveredFile, RawDocument, нормализованный текст документа
# Выходные данные: contracts.DocumentMeta
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def build_document_meta(item: DiscoveredFile, raw: RawDocument, text: str) -> DocumentMeta:
    title = _extract_title(raw.meta_title, item.path.stem)
    year = _extract_year(item.path.stem, raw.meta_year, text)
    authors = raw.meta_authors or []
    return DocumentMeta(
        doc_id=item.doc_id,
        path=item.rel_path,
        title=title,
        authors=authors,
        year=year,
        lang=detect_lang(text),
        geography=Geography.UNKNOWN,  # определяется позже (extraction), вне скоупа ingest
        source_folder=item.source_folder,
        is_core=True,
    )


# Назначение: приоритет — свойство документа (если не заглушка) → имя файла.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _extract_title(meta_title: str, filename_stem: str) -> str:
    if meta_title and meta_title.strip().lower() not in GENERIC_TITLES:
        return meta_title.strip()
    return filename_stem.replace("_", " ").strip()


# Назначение: приоритет — имя файла → свойства документа → первая страница текста.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _extract_year(filename_stem: str, meta_year: int | None, text: str) -> int | None:
    for source in (_year_from_filename(filename_stem), meta_year, _year_from_text(text)):
        if source is not None and _MIN_YEAR <= source <= _MAX_YEAR:
            return source
    return None


# Назначение: последнее 4-значное число года в имени файла (частый паттерн «Name 2011»).
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _year_from_filename(stem: str) -> int | None:
    matches = _YEAR_RE.findall(stem)
    return int(matches[-1]) if matches else None


# Назначение: первое 4-значное число года в первых 2000 символах текста.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _year_from_text(text: str) -> int | None:
    match = _YEAR_RE.search(text[:2000])
    return int(match.group(1)) if match else None


# ─── detect_lang ────────────────────────────────────────────────────────
# Назначение: определяет язык фрагмента текста по доле кириллических букв
#   среди кириллица+латиница; используется и для DocumentMeta.lang, и для
#   Chunk.lang в chunk.py (общая эвристика, без внешних NLP-зависимостей).
# Входные связи: строка текста (документ целиком или чанк)
# Выходные данные: contracts.Lang — RU/EN/MIXED
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def detect_lang(text: str) -> Lang:
    sample = text[:_LANG_SAMPLE_CHARS]
    n_cyr = len(_CYRILLIC_RE.findall(sample))
    n_lat = len(_LATIN_RE.findall(sample))
    total = n_cyr + n_lat
    if total == 0:
        return Lang.RU  # документ без букв (таблицы/формулы) — дефолт по контракту
    ratio_cyr = n_cyr / total
    if ratio_cyr >= _LANG_RATIO_RU:
        return Lang.RU
    if ratio_cyr <= _LANG_RATIO_EN:
        return Lang.EN
    return Lang.MIXED
