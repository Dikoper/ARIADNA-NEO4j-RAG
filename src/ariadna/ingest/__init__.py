"""Модуль ingest: конвертация ядра корпуса в DocumentMeta/DocumentText + чанкинг.

Вход: файлы `data/Обзоры|Статьи|Доклады/*` (PDF/DOCX/DOCM/DOC/PPTX).
Выход: `data/processed/{meta,texts,chunks}.jsonl` — контракты DocumentMeta,
DocumentText, Chunk (`ariadna.contracts`). Точка входа: `ariadna.ingest.pipeline.main`.
Паспорт: docs/dev/modules/ingest.md.
"""
