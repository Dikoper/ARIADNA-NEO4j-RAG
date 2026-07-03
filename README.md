# Ариадна — карта знаний R&D для горно-металлургической отрасли

Решение задачи «Научный клубок» хакатона Норникеля: GraphRAG-система, которая извлекает
сущности и связи из корпуса научно-технических документов, строит граф знаний в Neo4j
и отвечает на естественно-языковые запросы с числовыми ограничениями, географией и
провенансом до исходного документа.

## Архитектура

```
Документы (PDF/DOCX/PPTX)
   ↓  ingest: конвертация → текст + метаданные
   ↓  extraction: LLM-извлечение по онтологии + правила для чисел/единиц
Neo4j: двойной граф
   • лексический:  Document → Chunk (embeddings, vector index)
   • сущностный:   Experiment / TechSolution / Material / Process / Equipment /
                   Property / Publication / Expert / Facility (+ provenance до чанка)
   ↓  search: роутер запросов → шаблоны Cypher + гибридный retrieval
   ↓  analytics: литобзор, консенсус/разногласия, карта пробелов
UI: чат-поиск · интерактивный граф · карта пробелов
```

## Структура репозитория

```
src/ariadna/
  ingest/       конвертация документов в текст
  extraction/   LLM-пайплайн извлечения, правила чисел/единиц, синонимия RU/EN
  graph/        онтология, загрузка в Neo4j, шаблоны Cypher
  search/       ретриверы, роутер запросов, синтез ответов
  analytics/    литобзор, gap-анализ, экспорт MD/JSON-LD
  api/          FastAPI-бэкенд
ui/             Streamlit-интерфейс
ontology/       OWL-онтология, словарь синонимов RU/EN
config/         конфигурация пайплайна
scripts/        скрипты запуска этапов пайплайна
deploy/         docker-compose (Neo4j + Ollama; vLLM запаркован, профиль `vllm`)
tests/          тесты
docs/           презентация и материалы сдачи
data/           корпус документов (не в git; https://disk.yandex.ru/d/npigiuw4Rbe9Pg)
```

## Быстрый старт

Железо: DGX Spark (ARM64, GB10/Blackwell, ~120 ГБ unified memory), GPU хоста
пробрасывается в Ollama — нужен nvidia-container-toolkit (`docker run --gpus
all ... nvidia-smi` должен отработать до `docker compose up`).

```bash
cp .env.example .env          # заполнить NEO4J_PASSWORD и (опц.) сдвинуть порты

# ВАЖНО: .env лежит в корне репо, а compose-файл — в deploy/, поэтому везде ниже
# нужен явный --env-file .env (иначе docker compose ищет .env рядом с compose-файлом
# и не находит пароль/порты). Удобно завести алиас:
alias dc='docker compose --env-file .env -f deploy/docker-compose.yml'

# 1. Поднять инфраструктуру: Neo4j + Ollama (основной рантайм, решение PM 03.07.2026:
#    vLLM запаркован — см. подраздел ниже; профиль `vllm` в compose по умолчанию не стартует)
dc up -d

# 2. Дождаться готовности (healthcheck обоих сервисов — DEPLOY-001):
#    пайплайн стартует только после того, как neo4j станет healthy.
watch dc ps

# 3. Скачать модели в Ollama (НЕ входят в образ — тянутся отдельно, места на диске):
#    - извлечение, основная модель (int4, ~24 ГБ):
dc exec ollama ollama pull qwen3.5:35b-a3b
#    - эмбеддинги (маленькая, ~0.6 ГБ):
dc exec ollama ollama pull qwen3-embedding:0.6b
#    - опционально: облегчённый откат извлечения (~5-6 ГБ, Q4), если 35B не
#      помещается по памяти/времени:
dc exec ollama ollama pull qwen3.5:9b

# 4. Проверка готовности:
curl -s http://localhost:${NEO4J_HTTP_PORT:-7474} >/dev/null && echo "neo4j OK"
curl -s http://localhost:${OLLAMA_PORT:-11434}/api/tags && echo   # список загруженных моделей
curl -s http://localhost:${OLLAMA_PORT:-11434}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5:35b-a3b","messages":[{"role":"user","content":"Скажи привет одним словом."}],"max_tokens":30}'

# далее — скрипты пайплайна из scripts/ (см. документацию по мере разработки)
```

**Внимание — пароль Neo4j:** `NEO4J_AUTH` применяется только при первой инициализации
volume `neo4j_data`. Если сменить `NEO4J_PASSWORD` в `.env` после первого `dc up`, Neo4j
продолжит требовать старый пароль (хранится в томе) — контейнер будет падать в
healthcheck с "unauthorized". Лечение: `docker volume rm ariadna_neo4j_data` (граф в
Neo4j будет потерян) либо `cypher-shell` + `ALTER CURRENT USER SET PASSWORD FROM
'старый' TO 'новый'` без пересоздания тома.

### vLLM — опционально (профиль `vllm`, не основной путь)

Образ `vllm/vllm-openai:v0.18.0-cu130` в текущем теге **не стартует на GB10**: cuda
capability GPU — 12.1, PyTorch в образе поддерживает максимум 12.0 (контейнер уходит
в краш-луп). Сервис запаркован в compose (`profiles: ["vllm"]`), веса NVFP4
(`ig1/Qwen3.5-35B-A3B-NVFP4`, ~21.8 ГБ) остаются в volume `vllm_hf_cache`. Если
понадобится реанимировать: обновить тег образа по официальному рецепту vLLM для
DGX Spark (Blackwell/cuda 12.1+), затем `dc --profile vllm up -d vllm`.

## Стек

Neo4j (граф + vector index) · neo4j-graphrag · Qwen3.5-35B-A3B через Ollama (int4)
(извлечение — локально, откат: Ollama/qwen3.5:9b) · Qwen3-Embedding-0.6B (эмбеддинги
RU/EN, Ollama) · Qwen3.5 через Ollama (синтез ответов; Claude API — опция через ANSWER_BACKEND) · FastAPI · Streamlit
