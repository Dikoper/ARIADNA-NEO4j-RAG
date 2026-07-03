# Worklog: deploy

Шаблон записи (≤ 8 строк). Эскалация — блок `⛔ ЭСКАЛАЦИЯ:` (симптом, что пробовал, гипотезы).

```markdown
## ГГГГ-ММ-ДД ЧЧ:ММ · <агент> · <ID задачи>
**Сделано:** …
**Решения:** …
**Проблемы:** …
**Открыто:** …
```

---

## 2026-07-03 17:10 · module-dev · A-01
**Сделано:** `deploy/docker-compose.yml` (neo4j:5.26-community + vllm/vllm-openai:v0.18.0-cu130
+ ollama/ollama:0.31.1, healthcheck у всех трёх, GPU-проброс, именованные volume);
`.env.example` дополнен (порты, VLLM_MODEL, HF-токен); README «Быстрый старт» и стек;
CLAUDE.md — 2 формулировки закрыты.
**Решения:** HF-id — `ig1/Qwen3.5-35B-A3B-NVFP4` (~23.4 ГБ; официального NVFP4 от
Qwen/RedHatAI для 3.5 нет — есть только для 3.6, поэтому community-сборка под vLLM
v0.18.0-cu130). Ollama 0.31.1 (мин. требование для Qwen3.5/Gated DeltaNet — 0.17.4).
Явный `name: ariadna` в compose + `--env-file .env` в README (иначе .env ищется
рядом с compose-файлом в deploy/, а не в корне — нашёл на верификации).
**Проблемы:** инлайн-комментарий в `.env.example` после пустого значения ломал
парсинг (`HUGGING_FACE_HUB_TOKEN` подхватывал текст комментария как значение) —
вынес комментарий на отдельную строку.
**Открыто:** вес vLLM/образ и NVFP4-веса не скачивались (по ограничению задачи) —
решение о загрузке за оркестратором. Neo4j+Ollama подняты и проверены healthy
(APOC + vector index процедуры на месте), `qwen3-embedding:0.6b` скачан (639 МБ).

---

## 2026-07-03 17:40 · module-tester · A-01
**Сделано:** 10 тестов в `tests/deploy/test_compose.py` (config/healthcheck/volumes/
секреты/.env.example↔compose/README), 9 прошло / 1 упал. Живые проверки docker
(read-only) — см. отчёт оркестратору.
**Найдено:** (1) `VLLM_GPU_MEM_UTIL`, `VLLM_MAX_MODEL_LEN`, `VLLM_MAX_NUM_SEQS`
используются в `deploy/docker-compose.yml` (vllm.command), но отсутствуют в
`.env.example` — тест `test_all_compose_vars_in_env_example` падает.
(2) Neo4j сейчас `unhealthy`: cypher-shell healthcheck стабильно ловит
"unauthorized" с паролем из текущего `.env` — похоже на рассинхрон
NEO4J_PASSWORD с паролем, уже зафиксированным в `neo4j_data` (том создан
раньше правки .env). Ollama healthy (`qwen3-embedding:0.6b` на месте), vLLM
health: starting (качает веса NVFP4, ожидаемо). Оба образа + neo4j — linux/arm64
подтверждено. `pytest`/`pyyaml` отсутствовали в `.venv` — доустановлены локально
для прогона тестов, но не добавлены в `pyproject.toml` (не в зоне ответственности
tester).
**Открыто:** нужна ревизия .env.example (добавить 3 переменные) и решение по
Neo4j (сброс пароля/пересоздание volume — вне полномочий tester, docker down
запрещён по инструкции). `pytest`+`pyyaml` стоит добавить в `pyproject.toml`
dev-зависимостями отдельной задачей.

---

## 2026-07-03 19:10 · fixer · A-01
**Причина:** REJECT ревью — образ vllm v0.18.0-cu130 не стартует на GB10 (cuda cap
12.1 > 12.0) + ревизия решения PM 03.07.2026: основной рантайм извлечения — Ollama
(qwen3.5:35b-a3b, int4), vLLM запаркован (не удалён, веса в vllm_hf_cache).
**Патч:** compose — `profiles: ["vllm"]` + актуализация шапки; .env.example/.env —
Ollama основной, 3 переменные VLLM_* (замечание reviewer); README — быстрый старт под
Ollama, подраздел vLLM-опционально, предупреждение о смене NEO4J_PASSWORD после
инициализации тома; CLAUDE.md — 2 формулировки; pyproject — pytest+pyyaml.
**Проверено:** config --services без vllm (с `--profile vllm` — есть); up -d не тронул
healthy neo4j/ollama; qwen3.5:35b-a3b (23 ГБ) скачан, генерация через /v1/chat/completions
отвечает осмысленно («Париж»); pytest tests/deploy 10 passed (бывший красный зелёный);
lint_precomments ок. Устаревших тестов нет. **Открыто:** нет.
