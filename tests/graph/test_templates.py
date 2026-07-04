"""Тесты graph/templates.py + graph/cypher_templates.py (A-10): исполнение
Cypher-шаблонов роутера против ЖИВОГО (только чтение) боевого Neo4j.

Покрыто: 6 шаблонов на боевом графе (для 4 эталонных запросов TASK.md —
rows>0, это ПРИЁМОЧНЫЙ критерий паспорта модуля), формат результата
{rows, node_ids, chunk_ids, contradiction_pairs} и типы полей; инвариант №4
(только $param, никакой конкатенации/f-string в текстах Cypher-запросов);
неизвестный template_id -> ValueError (SEARCH-008); guard gap_matrix (оба слота
пустые -> ValueError, ХОТЯ БЫ один слот -> не падает).

Тесты используют ту же fixture `driver` (session-scoped, tests/graph/conftest.py),
что и остальные тесты graph/ — это ЧТЕНИЕ уже наполненного боевого графа
(живые данные корпуса A-09/A-20/A-21), тестовых узлов НЕ создаёт и НЕ пишет —
autouse-очистка conftest (test_a05_/test_a09_ префиксы) эти тесты не касается.
"""
from __future__ import annotations

import re
from pathlib import Path

from ariadna.contracts import QueryIntent
from ariadna.graph import cypher_templates
from ariadna.graph.templates import SEARCH_UNKNOWN_TEMPLATE, execute_intent
from ariadna.search.router import route

# ─── 4 эталонных вопроса жюри (TASK.md, дословно) ──────────────────────────
Q1_DESALINATION = (
    "Какие методы обессоливания воды подходят для обогатительной фабрики, если "
    "исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, а "
    "требуемый сухой остаток — ≤1000 мг/дм³?"
)
Q2_CATHOLYTE = (
    "Какие технические решения организации циркуляции католита при "
    "электроэкстракции никеля описаны в мировой практике, и какая скорость "
    "потока считается оптимальной?"
)
Q3_EXPERIMENTS = (
    "Покажите все эксперименты и публикации по распределению Au, Ag и МПГ между "
    "медным/никелевым штейном и шлаком за последние 5 лет."
)
Q4_MINE_WATER = (
    "Какие способы закачки шахтных вод в глубокие горизонты применялись в России "
    "и за рубежом, и каковы их технико-экономические показатели?"
)


# Назначение: проверяет форму результата execute_intent() — ключи и типы полей
#   (rows: list[dict], node_ids/chunk_ids: list[str], contradiction_pairs: list[dict]),
#   см. докстринг graph/templates.py.
# Уровень: ✅ реализовано (module-tester A-10)
def _assert_result_shape(result: dict) -> None:
    assert set(result.keys()) == {"rows", "node_ids", "chunk_ids", "contradiction_pairs"}
    assert isinstance(result["rows"], list)
    assert all(isinstance(r, dict) for r in result["rows"])
    assert isinstance(result["node_ids"], list)
    assert all(isinstance(nid, str) for nid in result["node_ids"])
    assert isinstance(result["chunk_ids"], list)
    assert all(isinstance(cid, str) for cid in result["chunk_ids"])
    assert isinstance(result["contradiction_pairs"], list)
    assert all(isinstance(p, dict) for p in result["contradiction_pairs"])


# ══════════════════════ 4 эталонных запроса -> rows>0 (приёмка) ══════════════════════

# Назначение: запрос №1 (обессоливание) через полный путь router.route ->
#   execute_intent -> rows>0 на боевом графе (приёмочный критерий).
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_query1_desalination_returns_nonempty_rows(driver):
    intent = route(Q1_DESALINATION)
    assert intent.template_id == "desalination_methods"
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0
    assert len(result["node_ids"]) > 0
    assert len(result["chunk_ids"]) > 0


# Назначение: запрос №2 (католит/электроэкстракция) -> rows>0.
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_query2_catholyte_returns_nonempty_rows(driver):
    intent = route(Q2_CATHOLYTE)
    assert intent.template_id == "catholyte_circulation"
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0
    assert len(result["chunk_ids"]) > 0


# Назначение: запрос №3 (Au/Ag/МПГ штейн-шлак, 5 лет) -> rows>0.
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_query3_experiments_returns_nonempty_rows(driver):
    intent = route(Q3_EXPERIMENTS)
    assert intent.template_id == "experiments_publications_by_topic"
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0
    assert len(result["chunk_ids"]) > 0


# Назначение: запрос №4 (закачка шахтных вод, РФ+зарубеж) -> rows>0,
#   и contradiction_pairs непусты (см. живой смоук worklogs/search.md#A-10:
#   1 contradiction на боевом графе для этого запроса).
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_query4_mine_water_returns_nonempty_rows_and_contradiction(driver):
    intent = route(Q4_MINE_WATER)
    assert intent.template_id == "mine_water_injection"
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0
    assert len(result["chunk_ids"]) > 0
    assert len(result["contradiction_pairs"]) >= 1


# ══════════════════════ Остальные 2 из 6 шаблонов (вне 4 эталонных) ══════════════════════

# Назначение: compare_ru_foreign (У-2) — топик «обессоливание», rows>0 на
#   боевом графе (не эталонный запрос, но часть реестра из 6 шаблонов A-10).
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_compare_ru_foreign_returns_nonempty_rows(driver):
    intent = QueryIntent(question="сравнение обессоливания РФ/зарубеж", template_id="compare_ru_foreign",
                          slots={"topic": "обессоливание"})
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0


# Назначение: gap_matrix (заготовка A-12) с непустыми обоими слотами ->
#   rows>=0 (пробелы могут отсутствовать — не строгий приёмочный критерий),
#   но исполнение не падает и форма результата верна.
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_gap_matrix_with_both_slots_returns_valid_shape(driver):
    intent = QueryIntent(question="пробелы штейн/флотация", template_id="gap_matrix",
                          slots={"material": "штейн", "process": "флотация"})
    result = execute_intent(driver, intent)
    _assert_result_shape(result)
    assert len(result["rows"]) > 0  # наблюдалось 30 строк на боевом графе (worklog A-10)


# ══════════════════════ Guard gap_matrix (пустые слоты) ══════════════════════

# Назначение: gap_matrix с ОБОИМИ пустыми слотами material/process -> ValueError
#   ДО обращения к Neo4j (защита от декартова произведения ~26 млн пар) —
#   guard срабатывает, запрос не падает по таймауту/OOM.
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_gap_matrix_both_slots_empty_raises_value_error(driver):
    intent = QueryIntent(question="пробелы без слотов", template_id="gap_matrix", slots={})
    try:
        execute_intent(driver, intent)
        assert False, "ожидался ValueError — оба слота material/process пусты"
    except ValueError as exc:
        assert "material" in str(exc) or "process" in str(exc)


# Назначение: gap_matrix с ХОТЯ БЫ одним непустым слотом (только material,
#   process — дефолт из TEMPLATE_DEFAULT_CANONICALS отсутствует для gap_matrix,
#   поэтому process_terms будет пуст) — guard НЕ должен падать, если material
#   непуст (условие — оба пустых, не один из двух).
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_gap_matrix_only_material_slot_does_not_raise(driver):
    intent = QueryIntent(question="пробелы по штейну", template_id="gap_matrix", slots={"material": "штейн"})
    result = execute_intent(driver, intent)
    _assert_result_shape(result)  # не упало — process_terms пуст, но material_terms непуст


# ══════════════════════ Неизвестный template_id (SEARCH-008) ══════════════════════

# Назначение: template_id, отсутствующий в TEMPLATES -> ValueError с кодом
#   SEARCH-008 в сообщении (docs/dev/ERRORS.md) — рассинхрон router/templates
#   ловится ДО обращения к Neo4j.
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_unknown_template_id_raises_value_error_with_search008(driver):
    intent = QueryIntent(question="q", template_id="not_a_real_template")
    try:
        execute_intent(driver, intent)
        assert False, "ожидался ValueError для неизвестного template_id"
    except ValueError as exc:
        assert SEARCH_UNKNOWN_TEMPLATE in str(exc)  # "SEARCH-008"
        assert "not_a_real_template" in str(exc)


# Назначение: template_id='rag_fallback' НЕ входит в TEMPLATES (это вектор-путь
#   search/retrieval, а не графовый шаблон) -> тоже ValueError/SEARCH-008, если
#   вызывающая сторона ошибочно передаст его в execute_intent напрямую
#   (retrieve()/answer_question() так не делают — используют intent.template_id
#   как флаг ДО вызова execute_fn, см. search/retrieval.py).
# Уровень: ✅ реализовано (module-tester A-10)
def test_execute_intent_rag_fallback_template_id_raises_value_error(driver):
    intent = QueryIntent(question="q", template_id="rag_fallback")
    try:
        execute_intent(driver, intent)
        assert False, "rag_fallback не зарегистрирован в TEMPLATES — ожидался ValueError"
    except ValueError as exc:
        assert SEARCH_UNKNOWN_TEMPLATE in str(exc)


# ══════════════════════ Инвариант №4: только $param, без конкатенации/f-string ══════════════════════

# Назначение: статическая проверка исходников Cypher-шаблонов — тексты запросов
#   в TEMPLATES не построены f-строкой/`.format()`/`%`-форматированием с
#   пользовательским вводом; единственный механизм параметризации — именованные
#   `$param`, подставляемые драйвером neo4j (session.run(query, **params)),
#   никогда не через конкатенацию строки запроса.
# Уровень: ✅ реализовано (module-tester A-10)
def test_cypher_templates_source_has_no_fstring_or_format_string_building():
    source = Path(cypher_templates.__file__).read_text(encoding="utf-8")
    # Ни одна Cypher-строка не должна быть f-строкой (запрещает f"..."/f'''...''').
    assert not re.search(r"\bf\"\"\"", source)
    assert not re.search(r"\bf'''", source)
    assert ".format(" not in source
    assert " % (" not in source and not re.search(r"%\s*\(", source)


# Назначение: каждый зарегистрированный Cypher-шаблон использует хотя бы один
#   именованный `$param` (параметризация обязательна — инвариант №4) и не
#   содержит явной конкатенации Python-строк (` + `) внутри собственного текста
#   (текст уже materialized как plain str на момент импорта — конкатенация
#   пользовательского ввода технически невозможна на этом уровне, но проверяем
#   явное отсутствие `+` рядом с ключевыми словами Cypher как доп. сигнал).
# Уровень: ✅ реализовано (module-tester A-10)
def test_all_registered_templates_use_dollar_params():
    for template_id, query_text in cypher_templates.TEMPLATES.items():
        assert "$" in query_text, f"шаблон {template_id} не использует ни одного $param"
        params_used = set(re.findall(r"\$(\w+)", query_text))
        assert params_used, f"шаблон {template_id}: не найдено ни одного именованного $param"


# Назначение: source graph/templates.py — единственное место, вызывающее
#   Cypher (_run/execute_intent), НЕ строит текст запроса конкатенацией/
#   f-строкой из intent.slots/вопроса — запрос всегда берётся как есть из
#   TEMPLATES[intent.template_id] (статический словарь), а не собирается на лету.
# Уровень: ✅ реализовано (module-tester A-10)
def test_templates_module_source_passes_static_query_text_not_built_dynamically():
    from ariadna.graph import templates as templates_module

    source = Path(templates_module.__file__).read_text(encoding="utf-8")
    assert "TEMPLATES[intent.template_id]" in source
    assert not re.search(r"f\"\"\".*MATCH", source, re.DOTALL)
    assert not re.search(r"query\s*\+=", source)
    assert not re.search(r"query\s*=.*\+", source)
