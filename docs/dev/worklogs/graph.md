# Worklog: graph

Шаблон записи (≤ 8 строк). Эскалация — блок `⛔ ЭСКАЛАЦИЯ:` (симптом, что пробовал, гипотезы).

```markdown
## ГГГГ-ММ-ДД ЧЧ:ММ · <агент> · <ID задачи>
**Сделано:** …
**Решения:** …
**Проблемы:** …
**Открыто:** …
```

---

## 2026-07-03 19:45 · module-dev (Sonnet) · A-05
**Сделано:** `graph/lexical_loader.py` (+`graph/config.py`) — констрейнты Document.doc_id/
Chunk.chunk_id, загрузка Document (meta.jsonl) и Chunk (chunks-файл) батчами UNWIND/MERGE,
связь HAS_CHUNK, vector index `chunk_embedding_idx` (cosine, размерность из данных),
self_check (счётчики + `db.index.vector.queryNodes` top-1=self), CLI `--limit`.
**Решения:** имя индекса — константа CHUNK_VECTOR_INDEX_NAME (graph/config.py) для search;
чанк без embedding — WARN GRAPH-002 (зарегистрирован в ERRORS.md), не блокирует загрузку;
связь Document→Chunk через OPTIONAL MATCH+FOREACH — смоук по чанкам без соответствующих
Document не теряет узлы; .env читаю простым парсером (без python-dotenv).
**Проблемы:** chunks_embedded.jsonl на момент завершения — 224/9580 строк, растёт (A-04
🚧). Полную загрузку 9580 чанков не делал.
**Открыто:** смоук подтверждён на реальных данных: 177 Document, 100 Chunk (dim=1024,
из чунков A-04, уже готовых), 100 HAS_CHUNK, индекс создан, vector_self_match=true.
Полная загрузка — после A-04 (перезапустить `python -m ariadna.graph.lexical_loader`
без аргументов, MERGE идемпотентен, дозагрузит остальные ~9480 чанков).

## 2026-07-03 · module-tester (Sonnet) · A-05
**Сделано:** `tests/graph/test_lexical_loader.py` (10 тестов) + `tests/graph/conftest.py`
(driver-фикстура + автоочистка узлов по префиксу `test_a05_` до/после теста) +
6 фикстур в `tests/graph/fixtures/`. Против живого `ariadna_neo4j`, изолированно от
параллельной боевой загрузки. Все 10 прошли, `lint_precomments.py` — ок.
**Найденные баги:** нет багов кода lexical_loader/config — контракт и граничные
случаи (idempotent MERGE, GRAPH-002, orphan doc_id без Document/связи, пустой файл,
спецсимволы, detect_embedding_dimension на смеси null/вектор, --limit) ведут себя
как в паспорте/пре-комментариях.
**Проблемы:** инструкция «создать синтетический vector index для проверки, не трогая
боевой chunk_embedding_idx» технически невыполнима: Neo4j допускает не более ОДНОГО
vector index на пару (label, property) — `CREATE VECTOR INDEX <другое_имя> ... FOR
(c:Chunk) ON (c.embedding) IF NOT EXISTS` молча no-op, если vector index на
(:Chunk).embedding уже существует под любым именем (подтверждено вручную:
notification `IndexOrConstraintAlreadyExists`, ни имя, ни dim не влияют). Тест
переписан на проверку факта: `ensure_vector_index(driver, 4)` — безопасный no-op,
конфигурация боевого индекса (dim=1024) не портится.
**Открыто:** `self_check()` берёт образец чанка через `MATCH...LIMIT 1` без ORDER BY —
в теории при разной размерности векторов в базе (напр. тестовые чанки meньшей
размерности рядом с боевыми 1024-мерными) `vector_self_match`-подзапрос может упасть
на несовпадении размерности; в этой БД не воспроизвелось (LIMIT 1 стабильно берёт
самый старый чанк — боевой, 177 Document уже были загружены раньше тестов), но
порядок не гарантирован Cypher-семантикой — не блокирующая находка, не эскалирую.
