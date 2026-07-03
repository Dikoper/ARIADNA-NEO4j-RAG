"""Общий JSON-логгер конвейера «Ариадны».

Вход: module-имя вызывающего модуля (ingest/extraction/graph/search/…) + run_id
прогона. Выход: `logging.LoggerAdapter`, пишущий JSON Lines в
`logs/pipeline/<run_id>.jsonl` с обязательными полями
`ts, run_id, module, stage, doc_id, level, event, detail` (CONVENTIONS.md §4).
Зависимости: только стандартная библиотека (`logging`, `json`).
Инвариант: один файл на run_id — повторный вызов `get_logger` с тем же run_id
переиспользует уже открытый файловый handler, не плодит дубликаты записей.
Паспорт: docs/dev/modules/ingest.md (логгер создан в рамках задачи A-02,
пригоден для использования любым модулем конвейера).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs/pipeline")  # относительно корня проекта — единое место логов конвейера

# Реестр уже созданных файловых handler'ов по run_id — защита от дублирования
# записей при повторных вызовах get_logger() в рамках одного процесса.
_HANDLERS: dict[str, logging.Handler] = {}


class _JsonLinesFormatter(logging.Formatter):
    """Форматирует запись логгера в одну строку JSON с фиксированными полями."""

    # ─── format ──────────────────────────────────────────────────────
    # Назначение: превращает LogRecord в одну строку JSON с обязательными
    #   полями ts/run_id/module/stage/doc_id/level/event/detail.
    # Входные связи: logging.LogRecord с extra-полями из _RunLoggerAdapter
    # Выходные данные: str — готовая строка для записи в logs/pipeline/<run_id>.jsonl
    # Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": getattr(record, "run_id", ""),
            "module": getattr(record, "app_module", ""),
            "stage": getattr(record, "stage", ""),
            "doc_id": getattr(record, "doc_id", ""),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "detail": getattr(record, "detail", ""),
        }
        return json.dumps(payload, ensure_ascii=False)


class _RunLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter, сливающий постоянные поля (run_id, module) с полями вызова."""

    # ─── process ─────────────────────────────────────────────────────
    # Назначение: сливает постоянные поля (run_id, module) с полями конкретного
    #   вызова (stage, doc_id, event, detail) — стандартный LoggerAdapter.process
    #   перезаписывает per-call extra своим self.extra вместо объединения.
    # Входные связи: msg/kwargs вызова .info/.warning/.error с extra={...}
    # Выходные данные: (msg, kwargs) с объединённым kwargs["extra"]
    # Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
    def process(self, msg, kwargs):  # noqa: ANN001 — сигнатура фиксирована базовым классом
        extra = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return msg, kwargs


# ─── get_logger ────────────────────────────────────────────────────────
# Назначение: возвращает логгер конвейера, привязанный к модулю и run_id;
#   создаёт logs/pipeline/<run_id>.jsonl при первом обращении.
# Входные связи: имя модуля-вызывающего (строка), run_id прогона
# Выходные данные: logging.LoggerAdapter — методы .info/.warning/.error с extra=
#   {stage, doc_id, event, detail}, см. log_event() для готового обёрточного вызова
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def get_logger(module: str, run_id: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(f"ariadna.{module}.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if run_id not in _HANDLERS:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_DIR / f"{run_id}.jsonl", encoding="utf-8")
        handler.setFormatter(_JsonLinesFormatter())
        _HANDLERS[run_id] = handler
    if _HANDLERS[run_id] not in logger.handlers:
        logger.addHandler(_HANDLERS[run_id])
    return _RunLoggerAdapter(logger, {"run_id": run_id, "app_module": module})


# ─── log_event ─────────────────────────────────────────────────────────
# Назначение: логирует одно событие конвейера с обязательным контекстом
#   воспроизведения (правило «ошибка = контекст», CONVENTIONS.md §4).
# Входные связи: logging.LoggerAdapter из get_logger(); строки stage/event/detail
# Выходные данные: запись JSON Lines в файл логгера; ничего не возвращает
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def log_event(
    logger: logging.LoggerAdapter,
    *,
    stage: str,
    event: str,
    level: str = "INFO",
    doc_id: str = "",
    detail: str = "",
) -> None:
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    logger.log(
        numeric_level,
        event,
        extra={"stage": stage, "doc_id": doc_id, "event": event, "detail": detail},
    )


# ─── new_run_id ────────────────────────────────────────────────────────
# Назначение: генерирует стабильный ID прогона по времени запуска (для имени
#   файла логов и сквозной трассировки run_id через конвейер).
# Входные связи: опциональный префикс (например, «ingest_»)
# Выходные данные: str — ID прогона вида «<prefix>YYYYMMDDTHHMMSS»
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def new_run_id(prefix: str = "") -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{prefix}{stamp}" if prefix else stamp
