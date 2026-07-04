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

## 2026-07-03 · module-dev (Sonnet) · A-06
**Сделано:** `ontology/ariadna.ttl` (8 классов = EntityType, 6 object properties =
RelationType, rdfs:label RU/EN, domain/range по смыслу задания, TechSolution как
`rdfs:subClassOf ariadna:Process`); `ontology/synonyms.yaml` (45 канонических
терминов, покрыты все 4 эталонных запроса + базовые материалы/процессы отрасли);
`src/ariadna/graph/ontology.py` (`load_synonyms`, `canonical_name`,
`ttl_smoke_check`, валидация типа против `contracts.EntityType`).
**Решения:** described_in/validated_by/contradicts — домен намеренно не сужен
(rdfs:domain с несколькими классами в RDFS даёт пересечение, а не объединение —
избежал ложного ограничения); TTL не парсится rdflib (запрет по задаче) — только
текстовая смоук-проверка на маркеры `owl:Class`/`owl:ObjectProperty`/`@prefix`;
неизвестный `type` в synonyms.yaml — `OntologyValidationError` с кодом GRAPH-003
(зарегистрирован в ERRORS.md) до использования словаря потребителями.
**Проблемы:** нет.
**Открыто:** extraction (A-08) и graph/entity-loader (A-09) — потребители
`load_synonyms()`/`canonical_name()`, интерфейс зафиксирован пре-комментариями.

## 2026-07-03 20:20 · module-tester (Sonnet) · A-06
**Сделано:** `tests/graph/test_ontology.py` (34 теста): боевой synonyms.yaml
(≥40 записей, валидные type, без дублей канона, без коллизий обратного индекса),
покрытие терминов жюри (electrowinning/catholyte/desalination/matte/slag/PGM/
AMD/mine water + RU-формы), canonical_name (регистронезависимость, unknown→None,
канон резолвится в себя), валидация (неизвестный type → OntologyValidationError
GRAPH-003 через tmp-файл; битый YAML → yaml.YAMLError; пустой YAML; отсутствующий
canonical → KeyError), TTL-смоук (боевой файл проходит, missing/empty → False,
все 8 EntityType и 6 RelationType присутствуют текстово, TechSolution — реальный
блок объявления subClassOf Process), кеш `_lookup_index` (monkeypatch-счётчик
вызовов load_synonyms, раздельный кеш по пути). Прогон `tests/graph/` целиком:
44/44 прошли (34 новых + 10 A-05 lexical_loader против живого Neo4j). Lint — ок.
**Найденные баги:** нет багов кода `ontology.py`/`ariadna.ttl`/`synonyms.yaml` —
контракт, валидация и граничные случаи ведут себя как в пре-комментариях/паспорте.
**Проблемы:** нет.
**Открыто:** нет.

## 2026-07-04 · module-dev (Sonnet) · A-09
**Сделано:** `graph/entity_dedup.py` (чистая агрегация: slugify/make_node_id,
pick_name_en, majority_geography, constraint_node_id, `aggregate_from_rows` —
один проход по ExtractionResult, дедуп по (canon.lower(), type), MENTIONED_IN-
пары, tech-solution Process); `graph/entity_graph_writer.py` (констрейнты
Entity.id/NumericConstraint.id, UNWIND+MERGE узлов по EntityType, связей по
RelationType, chunk-level NumericConstraint+HAS_CONSTRAINT, MENTIONED_IN,
self_check со смоук-ассертами); `graph/entity_loader.py` (тонкий CLI:
--input/--meta-path/--limit). GRAPH-004 зарегистрирован в ERRORS.md.
**Решения:** node id = детерминированная функция (type, canon) — не зависит от
порядка агрегации, relation endpoints резолвятся тем же способом внутри чанка
(без второго прохода по файлу); в пул синонимов для pick_name_en добавляю также
исходное Entity.name (не только Entity.synonyms) — более надёжный выбор
name_en, риск минимален; attrs → плоские attr_* через `SET e += map` (не
перечисляю ключи явно); is_tech_solution — явное bool-свойство на ВСЕХ :Process
(не только true) для простых Cypher-фильтров.
**Проблемы:** relation.constraints пуст на 100% строк (0 из 29972 связей) во
всём extracted_haiku.jsonl — регресс не мой, у A-07/A-08 числа идут только через
chunk-level ExtractionResult.constraints; c_param/c_op/c_norm_value/c_norm_unit
корректно дефолтятся в "", "", None, "" — не баг загрузчика, наблюдение для A-10.
**Открыто:** полный прогон (3500 строк) — self_check: 23584 :Entity (Material
5758/Process 4475/Equipment 3077/Property 4539/Experiment 437/Publication
774/Expert 727/Facility 3797), связи 24538 (USES_MATERIAL 8158/
OPERATES_AT_CONDITION 6734/PRODUCES_OUTPUT 6707/DESCRIBED_IN 1904/
VALIDATED_BY 581/CONTRADICTS 454), MENTIONED_IN 65392, NumericConstraint 8073
(= HAS_CONSTRAINT 8073), tech_solution Process 3012, n_relation_warnings=0
(GRAPH-004 ни разу не сработал на боевых данных). Повторный полный прогон —
счётчики идентичны (идемпотентность подтверждена). `tests/graph/` — 44/44
(без изменений), `lint_precomments.py` — ок. Юнит-тесты entity_dedup/
entity_loader — за module-tester.

## 2026-07-04 · module-tester (Sonnet) · A-09
**Сделано:** `tests/graph/test_entity_dedup.py` (48, офлайн, без Neo4j: slugify/
make_node_id/pick_name_en/majority_geography/constraint_node_id, дедуп по
(canon.lower(), type), канонизация через ontology/synonyms.yaml, резолв концов
связей внутри чанка/GRAPH-004, is_tech_solution, агрегация связей),
`tests/graph/test_entity_graph_writer.py` (15, живой Neo4j, префикс test_a09_:
двойная метка+provenance, идемпотентность load_entities/load_relations/
load_mentioned_in, HAS_CONSTRAINT, constraints_json/c_param/c_op, self_check
на боевой базе), `tests/graph/test_entity_loader_cli.py` (4: офлайн --limit на
боевом extracted_haiku.jsonl + subprocess CLI на изолированной фикстуре
test_a09_cli_). `conftest.py` расширен автоочисткой test_a09_ (Entity по id
CONTAINS 'test-a09', Chunk по chunk_id, NumericConstraint по param — не только
через HAS_CONSTRAINT, см. находку ниже). `tests/graph/` 130/130 (44+111 новых),
`pytest tests/ -q` 447 passed/3 xfailed — без регрессий, `lint_precomments.py` — ок.
**Найденные баги:**
1. ⚠ КРИТИЧНО (наблюдалось на боевых данных): `entity_loader --limit N` НЕ
   идемпотентен против уже полностью загруженной боевой базы — `load_entities`/
   `load_relations` всегда SET (перезаписывают) ВСЕ свойства узла на MERGE, включая
   `is_tech_solution`/`n_mentions`/`geography`/`synonyms`, на основе ТОЛЬКО текущей
   (частичной) агрегации. Ручной прогон `--limit 5` на боевом extracted_haiku.jsonl
   уронил `n_tech_solution` 3012→3008 (Process-узлы из первых 5 чанков, чья
   tech-solution-связь лежит в чанках ЗА пределами --limit, откатились до false) и
   породил осиротевший узел :NumericConstraint (chunk_id одной из первых 5 строк не
   резолвится ни к одному :Chunk — 8073→8074 при неизменном HAS_CONSTRAINT=8073).
   Восстановлено полным прогоном без --limit (n_tech_solution вернулся к 3012) +
   ручным удалением осиротевшего узла (8074→8073). Итог сверен и совпадает с
   исходными цифрами A-09 dev. Смоук по заданию пункта C намеренно НЕ
   автоматизирован против боевого extracted_haiku.jsonl (риск повтора инцидента
   при каждом прогоне тестов) — заменён на офлайн-проверку `_iter_extraction_results`/
   `aggregate_from_rows` (безопасно, только чтение) + end-to-end CLI-тест на
   изолированной фикстуре test_a09_cli_.
2. Средняя серьёзность: `constraint_node_id` строит id из chunk_id+param+op+value,
   БЕЗ unit/value_max — два РАЗНЫХ по смыслу ограничения в одном чанке с одинаковым
   param/op/value, но разным unit (например «300 мг/л» и «300 кг»), схлопываются в
   ОДИН узел :NumericConstraint (MERGE), последняя запись молча перезаписывает
   первую (silent data loss). Тест: test_entity_dedup.py::
   test_constraint_node_id_collides_when_only_unit_differs +
   test_entity_graph_writer.py::test_numeric_constraint_unit_collision_last_write_wins.
3. Низкая серьёзность/находка: `aggregate_from_rows` резолвит source/target связи
   через `local_map`, ключуемый ТОЛЬКО по `name.lower()` (без типа) — если два
   разных Entity одного чанка совпадают по имени (регистронезависимо) с разными
   EntityType, второй молча перезаписывает первого в local_map; связь по этому
   имени всегда резолвится ко ВТОРОМУ. Тест: test_relation_endpoint_name_
   collision_within_chunk_resolves_to_last_entity_of_that_name.
4. Низкая серьёзность/находка: `RelationAgg.constraints` заполняется только при
   ПЕРВОМ появлении ключа (source_id, target_id, type) — если та же связь
   повторяется в другом чанке с ДРУГИМИ NumericConstraint, они молча теряются
   (n_evidence/confidence продолжают агрегироваться верно). На боевых данных не
   проявляется (Relation.constraints пуст на 100% строк extracted_haiku.jsonl —
   уже отмечено dev в записи выше). Тест: test_relation_constraints_kept_only_
   from_first_occurrence.
**Проблемы:** «CLI-смоук --limit 5 на боевом входе» из постановки задачи
технически выполнен вручную (см. находку №1) и оказался небезопасным для
повторного/автоматического прогона — решение задокументировано в тестах и
здесь, не эскалирую (не требует правки контракта/архитектуры, но реализацию
`load_entities`/`load_relations` стоит пересмотреть перед след. частичным
прогоном на боевой базе — например, полными пересчётом is_tech_solution по
всей базе, а не только по строкам текущего вызова).
**Открыто:** боевые данные проверены и приведены в исходное состояние (23584
:Entity, 24538 связей, 65392 MENTIONED_IN, 8073 NumericConstraint = 8073
HAS_CONSTRAINT, 3012 tech_solution — совпадает с записью dev выше). Решение
находки №1 (безопасный частичный/повторный запуск против боевой базы) — за
оркестратором/reviewer.

## 2026-07-04 · module-dev fixer (Sonnet) · A-09
**Сделано:** исправлены 4 бага tester'а: №1 — writer теперь ON CREATE (всё) /
ON MATCH (монотонно: n_mentions/confidence/n_evidence=max, is_tech_solution=OR),
NumericConstraint создаётся только при найденном :Chunk (MATCH до MERGE — сироты
невозможны); №2 — unit+value_max в constraint_node_id; №3 — резолв концов связи
через кандидатов по имени (несколько — первый по появлению + WARN GRAPH-005,
новый код в ERRORS.md, связь не теряется); №4 — union constraints с дедупом.
**Решения:** :NumericConstraint пересозданы (id сменились из-за №2): 8073→8102
(+29 узлов, спасённых от unit-коллизий) = HAS_CONSTRAINT, сирот 0. Полный
перезапуск: Entity 23584, tech_solution 3012, связи/MENTIONED_IN без изменений.
Смоук `--limit 5` поверх полной базы — счётчики идентичны, деградации нет.
pytest tests/ — 452 passed/3 xfailed; tests/graph/ 116; lint — ок.
**Проблемы:** нет. **Открыто:** ре-тест fixer-правок — за module-tester.
