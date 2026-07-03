"""Чанкинг нормализованного текста с сохранением границ предложений.

Вход: `contracts.DocumentText` (текст уже без колонтитулов — ingest/normalize).
Выход: `list[contracts.Chunk]` — без эмбеддингов (их считает search/embeddings,
задача A-04). Размер/перекрытие — именованные константы `config.py`
(CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS), паспорт модуля требует их явности.
"""
from __future__ import annotations

import re

from ariadna.contracts import Chunk, DocumentText
from ariadna.ingest.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS
from ariadna.ingest.metadata import detect_lang

# Конец предложения: один+ терминатор (.!?…), опционально закрывающая кавычка/
# скобка, за которым пробел/конец текста — эвристика без внешних NLP-зависимостей
# (не идеальна на сокращениях «т.к.», «др.», но для чанкинга это допустимо —
# худший случай — на одно предложение длиннее/короче ожидаемого чанка).
_SENT_END_RE = re.compile(r"[.!?…]+[”\"»)]?(?=\s|$)")


# ─── generate_chunks ──────────────────────────────────────────────────
# Назначение: режет нормализованный текст на чанки с перекрытием, сохраняя
#   границы предложений (колонтитулы уже удалены в ingest/normalize).
# Входные связи: contracts.DocumentText; config.CHUNK_SIZE_CHARS/CHUNK_OVERLAP_CHARS
# Выходные данные: list[contracts.Chunk] — id, doc_id, text, span, lang
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def generate_chunks(doc: DocumentText) -> list[Chunk]:
    text = doc.text
    if not text.strip():
        return []
    spans = _split_sentence_spans(text)
    chunk_spans = _pack_spans(spans, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)
    chunks: list[Chunk] = []
    for idx, (start, end) in enumerate(chunk_spans):
        raw_slice = text[start:end]
        stripped = raw_slice.strip()
        if not stripped:
            continue
        lead = len(raw_slice) - len(raw_slice.lstrip())
        adj_start = start + lead
        adj_end = adj_start + len(stripped)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#{idx}",
                doc_id=doc.doc_id,
                text=stripped,
                start=adj_start,
                end=adj_end,
                lang=detect_lang(stripped),
            )
        )
    return chunks


# Назначение: разбивает text на смежные (без разрывов) предложения-спаны [start, end).
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _split_sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENT_END_RE.finditer(text):
        end = match.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


# Назначение: группирует предложения в чанки ≤ size_limit символов с overlap_limit перекрытием.
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _pack_spans(
    spans: list[tuple[int, int]], size_limit: int, overlap_limit: int
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    n = len(spans)
    i = 0
    while i < n:
        chunk_start = spans[i][0]
        j = i
        chunk_end = spans[j][1]
        while j + 1 < n and (spans[j + 1][1] - chunk_start) <= size_limit:
            j += 1
            chunk_end = spans[j][1]
        result.append((chunk_start, chunk_end))
        if j + 1 >= n:
            break
        k = j
        while k > i and (chunk_end - spans[k][0]) < overlap_limit:
            k -= 1
        i = k if k > i else j + 1
    return result
