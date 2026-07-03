"""Загрузка лексического графа Document->Chunk в Neo4j + vector index по чанкам.

Вход: `data/processed/meta.jsonl` (contracts.DocumentMeta), `data/processed/
chunks_embedded.jsonl` (contracts.Chunk с заполненным embedding; до готовности
A-04 структура идентична chunks.jsonl, но embedding=None — такой чанк грузится
без вектора + WARN GRAPH-002, не падает). Выход: узлы Document/Chunk, связь
(:Document)-[:HAS_CHUNK]->(:Chunk), vector index CHUNK_VECTOR_INDEX_NAME
(cosine, размерность — из первого чанка с вектором, не хардкод) в Neo4j.
Зависимости: neo4j (bolt-драйвер), ariadna.contracts, ariadna.logutil.
Инвариант: единственный модуль с правом записи в Neo4j (docs/dev/modules/graph.md).
Точка входа: `python -m ariadna.graph.lexical_loader [--limit N]`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from neo4j import Driver, GraphDatabase

from ariadna.contracts import Chunk, DocumentMeta
from ariadna.graph.config import (
    CHUNK_VECTOR_INDEX_NAME,
    DEFAULT_CHUNKS_PATH,
    DEFAULT_META_PATH,
    ENV_FILE,
    LOAD_BATCH_SIZE,
    NEO4J_PASSWORD_VAR,
    NEO4J_URI_VAR,
    NEO4J_USER_VAR,
    VECTOR_SIMILARITY_FUNCTION,
)
from ariadna.logutil import get_logger, log_event, new_run_id

# Чанк загружен без вектора (A-04 ещё не готов/сбой эмбеддинга) — ERRORS.md.
GRAPH_MISSING_EMBEDDING = "GRAPH-002"


# Назначение: читает KEY=VALUE из .env (без внешних зависимостей, комментарии/пустые строки пропускаются).
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# ─── get_driver ──────────────────────────────────────────────────────────
# Назначение: создаёт neo4j-драйвер по параметрам подключения — сначала
#   переменные окружения процесса, затем .env в корне проекта (CLAUDE.md).
# Входные связи: NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD (os.environ или .env)
# Выходные данные: neo4j.Driver, готовый к driver.session()
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def get_driver() -> Driver:
    env = _read_env_file(ENV_FILE)
    uri = os.environ.get(NEO4J_URI_VAR) or env.get(NEO4J_URI_VAR, "bolt://localhost:7687")
    user = os.environ.get(NEO4J_USER_VAR) or env.get(NEO4J_USER_VAR, "neo4j")
    password = os.environ.get(NEO4J_PASSWORD_VAR) or env.get(NEO4J_PASSWORD_VAR)
    if not password:
        raise RuntimeError(f"{NEO4J_PASSWORD_VAR} не задан ни в окружении, ни в {ENV_FILE}")
    return GraphDatabase.driver(uri, auth=(user, password))


# ─── ensure_constraints ──────────────────────────────────────────────────
# Назначение: создаёт констрейнты уникальности Document.doc_id и Chunk.chunk_id
#   до загрузки данных — идемпотентность последующих MERGE опирается на них.
# Входные связи: neo4j.Driver
# Выходные данные: нет (побочный эффект — DDL в Neo4j)
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def ensure_constraints(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT document_doc_id_unique IF NOT EXISTS "
            "FOR (d:Document) REQUIRE d.doc_id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT chunk_chunk_id_unique IF NOT EXISTS "
            "FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE"
        )


# ─── load_documents ──────────────────────────────────────────────────────
# Назначение: грузит узлы Document из meta.jsonl батчами UNWIND+MERGE (идемпотентно).
# Входные связи: contracts.DocumentMeta (JSONL, по строке на документ)
# Выходные данные: int — число прочитанных/загруженных строк meta.jsonl
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def load_documents(driver: Driver, meta_path: Path = DEFAULT_META_PATH, limit: int | None = None) -> int:
    rows: list[dict] = []
    n = 0
    with meta_path.open(encoding="utf-8") as f:
        for line in f:
            if limit is not None and n >= limit:
                break
            meta = DocumentMeta.model_validate_json(line)
            rows.append({
                "doc_id": meta.doc_id,
                "title": meta.title,
                "year": meta.year,
                "lang": meta.lang.value,
                "geography": meta.geography.value,
                "source_folder": meta.source_folder,
                "path": meta.path,
                "is_core": meta.is_core,
            })
            n += 1
    _write_document_batches(driver, rows)
    return n


# Назначение: пишет узлы Document батчами по LOAD_BATCH_SIZE через UNWIND+MERGE.
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def _write_document_batches(driver: Driver, rows: list[dict]) -> None:
    query = (
        "UNWIND $rows AS row "
        "MERGE (d:Document {doc_id: row.doc_id}) "
        "SET d.title = row.title, d.year = row.year, d.lang = row.lang, "
        "    d.geography = row.geography, d.source_folder = row.source_folder, "
        "    d.path = row.path, d.is_core = row.is_core"
    )
    with driver.session() as session:
        for i in range(0, len(rows), LOAD_BATCH_SIZE):
            session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])


# ─── load_chunks ──────────────────────────────────────────────────────────
# Назначение: грузит узлы Chunk + связь (:Document)-[:HAS_CHUNK]->(:Chunk)
#   батчами UNWIND+MERGE; чанк без embedding грузится без вектора + WARN
#   GRAPH-002 (не падает — A-04 может быть ещё не готов).
# Входные связи: contracts.Chunk (JSONL); ожидает существующие узлы Document
#   (load_documents должен быть вызван раньше по полному meta.jsonl)
# Выходные данные: (n_total, n_missing_embedding) — счётчики для отчёта/самопроверки
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def load_chunks(
    driver: Driver,
    chunks_path: Path = DEFAULT_CHUNKS_PATH,
    limit: int | None = None,
    logger=None,
) -> tuple[int, int]:
    rows: list[dict] = []
    n_total = 0
    n_missing = 0
    with chunks_path.open(encoding="utf-8") as f:
        for line in f:
            if limit is not None and n_total >= limit:
                break
            chunk = Chunk.model_validate_json(line)
            if chunk.embedding is None:
                n_missing += 1
                if logger is not None:
                    log_event(
                        logger, stage="lexical_load", event=GRAPH_MISSING_EMBEDDING, level="WARNING",
                        doc_id=chunk.doc_id,
                        detail=f"chunk_id={chunk.chunk_id} — эмбеддинг отсутствует, чанк загружен без вектора",
                    )
            rows.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "text": chunk.text,
                "start": chunk.start,
                "end": chunk.end,
                "lang": chunk.lang.value,
                "embedding": chunk.embedding,
            })
            n_total += 1
    _write_chunk_batches(driver, rows)
    return n_total, n_missing


# Назначение: пишет узлы Chunk + HAS_CHUNK батчами; embedding ставится только
#   если он не null (FOREACH-трюк — Cypher не умеет условный SET напрямую);
#   OPTIONAL MATCH + FOREACH на связь — чанк не теряется, даже если Document
#   ещё не загружен (например, смоук-прогон с --limit только по chunks).
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def _write_chunk_batches(driver: Driver, rows: list[dict]) -> None:
    query = (
        "UNWIND $rows AS row "
        "MERGE (c:Chunk {chunk_id: row.chunk_id}) "
        "SET c.doc_id = row.doc_id, c.text = row.text, c.start = row.start, "
        "    c.end = row.end, c.lang = row.lang "
        "WITH c, row "
        "FOREACH (_ IN CASE WHEN row.embedding IS NOT NULL THEN [1] ELSE [] END | "
        "  SET c.embedding = row.embedding) "
        "WITH c, row "
        "OPTIONAL MATCH (d:Document {doc_id: row.doc_id}) "
        "FOREACH (_ IN CASE WHEN d IS NOT NULL THEN [1] ELSE [] END | "
        "  MERGE (d)-[:HAS_CHUNK]->(c))"
    )
    with driver.session() as session:
        for i in range(0, len(rows), LOAD_BATCH_SIZE):
            session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])


# ─── detect_embedding_dimension ───────────────────────────────────────────
# Назначение: определяет размерность вектора по первому чанку с embedding —
#   размерность vector index нельзя хардкодить, проверяем по факту данных.
# Входные связи: тот же chunks-файл, что и load_chunks
# Выходные данные: int (размерность) или None — если ни одного вектора нет
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def detect_embedding_dimension(chunks_path: Path, limit: int | None = None) -> int | None:
    with chunks_path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            chunk = Chunk.model_validate_json(line)
            if chunk.embedding is not None:
                return len(chunk.embedding)
    return None


# ─── ensure_vector_index ──────────────────────────────────────────────────
# Назначение: создаёт vector index CHUNK_VECTOR_INDEX_NAME по Chunk.embedding
#   (cosine, размерность из detect_embedding_dimension) — потребуется search/retrieval.
# Входные связи: neo4j.Driver; dimension из detect_embedding_dimension
# Выходные данные: нет (побочный эффект — DDL в Neo4j)
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def ensure_vector_index(driver: Driver, dimension: int) -> None:
    # Имя индекса — фиксированная константа (не пользовательский ввод), Cypher
    # не позволяет параметризовать идентификаторы — конкатенация здесь безопасна.
    query = (
        f"CREATE VECTOR INDEX {CHUNK_VECTOR_INDEX_NAME} IF NOT EXISTS "
        "FOR (c:Chunk) ON (c.embedding) "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: $sim}}"
    )
    with driver.session() as session:
        session.run(query, dim=dimension, sim=VECTOR_SIMILARITY_FUNCTION)


# ─── self_check ───────────────────────────────────────────────────────────
# Назначение: самопроверка после загрузки — счётчики узлов/связей, наличие
#   vector index, пробный векторный запрос (топ-1 должен быть сам чанк).
# Входные связи: neo4j.Driver (после load_documents/load_chunks/ensure_vector_index)
# Выходные данные: dict с цифрами — в лог и в отчёт агента
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def self_check(driver: Driver) -> dict:
    report: dict = {}
    with driver.session() as session:
        report["n_documents"] = session.run("MATCH (d:Document) RETURN count(d) AS c").single()["c"]
        report["n_chunks"] = session.run("MATCH (c:Chunk) RETURN count(c) AS c").single()["c"]
        report["n_has_chunk"] = session.run("MATCH ()-[r:HAS_CHUNK]->() RETURN count(r) AS c").single()["c"]
        report["n_chunks_with_embedding"] = session.run(
            "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS c"
        ).single()["c"]

        index_names = {rec["name"] for rec in session.run("SHOW INDEXES YIELD name RETURN name")}
        report["vector_index_exists"] = CHUNK_VECTOR_INDEX_NAME in index_names

        report["vector_self_match"] = None
        if report["n_chunks_with_embedding"] > 0 and report["vector_index_exists"]:
            sample = session.run(
                "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL "
                "RETURN c.chunk_id AS id, c.embedding AS emb LIMIT 1"
            ).single()
            top = session.run(
                "CALL db.index.vector.queryNodes($index, 1, $vec) YIELD node, score "
                "RETURN node.chunk_id AS id, score",
                index=CHUNK_VECTOR_INDEX_NAME, vec=sample["emb"],
            ).single()
            report["vector_self_match"] = bool(top is not None and top["id"] == sample["id"])
    return report


# ─── main ──────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа полной загрузки лексического графа; печатает
#   сводку (для верификации задачи A-05) и пишет лог run_id в logs/pipeline/.
# Входные связи: аргументы --meta-path/--chunks-path/--limit
# Выходные данные: нет (побочный эффект — Neo4j + печать JSON-отчёта в stdout)
# Уровень: ✅ реализовано (A-05, worklogs/graph.md#2026-07-03)
def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка лексического графа Document->Chunk в Neo4j")
    parser.add_argument("--meta-path", type=Path, default=DEFAULT_META_PATH)
    parser.add_argument("--chunks-path", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число строк (смоук-прогон)")
    args = parser.parse_args()

    run_id = new_run_id("graph_")
    logger = get_logger("graph", run_id)
    driver = get_driver()
    try:
        ensure_constraints(driver)
        log_event(logger, stage="lexical_load", event="constraints_ready")

        n_docs = load_documents(driver, args.meta_path, limit=args.limit)
        log_event(logger, stage="lexical_load", event="documents_loaded", detail=f"n={n_docs}")

        n_chunks, n_missing = load_chunks(driver, args.chunks_path, limit=args.limit, logger=logger)
        log_event(
            logger, stage="lexical_load", event="chunks_loaded",
            detail=f"n={n_chunks} missing_embedding={n_missing}",
        )

        dimension = detect_embedding_dimension(args.chunks_path, limit=args.limit)
        if dimension is not None:
            ensure_vector_index(driver, dimension)
            log_event(logger, stage="lexical_load", event="vector_index_ready", detail=f"dim={dimension}")
        else:
            log_event(
                logger, stage="lexical_load", event="vector_index_skipped", level="WARNING",
                detail="ни одного чанка с embedding в выборке — индекс не создан",
            )

        report = self_check(driver)
        log_event(logger, stage="lexical_load", event="self_check", detail=json.dumps(report, ensure_ascii=False))

        summary = {
            "n_documents": n_docs, "n_chunks": n_chunks, "n_missing_embedding": n_missing,
            "embedding_dimension": dimension, **report,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
