"""Именованные константы конвейера ingest.

Вход: нет (статическая конфигурация). Выход: константы, используемые
discover/convert/normalize/chunk/pipeline. Зависимости: только stdlib.
Паспорт: docs/dev/modules/ingest.md.
"""
from __future__ import annotations

from pathlib import Path

# ─── Расположение корпуса и ядра ──────────────────────────────────────
DATA_DIR = Path("data")
PROCESSED_DIR = DATA_DIR / "processed"

# Ядро для графового извлечения (~180 документов) — задание CLAUDE.md/TASK.md.
# Папки «Журналы» и «Материалы конференций» намеренно исключены (не ядро).
CORE_FOLDERS = ("Обзоры", "Статьи", "Доклады")

# ─── Форматы ───────────────────────────────────────────────────────────
PDF_EXTS = {".pdf"}
OOXML_WORD_EXTS = {".docx", ".docm"}  # общий разбор через zip+XML (см. convert.py)
LEGACY_DOC_EXTS = {".doc"}            # конвертация через libreoffice --headless
PPTX_EXTS = {".pptx"}
SUPPORTED_EXTS = PDF_EXTS | OOXML_WORD_EXTS | LEGACY_DOC_EXTS | PPTX_EXTS

# ─── Чанкинг (границы предложений, размер/перекрытие — символы) ───────
CHUNK_SIZE_CHARS = 1500   # ~250–300 слов — единица индексации/цитирования
CHUNK_OVERLAP_CHARS = 200  # перекрытие для сохранения контекста на стыке чанков

# ─── Пороговые значения качества конвертации ───────────────────────────
MIN_TEXT_CHARS = 200  # меньше — INGEST-002 (нет текстового слоя/колонтитулы съели текст)

# ─── Заголовки-заглушки офисных приложений — не считать реальным title ─
GENERIC_TITLES = {
    "", "document", "документ microsoft word", "презентация powerpoint",
    "powerpoint presentation", "microsoft word - document1",
}

# ─── libreoffice headless-конвертация .doc → .docx ────────────────────
SOFFICE_TIMEOUT_SEC = 120
