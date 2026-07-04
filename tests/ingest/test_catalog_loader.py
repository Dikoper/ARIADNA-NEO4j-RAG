"""Тесты catalog_loader.py (A-20): src/ariadna/ingest/catalog_loader.py.

Юнит-тесты чистых функций (_read_env_file, get_driver) и Cypher-запросов
(мок neo4j.Driver — проверка формы запроса/параметров, без сети). Отдельно —
лёгкий смоук чтения из ЖИВОГО Neo4j (bolt://localhost:7687, кредлы из .env):
только MATCH/SHOW INDEXES, без записи и без пересчёта эмбеддингов. Смоук
пропускается автоматически, если Neo4j недоступен в окружении.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ariadna.contracts import CatalogEntry
from ariadna.ingest import catalog_loader


# ═══════════════════════ _read_env_file ═══════════════════════

def test_read_env_file_parses_key_value(tmp_path):
    p = tmp_path / ".env"
    p.write_text("NEO4J_URI=bolt://x:7687\n# comment\n\nNEO4J_PASSWORD=secret\n", encoding="utf-8")
    assert catalog_loader._read_env_file(p) == {
        "NEO4J_URI": "bolt://x:7687",
        "NEO4J_PASSWORD": "secret",
    }


def test_read_env_file_missing_file_returns_empty(tmp_path):
    assert catalog_loader._read_env_file(tmp_path / "nope.env") == {}


def test_read_env_file_value_with_equals_sign_kept_whole(tmp_path):
    p = tmp_path / ".env"
    p.write_text("SOME_URL=http://host?x=1&y=2\n", encoding="utf-8")
    assert catalog_loader._read_env_file(p)["SOME_URL"] == "http://host?x=1&y=2"


def test_read_env_file_ignores_blank_and_comment_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("\n  \n# full comment\nA=1\n   # indented comment\nB=2\n", encoding="utf-8")
    assert catalog_loader._read_env_file(p) == {"A": "1", "B": "2"}


# ═══════════════════════ get_driver ═══════════════════════

def test_get_driver_raises_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setattr(catalog_loader, "ENV_FILE", tmp_path / "no.env")
    with pytest.raises(RuntimeError):
        catalog_loader.get_driver()


def test_get_driver_env_var_priority_over_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("NEO4J_PASSWORD=from_file\nNEO4J_URI=bolt://filehost:7687\n", encoding="utf-8")
    monkeypatch.setattr(catalog_loader, "ENV_FILE", env_file)
    monkeypatch.setenv("NEO4J_PASSWORD", "from_env")
    monkeypatch.delenv("NEO4J_URI", raising=False)
    driver = catalog_loader.get_driver()
    try:
        assert driver is not None  # драйвер ленивый — соединение не открывается при создании
    finally:
        driver.close()


def test_get_driver_defaults_when_nothing_set(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_loader, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USER", raising=False)
    driver = catalog_loader.get_driver()
    driver.close()


# ═══════════════════════ Cypher-запросы через фейковый Driver ═══════════════════════

class _FakeResult:
    def __init__(self, records=None):
        self._records = list(records or [])

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, recorder):
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **kwargs):
        self._recorder.append((query, kwargs))
        return _FakeResult()


class _FakeDriver:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return _FakeSession(self.calls)


def _entry(catalog_id: str, embedding=None) -> CatalogEntry:
    return CatalogEntry(catalog_id=catalog_id, path=f"p{catalog_id}", title=f"t{catalog_id}",
                         kind="journal", n_files=1, description="d", embedding=embedding)


# Назначение: констрейнт уникальности catalog_id — форма Cypher.
# Уровень: юнит (мок Driver)
def test_ensure_catalog_constraint_query_shape():
    driver = _FakeDriver()
    catalog_loader.ensure_catalog_constraint(driver)
    query, _ = driver.calls[0]
    assert "CatalogEntry" in query
    assert "catalog_id IS UNIQUE" in query
    assert "IF NOT EXISTS" in query


# Назначение: векторный индекс — правильное имя, метка, размерность и cosine.
# Уровень: юнит (мок Driver)
def test_ensure_catalog_vector_index_query_and_params():
    driver = _FakeDriver()
    catalog_loader.ensure_catalog_vector_index(driver, 1024)
    query, kwargs = driver.calls[0]
    assert catalog_loader.CATALOG_VECTOR_INDEX_NAME in query
    assert "CatalogEntry" in query
    assert kwargs["dim"] == 1024
    assert kwargs["sim"] == "cosine"


# Назначение: load_catalog_entries бьёт на батчи по LOAD_BATCH_SIZE, строки
#   несут все поля контракта (embedding может быть None), возвращает len(entries).
# Уровень: юнит (мок Driver) — эквивалент проверки формы без сети
def test_load_catalog_entries_batches_and_row_shape(monkeypatch):
    entries = [_entry("0", embedding=[0.1]), _entry("1", embedding=None), _entry("2", embedding=[0.2])]
    monkeypatch.setattr(catalog_loader, "LOAD_BATCH_SIZE", 2)
    driver = _FakeDriver()
    n = catalog_loader.load_catalog_entries(driver, entries)
    assert n == 3
    assert len(driver.calls) == 2  # 3 записи батчами по 2 -> 2 вызова session.run
    query0, kwargs0 = driver.calls[0]
    assert "MERGE (c:CatalogEntry" in query0
    assert "catalog_id" in query0
    rows0 = kwargs0["rows"]
    assert len(rows0) == 2
    assert rows0[0]["catalog_id"] == "0"
    assert rows0[0]["embedding"] == [0.1]
    assert rows0[1]["embedding"] is None
    query1, kwargs1 = driver.calls[1]
    assert len(kwargs1["rows"]) == 1


# Назначение: пустой список карточек не должен обращаться к driver.session()
#   вовсе (0 итераций диапазона батчей) — но и не должен падать.
# Уровень: граничный случай
def test_load_catalog_entries_empty_list(monkeypatch):
    driver = _FakeDriver()
    n = catalog_loader.load_catalog_entries(driver, [])
    assert n == 0
    assert driver.calls == []


# ═══════════════════════ Смоук на живом Neo4j (только чтение) ═══════════════════════

def _neo4j_reachable() -> bool:
    try:
        driver = catalog_loader.get_driver()
    except RuntimeError:
        return False
    try:
        with driver.session() as session:
            session.run("RETURN 1").single()
        return True
    except Exception:
        return False
    finally:
        driver.close()


_NEO4J_UP = _neo4j_reachable()


# Назначение: смоук читает боевые данные боевого прогона A-20 (86 карточек,
#   86 эмбеддингов, векторный индекс) — НЕ пишет и НЕ пересчитывает эмбеддинги,
#   self_check делает только MATCH/SHOW INDEXES/CALL db.index.vector.queryNodes.
# Уровень: смоук (сеть, живой Neo4j)
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j недоступен в этом окружении (bolt://localhost:7687)")
def test_smoke_live_neo4j_catalog_entries():
    driver = catalog_loader.get_driver()
    try:
        report = catalog_loader.self_check(driver)
    finally:
        driver.close()
    assert report["n_catalog_entries"] == 86
    assert report["n_with_embedding"] == 86
    assert report["vector_index_exists"] is True
    assert report["vector_self_match"] is True
