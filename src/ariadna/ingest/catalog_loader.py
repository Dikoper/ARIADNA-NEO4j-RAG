"""Загрузка каталожных карточек (contracts.CatalogEntry) в Neo4j — вынесено из
catalog.py декомпозицией по лимиту ~350 строк (CONVENTIONS §3).

Вход: list[contracts.CatalogEntry] (после catalog.build_catalog_entries +
catalog.embed_catalog_entries). Выход: узлы :CatalogEntry в Neo4j, векторный
индекс catalog_embedding_idx (cosine).
Зависимости: neo4j (bolt-драйвер), ariadna.contracts.
Инвариант: СТРОГО метка CatalogEntry — Document/Chunk и онтологические метки
не трогает (та же Neo4j-инфраструктура, что и graph/lexical_loader, образец
соединения — оттуда, Grep, без копирования лишнего).
Паспорт: docs/dev/modules/ingest.md (каталожный слой — постановка оркестратора A-20).
"""
from __future__ import annotations

import os
from pathlib import Path

from neo4j import Driver, GraphDatabase

from ariadna.contracts import CatalogEntry

ENV_FILE = Path(".env")
NEO4J_URI_VAR = "NEO4J_URI"
NEO4J_USER_VAR = "NEO4J_USER"
NEO4J_PASSWORD_VAR = "NEO4J_PASSWORD"
CATALOG_VECTOR_INDEX_NAME = "catalog_embedding_idx"
VECTOR_SIMILARITY_FUNCTION = "cosine"
LOAD_BATCH_SIZE = 500


# Назначение: читает KEY=VALUE из .env (без внешних зависимостей) — тот же
#   формат, что и graph/lexical_loader._read_env_file.
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
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


# ─── get_driver ─────────────────────────────────────────────────────────────
# Назначение: neo4j-драйвер по параметрам подключения — окружение процесса,
#   затем .env в корне проекта (та же инфраструктура, что и graph/lexical_loader).
# Входные связи: NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD (os.environ или .env)
# Выходные данные: neo4j.Driver, готовый к driver.session()
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def get_driver() -> Driver:
    env = _read_env_file(ENV_FILE)
    uri = os.environ.get(NEO4J_URI_VAR) or env.get(NEO4J_URI_VAR, "bolt://localhost:7687")
    user = os.environ.get(NEO4J_USER_VAR) or env.get(NEO4J_USER_VAR, "neo4j")
    password = os.environ.get(NEO4J_PASSWORD_VAR) or env.get(NEO4J_PASSWORD_VAR)
    if not password:
        raise RuntimeError(f"{NEO4J_PASSWORD_VAR} не задан ни в окружении, ни в {ENV_FILE}")
    return GraphDatabase.driver(uri, auth=(user, password))


# ─── ensure_catalog_constraint ─────────────────────────────────────────────
# Назначение: констрейнт уникальности CatalogEntry.catalog_id — идемпотентность MERGE.
# Входные связи: neo4j.Driver
# Выходные данные: нет (побочный эффект — DDL в Neo4j)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def ensure_catalog_constraint(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT catalog_entry_id_unique IF NOT EXISTS "
            "FOR (c:CatalogEntry) REQUIRE c.catalog_id IS UNIQUE"
        )


# ─── load_catalog_entries ──────────────────────────────────────────────────
# Назначение: грузит узлы :CatalogEntry батчами UNWIND+MERGE (идемпотентно);
#   СТРОГО метка CatalogEntry — Document/Chunk и онтологические метки не трогает.
# Входные связи: list[contracts.CatalogEntry] (с/без embedding)
# Выходные данные: int — число загруженных карточек
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def load_catalog_entries(driver: Driver, entries: list[CatalogEntry]) -> int:
    rows = [{
        "catalog_id": e.catalog_id, "path": e.path, "title": e.title, "kind": e.kind,
        "year_from": e.year_from, "year_to": e.year_to, "n_files": e.n_files,
        "description": e.description, "embedding": e.embedding,
    } for e in entries]
    query = (
        "UNWIND $rows AS row "
        "MERGE (c:CatalogEntry {catalog_id: row.catalog_id}) "
        "SET c.path = row.path, c.title = row.title, c.kind = row.kind, "
        "    c.year_from = row.year_from, c.year_to = row.year_to, "
        "    c.n_files = row.n_files, c.description = row.description "
        "WITH c, row "
        "FOREACH (_ IN CASE WHEN row.embedding IS NOT NULL THEN [1] ELSE [] END | "
        "  SET c.embedding = row.embedding)"
    )
    with driver.session() as session:
        for i in range(0, len(rows), LOAD_BATCH_SIZE):
            session.run(query, rows=rows[i : i + LOAD_BATCH_SIZE])
    return len(rows)


# ─── ensure_catalog_vector_index ───────────────────────────────────────────
# Назначение: векторный индекс catalog_embedding_idx на (CatalogEntry,
#   embedding), cosine — новая метка, коллизии с chunk_embedding_idx нет
#   (один индекс на пару label+property, см. грабли в CLAUDE.md).
# Входные связи: neo4j.Driver; dimension — размерность вектора из данных
# Выходные данные: нет (побочный эффект — DDL в Neo4j)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def ensure_catalog_vector_index(driver: Driver, dimension: int) -> None:
    query = (
        f"CREATE VECTOR INDEX {CATALOG_VECTOR_INDEX_NAME} IF NOT EXISTS "
        "FOR (c:CatalogEntry) ON (c.embedding) "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: $sim}}"
    )
    with driver.session() as session:
        session.run(query, dim=dimension, sim=VECTOR_SIMILARITY_FUNCTION)


# ─── self_check ─────────────────────────────────────────────────────────────
# Назначение: самопроверка после загрузки — счётчик узлов CatalogEntry,
#   наличие векторного индекса, пробный векторный self-match запрос.
# Входные связи: neo4j.Driver (после load_catalog_entries/ensure_catalog_vector_index)
# Выходные данные: dict с цифрами — в лог и в финальный отчёт
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def self_check(driver: Driver) -> dict:
    report: dict = {}
    with driver.session() as session:
        report["n_catalog_entries"] = session.run(
            "MATCH (c:CatalogEntry) RETURN count(c) AS n"
        ).single()["n"]
        report["n_with_embedding"] = session.run(
            "MATCH (c:CatalogEntry) WHERE c.embedding IS NOT NULL RETURN count(c) AS n"
        ).single()["n"]
        index_names = {rec["name"] for rec in session.run("SHOW INDEXES YIELD name RETURN name")}
        report["vector_index_exists"] = CATALOG_VECTOR_INDEX_NAME in index_names

        report["vector_self_match"] = None
        if report["n_with_embedding"] > 0 and report["vector_index_exists"]:
            sample = session.run(
                "MATCH (c:CatalogEntry) WHERE c.embedding IS NOT NULL "
                "RETURN c.catalog_id AS id, c.embedding AS emb LIMIT 1"
            ).single()
            top = session.run(
                "CALL db.index.vector.queryNodes($index, 1, $vec) YIELD node, score "
                "RETURN node.catalog_id AS id, score",
                index=CATALOG_VECTOR_INDEX_NAME, vec=sample["emb"],
            ).single()
            report["vector_self_match"] = bool(top is not None and top["id"] == sample["id"])
    return report
