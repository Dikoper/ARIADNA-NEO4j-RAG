"""Ollama native `/api/chat` клиент для LLM-извлечения — extraction/ollama_client.py (A-08).

Вход: messages (system+user[, ретрай-сообщения]) для одного чанка. Выход: сырой
content ответа модели (call_extraction_llm) либо распарсенный/провалидированный
_RawExtraction (parse_raw_extraction) — сущности/связи без constraints/doc_id/
chunk_id/model/prompt_hash (их проставляет llm_extract.py).
Зависимости: только stdlib (`urllib`, `json`, `hashlib`) + pydantic + contracts.py
(Entity/Relation — переиспользуются как схема ответа, значит EntityType/
RelationType/confidence валидируются тем же контрактом, что и граф).
Инвариант: native `/api/chat` (не `/v1/chat/completions`) — только native
поддерживает `"think": false`, обязателен для reasoning-модели (см. паспорт
extraction.md); HTTP-клиент без прокси (см. search/embeddings — 502 на localhost).
Паспорт: docs/dev/modules/extraction.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from pydantic import BaseModel, Field, ValidationError

from ariadna.contracts import Entity, Relation
from ariadna.extraction.prompt import SYSTEM_PROMPT

OLLAMA_BASE_URL_DEFAULT = "http://localhost:11434"
EXTRACTION_MODEL_DEFAULT = "qwen3.5:35b-a3b"
# ~30 с/чанк без thinking на живом стенде (замер оркестратора 03.07) — с запасом.
REQUEST_TIMEOUT_SEC = 300
NUM_PREDICT = 2500
TEMPERATURE = 0.1
ERR_TRUNCATE = 500  # усечение ответа LLM в лог (CONVENTIONS.md §4)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

# Ollama — локальная инфраструктура (не внешний сервис): системный прокси не
# должен применяться к localhost, иначе 502 (тот же факт, что и в embeddings.py).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ─── clean_env_var ──────────────────────────────────────────────────────
# Назначение: os.environ.get(key, default) с защитой от инлайн-комментария
#   (` # ...`) в значении — общий загрузчик .env (search/embeddings._load_dotenv,
#   чужой модуль) его не срезает; без этой защиты EXTRACTION_MODEL="qwen3.5:35b-
#   a3b          # комментарий" целиком уходит в тело запроса к Ollama (HTTP 400).
# Входные связи: os.environ, имя переменной, значение по умолчанию
# Выходные данные: str — значение без инлайн-комментария и хвостовых пробелов
# Уровень: ✅ реализовано (A-08)
def clean_env_var(key: str, default: str) -> str:
    return os.environ.get(key, default).split(" #", 1)[0].rstrip()


class OllamaExtractionError(Exception):
    """Ollama chat недоступна, вернула не-JSON транспорта или пустой content (EXTRACT-004)."""


class ExtractionSchemaError(Exception):
    """Ответ LLM не парсится / не проходит валидацию Entity/Relation (EXTRACT-001)."""


# ─── _RawExtraction ─────────────────────────────────────────────────────
# Назначение: схема сырого ответа LLM — только entities/relations (без
#   constraints/doc_id/chunk_id/model/prompt_hash, их проставляет код);
#   переиспользует contracts.Entity/Relation, поэтому типы онтологии и
#   ограничения confidence (0..1) валидируются тем же контрактом, что и граф.
# Уровень: ✅ реализовано (A-08)
class _RawExtraction(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


# ─── prompt_hash ─────────────────────────────────────────────────────────
# Назначение: sha256 системного промпта (константа, не зависит от чанка) —
#   первые 12 hex-символов, для группировки/воспроизведения ошибок по версии
#   промпта (ExtractionResult.prompt_hash).
# Входные связи: extraction.prompt.SYSTEM_PROMPT
# Выходные данные: str, 12 hex-символов
# Уровень: ✅ реализовано (A-08)
def prompt_hash() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


PROMPT_HASH = prompt_hash()


# ─── call_extraction_llm ────────────────────────────────────────────────
# Назначение: один запрос к Ollama native `/api/chat` (не /v1/ — нужен
#   "think": false, иначе reasoning-модель тратит бюджет на размышления и
#   ответ обрезается, см. паспорт модуля); "stream": false — весь JSON сразу.
# Входные связи: messages (system+user[, доп. ретрай-сообщения]); модель/хост
#   из EXTRACTION_MODEL/OLLAMA_BASE_URL (.env)
# Выходные данные: str — message.content ответа (сырой текст, не распарсенный)
# Уровень: ✅ реализовано (A-08)
def call_extraction_llm(
    messages: list[dict],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    model = model or clean_env_var("EXTRACTION_MODEL", EXTRACTION_MODEL_DEFAULT)
    base_url = (base_url or clean_env_var("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)).rstrip("/")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "think": False,     # reasoning-модель: без этого бюджет уходит на <think>, см. паспорт
        "stream": False,
        "options": {"num_predict": NUM_PREDICT, "temperature": TEMPERATURE},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/chat",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _NO_PROXY_OPENER.open(request, timeout=REQUEST_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise OllamaExtractionError(f"Ollama chat недоступна ({base_url}, model={model}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OllamaExtractionError(f"битый JSON транспорта от Ollama chat: {exc}") from exc

    content = ((body.get("message") or {}).get("content") or "").strip()
    if not content:
        raise OllamaExtractionError(f"пустой content в ответе Ollama chat: тело={str(body)[:ERR_TRUNCATE]}")
    return content


# Назначение: снимает возможную обёртку ```json … ``` вокруг ответа LLM.
# Уровень: ✅ реализовано (A-08)
def _strip_fences(content: str) -> str:
    return _FENCE_RE.sub("", content.strip()).strip()


# ─── parse_raw_extraction ────────────────────────────────────────────────
# Назначение: content LLM (после снятия ```json-обёртки) -> json.loads ->
#   валидация _RawExtraction (Entity/Relation, значит и EntityType/RelationType/
#   confidence 0..1 из contracts.py); любая ошибка на этом пути — единая
#   ExtractionSchemaError с текстом причины для ретрая/лога.
# Входные связи: сырой content от call_extraction_llm
# Выходные данные: _RawExtraction (entities/relations, ещё не канонизированы)
# Уровень: ✅ реализовано (A-08)
def parse_raw_extraction(content: str) -> _RawExtraction:
    cleaned = _strip_fences(content)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionSchemaError(f"невалидный JSON: {exc}") from exc
    try:
        return _RawExtraction.model_validate(data)
    except ValidationError as exc:
        raise ExtractionSchemaError(f"не прошло схему Entity/Relation: {exc}") from exc
