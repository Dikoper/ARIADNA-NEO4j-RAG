"""Оркестрация ingest: discover → convert → normalize → chunk → JSONL.

Вход: `data/Обзоры|Статьи|Доклады/*`. Выход: `data/processed/meta.jsonl`
(DocumentMeta), `texts.jsonl` (DocumentText), `chunks.jsonl` (Chunk, без
эмбеддингов), `skipped.jsonl` (диагностика: путь/код/причина пропуска —
вспомогательный артефакт, не часть контрактов). Логи — JSON Lines через
`ariadna.logutil` в `logs/pipeline/<run_id>.jsonl`.

Точка входа: `python -m ariadna.ingest.pipeline`.
"""
from __future__ import annotations

import json
from pathlib import Path

from ariadna.contracts import DocumentText
from ariadna.ingest.chunk import generate_chunks
from ariadna.ingest.config import (
    LEGACY_DOC_EXTS,
    OOXML_WORD_EXTS,
    PDF_EXTS,
    PPTX_EXTS,
    PROCESSED_DIR,
)
from ariadna.ingest.convert import (
    ConversionError,
    convert_legacy_doc,
    convert_ooxml_word,
    convert_pdf,
    convert_pptx,
)
from ariadna.ingest.discover import DiscoveredFile, discover_core_documents
from ariadna.ingest.metadata import build_document_meta
from ariadna.ingest.normalize import is_too_short, normalize_document
from ariadna.logutil import get_logger, log_event, new_run_id

_CONVERTERS = {
    **{ext: convert_pdf for ext in PDF_EXTS},
    **{ext: convert_ooxml_word for ext in OOXML_WORD_EXTS},
    **{ext: convert_legacy_doc for ext in LEGACY_DOC_EXTS},
    **{ext: convert_pptx for ext in PPTX_EXTS},
}

_ERR_TRUNCATE = 500  # усечение сообщения об ошибке в лог (CONVENTIONS.md §4)


# ─── run_pipeline ────────────────────────────────────────────────────────
# Назначение: полный прогон ingest по ядру корпуса; пишет meta/texts/chunks/
#   skipped jsonl в processed_dir, ведёт лог прогона run_id.
# Входные связи: discover.discover_core_documents; convert/normalize/metadata/chunk
# Выходные данные: dict со сводными цифрами прогона (для отчёта агента)
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def run_pipeline(processed_dir: Path = PROCESSED_DIR, run_id: str | None = None) -> dict:
    run_id = run_id or new_run_id("ingest_")
    logger = get_logger("ingest", run_id)
    processed_dir.mkdir(parents=True, exist_ok=True)

    supported, unsupported = discover_core_documents()
    stats = {"run_id": run_id, "total_found": len(supported) + len(unsupported),
              "converted": 0, "skipped": 0, "chunks": 0}

    with (
        open(processed_dir / "meta.jsonl", "w", encoding="utf-8") as meta_f,
        open(processed_dir / "texts.jsonl", "w", encoding="utf-8") as texts_f,
        open(processed_dir / "chunks.jsonl", "w", encoding="utf-8") as chunks_f,
        open(processed_dir / "skipped.jsonl", "w", encoding="utf-8") as skipped_f,
    ):
        for item in unsupported:
            _write_skip(skipped_f, logger, run_id, item, "INGEST-001", "неподдерживаемый формат файла")
            stats["skipped"] += 1

        for item in supported:
            n_chunks = _process_one(item, logger, run_id, meta_f, texts_f, chunks_f, skipped_f)
            if n_chunks is None:
                stats["skipped"] += 1
            else:
                stats["converted"] += 1
                stats["chunks"] += n_chunks

    log_event(
        logger, stage="pipeline", event="run_complete",
        detail=json.dumps({k: v for k, v in stats.items() if k != "run_id"}, ensure_ascii=False),
    )
    return stats


# Назначение: конвертирует один файл и пишет его meta/text/chunks; None — файл пропущен.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _process_one(item: DiscoveredFile, logger, run_id: str, meta_f, texts_f, chunks_f, skipped_f) -> int | None:
    converter = _CONVERTERS.get(item.ext)
    if converter is None:  # не должно случаться (discover фильтрует по SUPPORTED_EXTS)
        _write_skip(skipped_f, logger, run_id, item, "INGEST-001", "нет обработчика формата")
        return None
    try:
        raw = converter(item.path)
    except ConversionError as exc:
        _write_skip(skipped_f, logger, run_id, item, "INGEST-001", str(exc)[:_ERR_TRUNCATE])
        return None
    except Exception as exc:  # непредвиденный сбой конвертации — не роняем весь прогон
        _write_skip(skipped_f, logger, run_id, item, "INGEST-001", f"неожиданная ошибка: {exc}"[:_ERR_TRUNCATE])
        return None

    text = normalize_document(raw)
    if is_too_short(text):
        _write_skip(
            skipped_f, logger, run_id, item, "INGEST-002",
            f"текста {len(text)} символов < порога (нет текстового слоя/колонтитулы съели текст)",
        )
        return None

    meta = build_document_meta(item, raw, text)
    doc_text = DocumentText(doc_id=item.doc_id, text=text, n_chars=len(text))
    chunks = generate_chunks(doc_text)

    meta_f.write(meta.model_dump_json() + "\n")
    texts_f.write(doc_text.model_dump_json() + "\n")
    for chunk in chunks:
        chunks_f.write(chunk.model_dump_json() + "\n")

    log_event(
        logger, stage="pipeline", event="converted", doc_id=item.doc_id,
        detail=f"path={item.rel_path} n_chars={len(text)} n_chunks={len(chunks)}",
    )
    return len(chunks)


# Назначение: пишет строку в skipped.jsonl и лог-событие с кодом ошибки (INGEST-00x).
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _write_skip(skipped_f, logger, run_id: str, item: DiscoveredFile, code: str, reason: str) -> None:
    skipped_f.write(json.dumps(
        {"doc_id": item.doc_id, "path": item.rel_path, "code": code, "reason": reason},
        ensure_ascii=False,
    ) + "\n")
    log_event(
        logger, stage="pipeline", event=code, level="WARNING", doc_id=item.doc_id,
        detail=f"path={item.rel_path} reason={reason}",
    )


# ─── main ────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа полного прогона ingest по ядру корпуса; печатает
#   сводку в stdout (для верификации задачи A-02).
# Входные связи: аргументов командной строки нет — конфигурация через config.py
# Выходные данные: нет (побочный эффект — файлы processed/*.jsonl + печать сводки)
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def main() -> None:
    stats = run_pipeline()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
