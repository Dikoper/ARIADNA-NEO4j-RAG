"""Смоук-тест на реальном результате прогона ingest: data/processed/*.jsonl.

Только ЧТЕНИЕ данных, полученных прогоном разработчика (module-dev, A-02,
worklogs/ingest.md#2026-07-03: 177 сконвертировано, 9580 чанков). Ничего не
пишет и не перезаписывает — файлы processed/ не трогаем.

Пропускается (skip), если каталог отсутствует — на другой машине без
предварительного прогона по корпусу тест не должен падать.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from ariadna.contracts import Chunk, DocumentMeta, DocumentText

PROCESSED_DIR = Path("data/processed")
META_PATH = PROCESSED_DIR / "meta.jsonl"
TEXTS_PATH = PROCESSED_DIR / "texts.jsonl"
CHUNKS_PATH = PROCESSED_DIR / "chunks.jsonl"

pytestmark = pytest.mark.skipif(
    not (META_PATH.exists() and TEXTS_PATH.exists() and CHUNKS_PATH.exists()),
    reason="data/processed/*.jsonl отсутствуют — нет результата реального прогона ingest",
)


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").strip().splitlines()


# Назначение: все строки meta.jsonl валидны по контракту DocumentMeta (полный
#   прогон — 177 документов, объём небольшой, проверяем целиком).
# Уровень: ✅ реализовано (A-02 tests)
def test_all_meta_rows_validate_against_contract():
    lines = _read_lines(META_PATH)
    assert len(lines) > 0
    metas = [DocumentMeta.model_validate_json(ln) for ln in lines]
    assert len(metas) == len(lines)
    assert all(m.is_core is True for m in metas)  # ingest обрабатывает только ядро


# Назначение: все строки texts.jsonl валидны по контракту DocumentText,
#   n_chars согласован с фактической длиной text.
# Уровень: ✅ реализовано (A-02 tests)
def test_all_text_rows_validate_against_contract():
    lines = _read_lines(TEXTS_PATH)
    texts = [DocumentText.model_validate_json(ln) for ln in lines]
    for t in texts:
        assert t.n_chars == len(t.text), f"doc_id={t.doc_id}: n_chars расходится с len(text)"


# Назначение: doc_id из meta.jsonl и texts.jsonl совпадают 1-в-1 (контракт:
#   DocumentText.doc_id = DocumentMeta.doc_id, инвариант связи файлов).
# Уровень: ✅ реализовано (A-02 tests)
def test_doc_id_consistent_between_meta_and_texts():
    meta_ids = {DocumentMeta.model_validate_json(ln).doc_id for ln in _read_lines(META_PATH)}
    text_ids = {DocumentText.model_validate_json(ln).doc_id for ln in _read_lines(TEXTS_PATH)}
    assert meta_ids == text_ids
    assert len(meta_ids) == len(_read_lines(META_PATH))  # doc_id уникальны, нет дублей


# Назначение: выборка строк chunks.jsonl (детерминированная случайная выборка,
#   без загрузки всего файла в pydantic — 9580 строк) валидна по Chunk;
#   doc_id каждого чанка ссылается на существующий документ.
# Уровень: ✅ реализовано (A-02 tests)
def test_sample_of_chunk_rows_validate_and_reference_known_doc():
    lines = _read_lines(CHUNKS_PATH)
    assert len(lines) > 0
    meta_ids = {DocumentMeta.model_validate_json(ln).doc_id for ln in _read_lines(META_PATH)}
    rng = random.Random(42)  # фиксированный seed — воспроизводимая выборка
    sample = rng.sample(lines, min(300, len(lines)))
    for ln in sample:
        chunk = Chunk.model_validate_json(ln)
        assert chunk.doc_id in meta_ids, f"chunk_id={chunk.chunk_id} ссылается на неизвестный doc_id"
        assert chunk.chunk_id == f"{chunk.doc_id}#{chunk.chunk_id.split('#')[-1]}"
        assert chunk.start < chunk.end
        assert chunk.text.strip() != ""


# Назначение: embedding is None у ВСЕХ чанков (это должен заполнить только
#   search/embeddings, ingest эмбеддинги не считает) — проверяем весь файл
#   без полной pydantic-валидации (быстрый JSON-парсинг одного поля).
# Уровень: ✅ реализовано (A-02 tests)
def test_all_chunks_have_no_embedding():
    lines = _read_lines(CHUNKS_PATH)
    offenders = [json.loads(ln)["chunk_id"] for ln in lines if json.loads(ln).get("embedding") is not None]
    assert offenders == [], f"чанки с непустым embedding (не должно быть после ingest): {offenders[:5]}"


# Назначение: chunk_id уникальны по всему файлу (нет дублей внутри/между
#   документами — единица цитирования).
# Уровень: ✅ реализовано (A-02 tests)
def test_all_chunk_ids_are_unique():
    lines = _read_lines(CHUNKS_PATH)
    ids = [json.loads(ln)["chunk_id"] for ln in lines]
    assert len(ids) == len(set(ids))


# Назначение: chunk.text реально совпадает с doc_text.text[start:end] для
#   выборки реальных документов — прямая проверка инварианта смещений на
#   боевых данных (не только на синтетике из test_chunk.py).
# Уровень: ✅ реализовано (A-02 tests)
def test_sample_chunk_offsets_match_source_text():
    text_lines = _read_lines(TEXTS_PATH)
    rng = random.Random(7)
    doc_sample = rng.sample(text_lines, min(5, len(text_lines)))
    docs_by_id = {}
    for ln in doc_sample:
        doc = DocumentText.model_validate_json(ln)
        docs_by_id[doc.doc_id] = doc.text

    checked = 0
    for ln in _read_lines(CHUNKS_PATH):
        raw = json.loads(ln)
        if raw["doc_id"] not in docs_by_id:
            continue
        chunk = Chunk.model_validate_json(ln)
        source_text = docs_by_id[chunk.doc_id]
        assert source_text[chunk.start:chunk.end] == chunk.text, (
            f"смещения не соответствуют тексту: chunk_id={chunk.chunk_id}"
        )
        checked += 1
    assert checked > 0, "выборка документов не пересеклась ни с одним чанком — расширить sample"
