"""Фикстуры тестов search (A-04 embeddings, M-01 rag_demo): маленькие JSONL-фикстуры
+ проверка живых Ollama/Neo4j (для тестов, которым нужен реальный HTTP/bolt-вызов —
смоук embed_texts/answer_question, проверка обхода прокси). Если стенд недоступен,
живые тесты помечаются skip, а не падают — офлайновые тесты (контракт, перезапуск,
изоляция сбоев через мёртвый порт/хост) от живой инфраструктуры не зависят.

Вход: нет. Выход: FIXTURES_DIR, OLLAMA_LIVE (bool), NEO4J_LIVE (bool).
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ─── _check_ollama_alive ───────────────────────────────────────────────────
# Назначение: проверяет доступность Ollama в обход системного HTTP_PROXY (тот же
#   приём, что embeddings._NO_PROXY_OPENER) — иначе живые тесты ошибочно
#   скипнутся/упадут из-за прокси, а не из-за отсутствия Ollama.
# Уровень: ✅ реализовано (module-tester A-04)
def _check_ollama_alive() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://localhost:11434/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


OLLAMA_LIVE = _check_ollama_alive()


# ─── _check_neo4j_alive ─────────────────────────────────────────────────────
# Назначение: проверяет доступность Neo4j (bolt, NEO4J_URI/USER/PASSWORD из .env) —
#   для живого смоука rag_demo.answer_question (M-01); импорт embeddings как
#   побочный эффект подхватывает .env в os.environ (тот же приём, что в rag_demo.py).
# Уровень: ✅ реализовано (module-tester M-01)
def _check_neo4j_alive() -> bool:
    from ariadna.search import embeddings  # noqa: F401 — импорт грузит .env (embeddings._load_dotenv)

    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        return False
    try:
        from neo4j import GraphDatabase

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            driver.verify_connectivity()
            return True
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 — любой сбой подключения = живого стенда нет
        return False


NEO4J_LIVE = _check_neo4j_alive()
