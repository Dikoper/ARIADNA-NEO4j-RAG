# Ариадна — карта знаний R&D для горно-металлургической отрасли

Решение задачи «Научный клубок» хакатона Норникеля: GraphRAG-система, которая извлекает
сущности и связи из корпуса научно-технических документов (статьи, обзоры, доклады,
RU/EN), строит граф знаний в Neo4j и отвечает на естественно-языковые запросы с
числовыми ограничениями, географией, временными рамками и провенансом до исходного
чанка. Стек полностью локальный: Neo4j + Qwen через Ollama на DGX Spark, без внешних
API в проде.

**Ключевые цифры системы** (актуальны на момент сдачи, см. `artifacts/SESSION-07.md`):

| Показатель | Значение |
|---|---|
| Документов обработано (ingest) | 177 |
| Чанков | 9 580 |
| Каталожных карточек неиндексированного корпуса (CatalogEntry) | 86 |
| Сущностей в графе (Entity) | 23 600+ |
| Связей в графе | 25 063 |
| Числовых ограничений (NumericConstraint) | 8 179 |
| Документов с гео-разметкой | 175 / 177 |
| География опыта (ru / foreign / global) | 65 / 56 / 54 |
| Тесты | 620 passed + 3 xfail (`pytest tests/`) |

## Демо за 3 шага

Это главный раздел — жюри может запустить демо, не читая остальное.

```bash
# 1. Поднять инфраструктуру (Neo4j уже наполнен графом из репозитория данных —
#    ничего пересчитывать не нужно; Ollama нужен только если жюри захочет живой
#    синтез нового вопроса вне 4 пресетов, см. ограничения ниже)
cp .env.example .env   # заполнить NEO4J_PASSWORD тем же значением, которым наполнялась БД
alias dc='docker compose --env-file .env -f deploy/docker-compose.yml'
dc up -d
watch dc ps            # ждать healthy у обоих сервисов

# 2. Запустить демо-интерфейс
.venv/bin/python -m streamlit run ui/app.py

# 3. В открывшемся браузере (http://localhost:8501) — вкладка «Чат»,
#    нажать любой из 4 пресетов жюри
```

Ответы на 4 эталонных вопроса приходят **мгновенно** — они синтезированы заранее и
прогреты в `data/processed/answer_cache.json`. Живой синтез нового вопроса локальной
LLM занимает 24–480 секунд (зависит от загрузки Ollama) — на демо для жюри
используется только кэш; кнопка «пересчитать» в UI живой путь тоже поддерживает,
но не гарантирует ответ за секунды.

## Что умеет система

- **4 эталонных запроса жюри** — сквозной путь вопрос → роутер → граф+вектор → ответ
  с цитатами → подграф в UI:
  1. Методы обессоливания воды под параметры (сульфаты/хлориды/Ca/Mg/Na 200–300 мг/л,
     сухой остаток ≤1000 мг/дм³).
  2. Циркуляция католита при электроэкстракции никеля — мировая практика + скорость потока.
  3. Распределение Au/Ag/МПГ между штейном и шлаком — эксперименты и публикации за 5 лет.
  4. Закачка шахтных вод в глубокие горизонты — РФ vs зарубеж.
- **Карта пробелов ⭐** (`analytics.gap_map`) — матрица «материал × процесс» без прямой
  связи в графе + списки тем, которые встречаются **только** в отечественной (44 темы)
  или **только** в зарубежной (225 тем) практике. Приёмочная ячейка №1 по умолчанию —
  «медно-никелевая руда × кучное выщелачивание» (пробел, который явно называет задание).
  Q4 (закачка шахтных вод) в корпусе **физически отсутствует как тема** — система честно
  отвечает «в корпусе не найдено» и показывает смежные альтернативы (захоронение,
  поглощающие горизонты). Это фича карты пробелов, не баг: демонстрирует, что система
  не галлюцинирует там, где данных нет.
- **Фильтры гео/год** в сайдбаре UI: география — по признаку описываемого опыта в
  документе (не по языку текста — 158/177 документов русскоязычны, но описывают в т.ч.
  зарубежные практики), год — фильтрует цитаты чата по `Citation.year`.
- **Подсветка contradicts** (У-3) — противоречащие факты в подграфе ответа выделены
  красным.
- **Provenance до чанка** — каждый факт в графе и каждая цитата в ответе трассируется
  до конкретного чанка исходного документа (`doc_id`/`chunk_id`), не только до файла.
- **Ручная правка эксперта** (У-4) — версионирование-минимум на узлах/связях графа:
  `confidence`, `updated_at`, `edited_by` (пусто = автоизвлечение). Правится прямо в
  Neo4j Browser (`http://localhost:7474`), без кода — см. `docs/submission/DEMO-SCRIPT.md`
  для готового Cypher-примера.

## Полный пайплайн end-to-end

Ниже — реальные точки входа модулей (проверено по факту в исходниках, аргументы CLI —
argparse каждого модуля). Порядок соответствует потоку данных из
`docs/dev/ARCHITECTURE.md`. Neo4j в репозитории **уже наполнен** этим пайплайном —
повторный прогон нужен только для воспроизведения с нуля или на новом корпусе.

```bash
# 1. ingest — конвертация корпуса в текст + чанкинг (без аргументов)
#    вход: data/Обзоры|Статьи|Доклады/*  выход: data/processed/{meta,texts,chunks,skipped}.jsonl
.venv/bin/python -m ariadna.ingest.pipeline

# 2. ingest.select — отбор документов под 4 эталонных запроса + флаг is_core
#    (без аргументов; правит meta.jsonl на месте + пишет targets.jsonl)
.venv/bin/python -m ariadna.ingest.select

# 3. ingest.geo_classify — гео-разметка правилами (маркеры RU/foreign по вхождению)
#    выход: data/processed/doc_geography.jsonl (unknown-хвост уходит в отдельную
#    LLM-доразметку оркестратора — вне CLI-пайплайна, слияние geography в meta.jsonl
#    делает оркестратор вручную поверх doc_geography.jsonl)
.venv/bin/python -m ariadna.ingest.geo_classify [--meta-path PATH] [--chunks-path PATH] [--output PATH] [--json]

# 4. search.embeddings — офлайн-батч эмбеддингов чанков (Qwen3-Embedding-0.6B, Ollama)
#    (без аргументов; вход chunks.jsonl → выход chunks_embedded.jsonl)
.venv/bin/python -m ariadna.search.embeddings

# 5. graph.lexical_loader — лексический граф Document->Chunk + vector index в Neo4j
.venv/bin/python -m ariadna.graph.lexical_loader [--meta-path PATH] [--chunks-path PATH] [--limit N]

# 6. extraction.llm_extract — LLM-извлечение сущностей/связей по онтологии
#    (Qwen3.5-35B-A3B через Ollama; числа/единицы — ТОЛЬКО правилами, не LLM)
#    выход: data/processed/extracted.jsonl (+ extract_skiplist.jsonl для сбойных чанков)
.venv/bin/python -m ariadna.extraction.llm_extract [--limit N] [--targets data/processed/targets.jsonl] [--workers N]

# 7. graph.entity_loader — загрузка сущностного графа (8 типов узлов / 6 типов связей) в Neo4j
#    ВНИМАНИЕ: --input по умолчанию указывает на data/processed/extracted_haiku.jsonl —
#    исторический файл текущей боевой БД (обогащение волной Haiku до отключения Claude
#    API решением PM 03.07.2026). При прогоне с нуля на локальной LLM указывайте явно
#    --input data/processed/extracted.jsonl (выход шага 6)
.venv/bin/python -m ariadna.graph.entity_loader --input data/processed/extracted.jsonl [--meta-path PATH] [--limit N]

# 8. graph.doc_geography_loader — загрузка гео-разметки документов в Neo4j (Document.geography)
.venv/bin/python -m ariadna.graph.doc_geography_loader --input data/processed/doc_geography.jsonl

# 9. ingest.catalog — каталожные карточки неиндексированной части корпуса (Журналы,
#    Материалы конференций) — CatalogEntry в Neo4j для панели рекомендаций UI
.venv/bin/python -m ariadna.ingest.catalog [--dry-run] [--no-load]

# 10. analytics.gap_map — карта пробелов (CLI печатает GapReport в консоль; UI дергает
#     ту же функцию build_gap_report() напрямую)
.venv/bin/python -m ariadna.analytics.gap_map [--limit N] [--json]

# 11. search.answer — синтез одного ответа с цитатами и пометкой contradicts
.venv/bin/python -m ariadna.search.answer "Ваш вопрос на русском" [--top-k N]
```

## Структура репозитория

```
src/ariadna/
  ingest/       конвертация документов в текст, отбор ядра, гео-разметка, каталог
  extraction/   LLM-пайплайн извлечения, правила чисел/единиц, синонимия RU/EN
  graph/        онтология, загрузка в Neo4j, шаблоны Cypher
  search/       ретриверы, роутер запросов, синтез ответов
  analytics/    литобзор, gap-анализ, экспорт MD/JSON-LD
  api/          FastAPI-бэкенд
ui/             Streamlit-интерфейс
ontology/       OWL-онтология, словарь синонимов RU/EN
config/         конфигурация пайплайна
scripts/        скрипты запуска этапов пайплайна, lint_precomments.py
deploy/         docker-compose (Neo4j + Ollama; vLLM запаркован, профиль `vllm`)
tests/          тесты (620 passed + 3 xfail)
docs/           паспорта модулей, архитектура, материалы сдачи (docs/submission/)
data/           корпус документов (не в git; https://disk.yandex.ru/d/npigiuw4Rbe9Pg)
```

## Быстрый старт (инфраструктура)

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

# 5. Демо (см. раздел «Демо за 3 шага» выше) либо полный пайплайн end-to-end
#    (см. раздел выше) для воспроизведения графа с нуля.
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

## Тесты

```bash
pytest tests/
```

Единый прогон всей директории (не по модулям отдельно) — 620 passed + 3 xfail.

## Ограничения (честно)

- Синтез ответа на новый вопрос (вне 4 пресетов) локальной LLM занимает 24–480 секунд
  в зависимости от загрузки Ollama — не укладывается в целевые 3–5 с из задания.
  На демо для жюри ответы отдаются из прогретого кэша `data/processed/answer_cache.json`;
  живой путь работает и доступен через UI («пересчитать»), но не для интерактивного показа.
- Граф наполнен извлечением по ядру корпуса (~180 документов: Обзоры + Статьи +
  Доклады); журнальные подшивки и материалы конференций — в каталожном слое
  (CatalogEntry) и векторном индексе, но не в сущностном графе.
- 2 документа остаются с `geography=unknown` (методички без гео-привязки к практике).
- Свободный text2cypher не реализован (осознанно, инвариант задания) — роутер работает
  по фиксированным Cypher-шаблонам со слотами.

## Стек

Neo4j (граф + vector index) · neo4j-graphrag · Qwen3.5-35B-A3B через Ollama (int4)
(извлечение — локально, откат: Ollama/qwen3.5:9b) · Qwen3-Embedding-0.6B (эмбеддинги
RU/EN, Ollama) · Qwen3.5 через Ollama (синтез ответов; Claude API — опция через ANSWER_BACKEND) · FastAPI · Streamlit
