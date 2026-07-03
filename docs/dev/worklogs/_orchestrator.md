# Worklog: _orchestrator (Fable)

Журнал решений оркестратора: bootstrap, приёмка вех, эскалации, деградация.

---

## 2026-07-03 · оркестратор (Fable) · A-00
**Сделано:** bootstrap: CLAUDE.md, TASK.md, ARCHITECTURE.md, contracts.py (🔒),
CONVENTIONS.md, ERRORS.md, lint_precomments.py, pyproject.toml, STATUS.md (беклог
A-01…A-19 + вехи M-01…M-06 + Could C-01…C-04), 8 паспортов модулей, worklog-и.
**Решения:** улучшения У-1…У-4 (ревизия 3 анализа) вшиты в контракты (Recommendation,
Contradiction, ComparisonRow, Provenance), паспорта и беклог; api — вне критического
пути (UI импортирует search напрямую); chunking отнесён к ingest, embeddings — к search.
**Проблемы:** pydantic отсутствовал в системе — создан .venv, пакет ставится `pip install -e .`.
**Открыто:** выбор qwen3:8b vs 14b — по факту скорости на GB10 (решится в A-08).
