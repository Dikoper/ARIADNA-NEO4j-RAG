"""Тесты ingest/chunk.py: границы предложений, размер/перекрытие, спецсимволы, id."""
from __future__ import annotations

from ariadna.contracts import DocumentText
from ariadna.ingest.chunk import generate_chunks
from ariadna.ingest.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS


# Назначение: пустой документ -> пустой список чанков, без падения.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_empty_document():
    doc = DocumentText(doc_id="d1", text="", n_chars=0)
    assert generate_chunks(doc) == []


# Назначение: документ из одних пробелов -> пустой список (текст пуст после strip).
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_whitespace_only_document():
    doc = DocumentText(doc_id="d1", text="   \n\n\t  ", n_chars=8)
    assert generate_chunks(doc) == []


# Назначение: документ короче CHUNK_SIZE_CHARS -> ровно один чанк, весь текст.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_short_document_single_chunk():
    text = "Первое предложение. Второе предложение. Третье предложение."
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_id == "d1#0"
    assert chunks[0].doc_id == "d1"


# Назначение: chunk_id имеет формат <doc_id>#<порядковый номер> без пропусков/дублей.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_chunk_id_format_sequential():
    sentence = "Слово раз два три четыре пять шесть семь. "
    text = sentence * 200  # достаточно длинно для нескольких чанков
    doc = DocumentText(doc_id="doc42", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    assert len(chunks) > 1
    for idx, chunk in enumerate(chunks):
        assert chunk.chunk_id == f"doc42#{idx}"
        assert chunk.doc_id == "doc42"


# Назначение: ни один чанк не обрывается посреди слова — последний символ
#   чанка либо конец текста, либо граница предложения/пробел в исходном тексте.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_does_not_break_mid_word():
    sentence = "Электроэкстракция меди из растворов сульфата является процессом. "
    text = sentence * 100
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        # чанк должен заканчиваться концом предложения (после .!?… с опц. кавычкой/скобкой)
        assert chunk.text[-1] in ".!?…\")»”", f"чанк обрывается не на границе предложения: {chunk.text[-40:]!r}"
    # ни один чанк не должен начинаться/заканчиваться посреди слова:
    # первый символ — заглавная буква начала предложения или граница текста
    for chunk in chunks:
        assert not chunk.text.startswith(" ")
        assert not chunk.text.endswith(" ")


# Назначение: реконструкция через start/end на исходном тексте документа
#   даёт byte-в-byte тот же текст, что в chunk.text (инвариант оффсетов).
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_start_end_match_source_text():
    sentence = "Испытание образца проводилось при температуре 80°C и давлении ≤ 5 атм. "
    text = sentence * 50
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    for chunk in chunks:
        assert text[chunk.start:chunk.end] == chunk.text
        assert 0 <= chunk.start < chunk.end <= len(text)


# Назначение: чанки на длинном тексте примерно укладываются в CHUNK_SIZE_CHARS
#   (соседние предложения короткие — превышение возможно только на длину
#   одного предложения сверх лимита).
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_respects_size_constant_approximately():
    sentence = "Слово раз два три четыре пять шесть семь восемь девять десять. "
    text = sentence * 300
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    max_sentence_len = len(sentence) + 1
    for chunk in chunks[:-1]:  # последний чанк может быть короче — это ожидаемо
        assert len(chunk.text) <= CHUNK_SIZE_CHARS + max_sentence_len


# Назначение: соседние чанки перекрываются текстом (не начинаются сразу
#   после конца предыдущего) — перекрытие в районе CHUNK_OVERLAP_CHARS.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_overlap_between_consecutive_chunks():
    sentence = "Металлургический процесс переработки руды описан подробно тут. "
    text = sentence * 300
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    assert len(chunks) >= 3
    for prev, cur in zip(chunks, chunks[1:]):
        overlap = prev.end - cur.start
        assert overlap > 0, "соседние чанки не перекрываются вовсе"
        # перекрытие не должно превышать сам предыдущий чанк
        assert overlap <= (prev.end - prev.start)


# Назначение: спецсимволы (индексы, ≤, °C, en-dash, дробные единицы, RU/EN)
#   не теряются и не искажаются при разбиении на чанки.
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_preserves_special_chars(special_chars_sentence):
    text = (special_chars_sentence + " ") * 10
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    joined = " ".join(c.text for c in chunks)
    for token in ("SO₂", "CO₂", "≤ 200 мг/л", "80°C", "200–300 мг/дм³", "electrowinning", "электроэкстракция"):
        assert token in joined, f"токен {token!r} потерян/искажён при чанкинге"


# Назначение: у каждого чанка сразу после ingest embedding is None
#   (эмбеддинги считает отдельный модуль search/embeddings — A-04).
# Уровень: ✅ реализовано (A-02 tests)
def test_generate_chunks_embedding_is_none():
    text = "Предложение один. Предложение два. Предложение три."
    doc = DocumentText(doc_id="d1", text=text, n_chars=len(text))
    chunks = generate_chunks(doc)
    assert all(c.embedding is None for c in chunks)
