"""Тесты ingest/pipeline.py: end-to-end на фикстурном мини-корпусе.

run_pipeline() жёстко вызывает discover.discover_core_documents() без
параметра data_dir (config.DATA_DIR закодирован по умолчанию в
discover_core_documents), поэтому для прогона на фикстурах подменяем
ariadna.ingest.pipeline.discover_core_documents через monkeypatch —
run_pipeline не даёт внедрить произвольный data_dir напрямую.
"""
from __future__ import annotations

import json

from ariadna.contracts import Chunk, DocumentMeta, DocumentText
from ariadna.ingest import pipeline as pipeline_mod
from ariadna.ingest.discover import discover_core_documents


def _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch, run_id="test_ingest_run"):
    supported, unsupported = discover_core_documents(data_dir=core_fixture_dir)
    monkeypatch.setattr(
        pipeline_mod, "discover_core_documents", lambda: (supported, unsupported)
    )
    processed_dir = tmp_path / "processed"
    return pipeline_mod.run_pipeline(processed_dir=processed_dir, run_id=run_id)


# Назначение: полный прогон на фикстурном корпусе (docx/pptx/pdf валидные +
#   pdf без текста + pdf короче порога + неподдерживаемый .rar) даёт ожидаемые
#   счётчики converted/skipped и валидные JSONL по всем трём контрактам.
# Уровень: ✅ реализовано (A-02 tests)
def test_run_pipeline_end_to_end_on_fixture_corpus(core_fixture_dir, tmp_path, monkeypatch):
    stats = _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch)

    # 3 валидных документа (docx, pptx, pdf_special_chars) + pdf_with_boilerplate = 4 converted;
    # pdf_no_text_layer + pdf_too_short -> INGEST-002; archive.rar -> INGEST-001
    assert stats["converted"] == 4
    assert stats["skipped"] == 3
    assert stats["chunks"] > 0

    processed_dir = tmp_path / "processed"
    meta_lines = (processed_dir / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
    text_lines = (processed_dir / "texts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    chunk_lines = (processed_dir / "chunks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    skipped_lines = (processed_dir / "skipped.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert len(meta_lines) == 4
    assert len(text_lines) == 4
    assert len(skipped_lines) == 3

    metas = [DocumentMeta.model_validate_json(ln) for ln in meta_lines]
    texts = [DocumentText.model_validate_json(ln) for ln in text_lines]
    chunks = [Chunk.model_validate_json(ln) for ln in chunk_lines]

    meta_ids = {m.doc_id for m in metas}
    text_ids = {t.doc_id for t in texts}
    assert meta_ids == text_ids  # doc_id согласован между meta.jsonl и texts.jsonl
    assert all(c.doc_id in meta_ids for c in chunks)  # у всех чанков есть родитель
    assert all(c.embedding is None for c in chunks)


# Назначение: неподдерживаемый формат (.rar) уходит в skip с кодом INGEST-001,
#   без падения всего прогона (реестр ERRORS.md).
# Уровень: ✅ реализовано (A-02 tests)
def test_run_pipeline_unsupported_format_gets_ingest_001(core_fixture_dir, tmp_path, monkeypatch):
    _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch)
    skipped = [
        json.loads(ln)
        for ln in (tmp_path / "processed" / "skipped.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    rar_entries = [s for s in skipped if s["path"].endswith(".rar")]
    assert len(rar_entries) == 1
    assert rar_entries[0]["code"] == "INGEST-001"


# Назначение: PDF без текстового слоя и PDF короче MIN_TEXT_CHARS оба уходят
#   в skip с кодом INGEST-002 (без падения прогона).
# Уровень: ✅ реализовано (A-02 tests)
def test_run_pipeline_short_or_textless_pdf_gets_ingest_002(core_fixture_dir, tmp_path, monkeypatch):
    _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch)
    skipped = [
        json.loads(ln)
        for ln in (tmp_path / "processed" / "skipped.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    ingest_002 = [s for s in skipped if s["code"] == "INGEST-002"]
    assert len(ingest_002) == 2
    paths = {s["path"] for s in ingest_002}
    assert any("no_text_layer" in p for p in paths)
    assert any("too_short" in p for p in paths)


# Назначение: журнальные колонтитулы вычищены ДО чанкинга — ни один чанк
#   не содержит текст повторяющегося заголовка (паспорт модуля, требование
#   «чистить ДО чанкинга, иначе мусор в эмбеддингах»).
# Уровень: ✅ реализовано (A-02 tests)
def test_run_pipeline_boilerplate_removed_before_chunking(core_fixture_dir, tmp_path, monkeypatch):
    _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch)
    chunk_lines = (tmp_path / "processed" / "chunks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    chunks = [Chunk.model_validate_json(ln) for ln in chunk_lines]
    for c in chunks:
        assert "Журнал металлургических исследований" not in c.text


# Назначение: повторный прогон на той же фикстурной директории даёт те же
#   doc_id и не дублирует записи (идемпотентность — открытие файлов в режиме
#   "w" перезаписывает processed_dir целиком при каждом run_pipeline).
# Уровень: ✅ реализовано (A-02 tests)
def test_run_pipeline_idempotent_on_repeated_run(core_fixture_dir, tmp_path, monkeypatch):
    stats1 = _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch, run_id="run_a")
    processed_dir = tmp_path / "processed"
    meta1 = sorted(
        DocumentMeta.model_validate_json(ln).doc_id
        for ln in (processed_dir / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    chunks1 = sorted(
        Chunk.model_validate_json(ln).chunk_id
        for ln in (processed_dir / "chunks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )

    stats2 = _run_on_fixture(core_fixture_dir, tmp_path, monkeypatch, run_id="run_b")
    meta2 = sorted(
        DocumentMeta.model_validate_json(ln).doc_id
        for ln in (processed_dir / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    chunks2 = sorted(
        Chunk.model_validate_json(ln).chunk_id
        for ln in (processed_dir / "chunks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )

    assert stats1["converted"] == stats2["converted"]
    assert stats1["chunks"] == stats2["chunks"]
    assert meta1 == meta2  # те же doc_id, не задвоены
    assert chunks1 == chunks2  # те же chunk_id, не задвоены
    assert len(meta1) == len(set(meta1))  # нет дублей внутри одного прогона
    assert len(chunks1) == len(set(chunks1))
