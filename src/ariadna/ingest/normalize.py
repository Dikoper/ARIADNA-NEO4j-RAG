"""Нормализация сырого текста: чистка колонтитулов PDF + пробелов, контроль объёма.

Вход: `convert.RawDocument`. Выход: нормализованный текст для
`contracts.DocumentText` (без колонтитулов и мусора) + признак «текста мало»
(источник кода INGEST-002, реестр `docs/dev/ERRORS.md`).

Колонтитулы обнаруживаются только у PDF (постраничная разбивка есть только
там — DOCX/PPTX хранят реальный header/footer в отдельных XML-частях,
которые ingest не читает, поэтому в извлечённом тексте их нет).
"""
from __future__ import annotations

import re

from ariadna.ingest.config import MIN_TEXT_CHARS
from ariadna.ingest.convert import RawDocument

_WS_RE = re.compile(r"[ \t ]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_PAGE_NUMBER_RE = re.compile(r"^[|\s]*\d{1,4}[.\s]*$")
_DIGITS_RE = re.compile(r"\d+")

# Строка-кандидат в колонтитул должна повторяться минимум в такой доле страниц
# (и минимум на MIN_PAGES_FOR_BOILERPLATE страницах), чтобы её вырезали.
BOILERPLATE_MIN_SHARE = 0.4
MIN_PAGES_FOR_BOILERPLATE = 3


# ─── strip_pdf_boilerplate ────────────────────────────────────────────────
# Назначение: убирает повторяющиеся из страницы в страницу колонтитулы
#   (бегущий заголовок/футер) и чистые номера страниц у PDF, извлечённого
#   постранично; сравнение — по первой/последней строке страницы.
# Входные связи: list[str] — текст страниц из convert.convert_pdf
# Выходные данные: str — текст документа с вырезанными колонтитулами
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def strip_pdf_boilerplate(pages: list[str]) -> str:
    page_lines = [[ln for ln in page.splitlines() if ln.strip()] for page in pages]
    n_pages = len(page_lines)
    boilerplate = _find_boilerplate_lines(page_lines, n_pages)

    cleaned_pages: list[str] = []
    for lines in page_lines:
        kept = list(lines)
        if kept and (_normalize_candidate(kept[0]) in boilerplate or _PAGE_NUMBER_RE.match(kept[0].strip())):
            kept = kept[1:]
        if kept and (_normalize_candidate(kept[-1]) in boilerplate or _PAGE_NUMBER_RE.match(kept[-1].strip())):
            kept = kept[:-1]
        cleaned_pages.append("\n".join(kept))
    return "\n".join(cleaned_pages)


# Назначение: первая/последняя строка каждой страницы — кандидат в колонтитул;
#   повторяющиеся чаще порога — вырезаем.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _find_boilerplate_lines(page_lines: list[list[str]], n_pages: int) -> set[str]:
    if n_pages < MIN_PAGES_FOR_BOILERPLATE:
        return set()
    counts: dict[str, int] = {}
    for lines in page_lines:
        candidates = {_normalize_candidate(lines[0])} if lines else set()
        if lines:
            candidates.add(_normalize_candidate(lines[-1]))
        for cand in candidates:
            if cand:
                counts[cand] = counts.get(cand, 0) + 1
    threshold = max(MIN_PAGES_FOR_BOILERPLATE, int(n_pages * BOILERPLATE_MIN_SHARE))
    return {cand for cand, n in counts.items() if n >= threshold}


# Назначение: строка → форма для сравнения между страницами (пробелы схлопнуты, цифры стёрты).
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _normalize_candidate(line: str) -> str:
    return _WS_RE.sub(" ", _DIGITS_RE.sub("#", line.strip().lower()))


# ─── clean_whitespace ──────────────────────────────────────────────────
# Назначение: схлопывает повторные пробелы/пустые строки, убирает висячие
#   пробелы по краям строк — общий шаг для всех форматов после конвертации.
# Входные связи: str — текст документа (уже без колонтитулов, если PDF)
# Выходные данные: str — нормализованный текст
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def clean_whitespace(text: str) -> str:
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    joined = "\n".join(lines)
    return _BLANK_LINES_RE.sub("\n\n", joined).strip()


# ─── normalize_document ──────────────────────────────────────────────────
# Назначение: полный шаг нормализации сырого документа: чистка колонтитулов
#   (только PDF) + пробелов; единая точка входа для pipeline.py.
# Входные связи: convert.RawDocument
# Выходные данные: str — итоговый текст для contracts.DocumentText.text
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def normalize_document(raw: RawDocument) -> str:
    text = strip_pdf_boilerplate(raw.pages) if raw.pages is not None else raw.text
    return clean_whitespace(text)


# ─── is_too_short ────────────────────────────────────────────────────────
# Назначение: проверяет, достаточно ли текста получилось (порог INGEST-002 —
#   PDF без текстового слоя/колонтитулы «съели» весь текст).
# Входные связи: нормализованный текст, config.MIN_TEXT_CHARS
# Выходные данные: bool — True, если документ нужно отправить в skip-лист
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def is_too_short(text: str) -> bool:
    return len(text) < MIN_TEXT_CHARS
