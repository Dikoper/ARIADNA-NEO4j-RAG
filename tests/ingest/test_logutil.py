"""Тесты logutil.py: JSON Lines в logs/pipeline/<run_id>.jsonl, обязательные поля,
идемпотентность get_logger по run_id, контекст в событии ERROR."""
from __future__ import annotations

import json

from ariadna import logutil

REQUIRED_FIELDS = {"ts", "run_id", "module", "stage", "doc_id", "level", "event", "detail"}


# Назначение: log_event пишет одну валидную строку JSON со всеми обязательными полями.
# Уровень: ✅ реализовано (A-02 tests)
def test_log_event_writes_json_line_with_required_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logutil, "_HANDLERS", {})
    run_id = "test_run_001"
    logger = logutil.get_logger("ingest", run_id)
    logutil.log_event(logger, stage="convert", event="converted", doc_id="doc1", detail="path=foo.pdf")

    log_file = tmp_path / f"{run_id}.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert REQUIRED_FIELDS.issubset(payload.keys())
    assert payload["run_id"] == run_id
    assert payload["module"] == "ingest"
    assert payload["stage"] == "convert"
    assert payload["doc_id"] == "doc1"
    assert payload["level"] == "INFO"
    assert payload["event"] == "converted"
    assert payload["detail"] == "path=foo.pdf"


# Назначение: событие ERROR обязано содержать непустой контекст воспроизведения
#   (правило CONVENTIONS.md §4 «ошибка = контекст»).
# Уровень: ✅ реализовано (A-02 tests)
def test_log_event_error_contains_reproduction_context(tmp_path, monkeypatch):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logutil, "_HANDLERS", {})
    run_id = "test_run_002"
    logger = logutil.get_logger("ingest", run_id)
    logutil.log_event(
        logger, stage="convert", event="INGEST-001", level="ERROR", doc_id="docX",
        detail="path=broken.pdf reason=не удалось открыть PDF: битый файл",
    )
    log_file = tmp_path / f"{run_id}.jsonl"
    payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert payload["level"] == "ERROR"
    assert payload["detail"] != ""
    assert "broken.pdf" in payload["detail"]


# Назначение: повторный вызов get_logger с тем же run_id не плодит дублирующихся
#   file-handler'ов — одно событие пишется в файл ровно один раз, а не N раз
#   (инвариант, заявленный в шапке logutil.py).
# Уровень: ✅ реализовано (A-02 tests)
def test_get_logger_same_run_id_does_not_duplicate_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logutil, "_HANDLERS", {})
    run_id = "test_run_003"
    logger1 = logutil.get_logger("ingest", run_id)
    logger2 = logutil.get_logger("ingest", run_id)  # повторный вызов — тот же run_id
    logutil.log_event(logger1, stage="s", event="e1", detail="d1")
    logutil.log_event(logger2, stage="s", event="e2", detail="d2")

    log_file = tmp_path / f"{run_id}.jsonl"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 2 события -> 2 строки, не 4 (не задвоено per-событие)


# Назначение: разные модули с одним run_id пишут в один и тот же файл
#   (сквозной run_id по всему конвейеру — CONVENTIONS.md §4).
# Уровень: ✅ реализовано (A-02 tests)
def test_get_logger_different_modules_same_run_id_share_file(tmp_path, monkeypatch):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logutil, "_HANDLERS", {})
    run_id = "test_run_004"
    ingest_logger = logutil.get_logger("ingest", run_id)
    extraction_logger = logutil.get_logger("extraction", run_id)
    logutil.log_event(ingest_logger, stage="s1", event="e1", doc_id="d1", detail="")
    logutil.log_event(extraction_logger, stage="s2", event="e2", doc_id="d1", detail="")

    log_file = tmp_path / f"{run_id}.jsonl"
    lines = [json.loads(ln) for ln in log_file.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 2
    modules = {ln["module"] for ln in lines}
    assert modules == {"ingest", "extraction"}


# Назначение: new_run_id даёт стабильный формат «<prefix>YYYYMMDDTHHMMSS».
# Уровень: ✅ реализовано (A-02 tests)
def test_new_run_id_format_with_prefix():
    run_id = logutil.new_run_id("ingest_")
    assert run_id.startswith("ingest_")
    stamp = run_id.removeprefix("ingest_")
    assert len(stamp) == 15  # YYYYMMDDTHHMMSS
    assert stamp[8] == "T"
    assert stamp[:8].isdigit()
    assert stamp[9:].isdigit()


# Назначение: без префикса new_run_id по-прежнему даёт валидный timestamp.
# Уровень: ✅ реализовано (A-02 tests)
def test_new_run_id_format_without_prefix():
    run_id = logutil.new_run_id()
    assert len(run_id) == 15
    assert run_id[8] == "T"


# Назначение: некорректный уровень (не из logging) не падает — используется INFO по умолчанию.
# Уровень: ✅ реализовано (A-02 tests)
def test_log_event_invalid_level_falls_back_to_info(tmp_path, monkeypatch):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logutil, "_HANDLERS", {})
    run_id = "test_run_005"
    logger = logutil.get_logger("ingest", run_id)
    logutil.log_event(logger, stage="s", event="e", level="NOPE", detail="")
    log_file = tmp_path / f"{run_id}.jsonl"
    payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert payload["level"] == "INFO"
