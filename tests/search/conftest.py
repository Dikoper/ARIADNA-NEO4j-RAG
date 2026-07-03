"""Фикстуры тестов search/embeddings (A-04): маленькие JSONL-фикстуры + проверка
живого Ollama (для тестов, которым нужен реальный HTTP-вызов — смоук embed_texts,
проверка обхода прокси). Если Ollama на localhost:11434 недоступна, живые тесты
помечаются skip, а не падают — офлайновые тесты (контракт, перезапуск, изоляция
сбоев через мёртвый порт) от Ollama не зависят.

Вход: нет. Выход: FIXTURES_DIR, OLLAMA_LIVE (bool).
"""
from __future__ import annotations

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
