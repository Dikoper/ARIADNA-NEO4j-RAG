"""Тесты A-05: `ariadna.graph.lexical_loader` — независимая проверка контракта
(не переписывает реализацию). Живой Neo4j (ariadna_neo4j, .env в корне);
все узлы — с префиксом test_a05_ (см. conftest.py), очистка до/после теста.

Проверяется: идемпотентность двойной загрузки, соответствие свойств узлов
полям contracts.DocumentMeta/Chunk, поведение чанка без эмбеддинга (GRAPH-002),
поведение чанка с doc_id, отсутствующим в meta.jsonl (orphan), пустой файл
чанков, спецсимволы/кириллица, detect_embedding_dimension на смешанных данных,
--limit, создание vector index на синтетическом имени (не трогает боевой
chunk_embedding_idx).
"""
from __future__ import annotations

import json
from pathlib import Path

from ariadna.graph import lexical_loader as ll
from ariadna.logutil import LOG_DIR, get_logger, new_run_id

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_PREFIX = "test_a05_"

META_PATH = FIXTURES_DIR / "meta.jsonl"
CHUNKS_PATH = FIXTURES_DIR / "chunks.jsonl"


# ─── _counts ───────────────────────────────────────────────────────────
# Назначение: считает тестовые Document/Chunk/HAS_CHUNK (только с префиксом
#   test_a05_) — не зависит от глобальных счётчиков параллельной боевой загрузки.
# Уровень: ✅ реализовано (module-tester A-05)
def _counts(driver) -> tuple[int, int, int]:
    with driver.session() as session:
        n_docs = session.run(
            "MATCH (d:Document) WHERE d.doc_id STARTS WITH $p RETURN count(d) AS c", p=TEST_PREFIX
        ).single()["c"]
        n_chunks = session.run(
            "MATCH (c:Chunk) WHERE c.chunk_id STARTS WITH $p RETURN count(c) AS c", p=TEST_PREFIX
        ).single()["c"]
        n_rel = session.run(
            "MATCH (d:Document)-[r:HAS_CHUNK]->(c:Chunk) "
            "WHERE d.doc_id STARTS WITH $p RETURN count(r) AS c",
            p=TEST_PREFIX,
        ).single()["c"]
    return n_docs, n_chunks, n_rel


# ─── test_double_load_is_idempotent ─────────────────────────────────────
# Назначение: двойная загрузка одной фикстуры не меняет счётчики тестовых
#   узлов/связей (MERGE, не CREATE) — приоритет №1 задания.
# Уровень: ✅ реализовано (module-tester A-05)
def test_double_load_is_idempotent(driver):
    ll.ensure_constraints(driver)
    ll.load_documents(driver, META_PATH)
    ll.load_chunks(driver, CHUNKS_PATH)
    first = _counts(driver)

    ll.load_documents(driver, META_PATH)
    ll.load_chunks(driver, CHUNKS_PATH)
    second = _counts(driver)

    assert first == second
    n_docs, n_chunks, n_rel = second
    # 2 документа в meta.jsonl, 4 чанка в chunks.jsonl (1 orphan без Document).
    assert n_docs == 2
    assert n_chunks == 4
    # HAS_CHUNK: doc1 (2 чанка) + doc2 (1 чанк) = 3; orphan-чанк без связи.
    assert n_rel == 3


# ─── test_document_and_chunk_properties_match_contract ─────────────────
# Назначение: узлы Document/Chunk после загрузки фикстуры несут те же значения
#   полей, что и contracts.DocumentMeta/Chunk на входе (без потерь/искажений).
# Уровень: ✅ реализовано (module-tester A-05)
def test_document_and_chunk_properties_match_contract(driver):
    ll.ensure_constraints(driver)
    ll.load_documents(driver, META_PATH)
    ll.load_chunks(driver, CHUNKS_PATH)

    with driver.session() as session:
        doc = session.run(
            "MATCH (d:Document {doc_id: 'test_a05_doc1'}) RETURN d"
        ).single()["d"]
        chunk = session.run(
            "MATCH (c:Chunk {chunk_id: 'test_a05_doc1#0'}) RETURN c"
        ).single()["c"]

    assert doc["title"] == "Тестовый документ №1 «Экстракция меди»"
    assert doc["year"] == 2021
    assert doc["lang"] == "ru"
    assert doc["geography"] == "ru"
    assert doc["source_folder"] == "Статьи"
    assert doc["path"] == "test/a05/doc1.txt"
    assert doc["is_core"] is True

    assert chunk["doc_id"] == "test_a05_doc1"
    assert chunk["start"] == 0
    assert chunk["end"] == 110
    assert chunk["lang"] == "ru"
    assert list(chunk["embedding"]) == [0.11, 0.22, 0.33, 0.44]


# ─── test_chunk_without_embedding_warns_and_has_no_vector ───────────────
# Назначение: чанк с embedding=null грузится (не падает), узел без вектора,
#   в лог пишется WARN GRAPH-002 с указанием chunk_id — приоритет №3.
# Уровень: ✅ реализовано (module-tester A-05)
def test_chunk_without_embedding_warns_and_has_no_vector(driver):
    run_id = new_run_id("test_a05_graph002_")
    logger = get_logger("graph", run_id)
    log_path = LOG_DIR / f"{run_id}.jsonl"
    try:
        n_total, n_missing = ll.load_chunks(driver, CHUNKS_PATH, logger=logger)
        assert n_total == 4
        assert n_missing == 2  # doc1#1 и orphan#0 — оба без embedding

        with driver.session() as session:
            rec = session.run(
                "MATCH (c:Chunk {chunk_id: 'test_a05_doc1#1'}) RETURN c.embedding AS e"
            ).single()
        assert rec["e"] is None

        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        graph_002 = [e for e in events if e["event"] == "GRAPH-002"]
        assert len(graph_002) == 2
        assert any("test_a05_doc1#1" in e["detail"] for e in graph_002)
        assert any("test_a05_orphan#0" in e["detail"] for e in graph_002)
        assert all(e["level"] == "WARNING" for e in graph_002)
    finally:
        log_path.unlink(missing_ok=True)


# ─── test_orphan_chunk_loaded_without_has_chunk_relation ────────────────
# Назначение: чанк, чей doc_id отсутствует в meta.jsonl (Document не загружен),
#   не теряется — узел Chunk создаётся, но связь HAS_CHUNK не появляется и
#   Document для несуществующего doc_id не создаётся неявно (OPTIONAL MATCH +
#   FOREACH, см. пре-комментарий _write_chunk_batches). Приоритет №3.
# Уровень: ✅ реализовано (module-tester A-05)
def test_orphan_chunk_loaded_without_has_chunk_relation(driver):
    # Намеренно НЕ грузим meta.jsonl — test_a05_missing_doc никогда не появится.
    ll.load_chunks(driver, CHUNKS_PATH)

    with driver.session() as session:
        chunk = session.run(
            "MATCH (c:Chunk {chunk_id: 'test_a05_orphan#0'}) RETURN c"
        ).single()["c"]
        n_rel = session.run(
            "MATCH (c:Chunk {chunk_id: 'test_a05_orphan#0'})<-[:HAS_CHUNK]-() "
            "RETURN count(*) AS c"
        ).single()["c"]
        n_doc = session.run(
            "MATCH (d:Document {doc_id: 'test_a05_missing_doc'}) RETURN count(d) AS c"
        ).single()["c"]

    assert chunk["doc_id"] == "test_a05_missing_doc"
    assert n_rel == 0
    assert n_doc == 0  # OPTIONAL MATCH не создаёт Document — только сопоставляет существующий


# ─── test_empty_chunks_file_loads_nothing ───────────────────────────────
# Назначение: пустой файл чанков — 0 обработанных строк, без исключений.
#   Приоритет №3 (граничный случай).
# Уровень: ✅ реализовано (module-tester A-05)
def test_empty_chunks_file_loads_nothing(driver):
    n_total, n_missing = ll.load_chunks(driver, FIXTURES_DIR / "chunks_empty.jsonl")
    assert (n_total, n_missing) == (0, 0)


# ─── test_special_characters_and_cyrillic_preserved ─────────────────────
# Назначение: кириллица, кавычки-ёлочки, ≤/°/– и т.п. сохраняются в тексте
#   узла ровно как во входном JSONL (проверка ingest->graph без искажений).
# Уровень: ✅ реализовано (module-tester A-05)
def test_special_characters_and_cyrillic_preserved(driver):
    ll.load_chunks(driver, CHUNKS_PATH)
    with driver.session() as session:
        text = session.run(
            "MATCH (c:Chunk {chunk_id: 'test_a05_doc1#0'}) RETURN c.text AS t"
        ).single()["t"]
    expected = (
        "Экстракция меди при температуре 60°C, содержание SO4^2- ≤ 300 мг/л, "
        "«эффективность» — 95% (диапазон 200–300 мг/дм³)."
    )
    assert text == expected


# ─── test_detect_embedding_dimension_skips_leading_null ─────────────────
# Назначение: размерность берётся из ПЕРВОГО чанка С вектором, даже если
#   более ранние строки файла — без embedding (не просто "первая строка").
#   Приоритет №4.
# Уровень: ✅ реализовано (module-tester A-05)
def test_detect_embedding_dimension_skips_leading_null():
    dim = ll.detect_embedding_dimension(FIXTURES_DIR / "chunks_dim.jsonl")
    assert dim == 2


# ─── test_detect_embedding_dimension_none_when_no_vectors ───────────────
# Назначение: если ни один чанк в файле не имеет embedding — функция
#   возвращает None (сигнал "индекс не создавать"), а не падает.
# Уровень: ✅ реализовано (module-tester A-05)
def test_detect_embedding_dimension_none_when_no_vectors():
    dim = ll.detect_embedding_dimension(FIXTURES_DIR / "chunks_no_embedding.jsonl")
    assert dim is None


# ─── test_limit_restricts_documents_and_chunks ──────────────────────────
# Назначение: --limit (через load_documents/load_chunks(limit=N)) реально
#   ограничивает число обработанных строк, а не только счётчик в отчёте.
#   Приоритет №5.
# Уровень: ✅ реализовано (module-tester A-05)
def test_limit_restricts_documents_and_chunks(driver):
    meta_path = FIXTURES_DIR / "meta_limit.jsonl"
    chunks_path = FIXTURES_DIR / "chunks_limit.jsonl"

    n_docs = ll.load_documents(driver, meta_path, limit=2)
    n_chunks, n_missing = ll.load_chunks(driver, chunks_path, limit=2)

    assert n_docs == 2
    assert (n_chunks, n_missing) == (2, 0)

    with driver.session() as session:
        n_docs_db = session.run(
            "MATCH (d:Document) WHERE d.doc_id STARTS WITH 'test_a05_limit_doc' "
            "RETURN count(d) AS c"
        ).single()["c"]
        n_chunks_db = session.run(
            "MATCH (c:Chunk) WHERE c.chunk_id STARTS WITH 'test_a05_limit#' "
            "RETURN count(c) AS c"
        ).single()["c"]
        third_doc = session.run(
            "MATCH (d:Document {doc_id: 'test_a05_limit_doc3'}) RETURN d"
        ).single()
        third_chunk = session.run(
            "MATCH (c:Chunk {chunk_id: 'test_a05_limit#2'}) RETURN c"
        ).single()

    assert n_docs_db == 2
    assert n_chunks_db == 2
    assert third_doc is None  # третья строка файла (limit=2) не должна попасть в базу
    assert third_chunk is None


# ─── test_ensure_vector_index_is_safe_noop_against_existing_index ───────
# Назначение: ⚠ БАГ/ограничение, найденное при тестировании (см. отчёт module-
#   tester): Neo4j допускает не более ОДНОГО vector index на пару (label,
#   property) — "CREATE VECTOR INDEX <любое_другое_имя> ... FOR (c:Chunk)
#   ON (c.embedding) IF NOT EXISTS" молча схлопывается в no-op, если
#   VECTOR-индекс на (:Chunk).embedding уже существует под ЛЮБЫМ именем
#   (проверено вручную: Neo4j возвращает notification
#   Neo.ClientNotification.Schema.IndexOrConstraintAlreadyExists и не создаёт
#   новый индекс — ни имя, ни переданная размерность роли не играют).
#   Из-за этого инструкция «создать синтетический vector index для проверки,
#   не трогая боевой chunk_embedding_idx» технически невыполнима на этой БД:
#   пока боевой индекс существует на (:Chunk).embedding, ensure_vector_index()
#   с любым другим именем/размерностью — гарантированный no-op. Ниже —
#   тест именно этого фактического поведения (безопасность вызова), а не
#   создания независимого индекса.
# Уровень: ✅ реализовано (module-tester A-05)
def test_ensure_vector_index_is_safe_noop_against_existing_index(driver):
    with driver.session() as session:
        before = session.run(
            "SHOW INDEXES YIELD name, options WHERE name = $name RETURN options",
            name=ll.CHUNK_VECTOR_INDEX_NAME,
        ).single()
    assert before is not None, "боевой vector index должен уже существовать в этой БД для этого теста"
    before_dims = before["options"]["indexConfig"]["vector.dimensions"]

    # Вызов с заведомо другой размерностью не должен падать и не должен
    # изменить конфигурацию существующего боевого индекса.
    ll.ensure_vector_index(driver, 4)

    with driver.session() as session:
        after = session.run(
            "SHOW INDEXES YIELD name, options WHERE name = $name RETURN options",
            name=ll.CHUNK_VECTOR_INDEX_NAME,
        ).single()
    assert after["options"]["indexConfig"]["vector.dimensions"] == before_dims
