"""Тесты A-06: `ariadna.graph.ontology` — словарь синонимов RU/EN и смоук-проверка
TTL. Независимая проверка контракта (не переписывает реализацию, module-dev A-06).

Проверяется: боевой ontology/synonyms.yaml (≥40 записей, валидные type, без дублей
канона/коллизий обратного индекса), покрытие терминов 4 эталонных запросов жюри,
canonical_name (регистронезависимость, неизвестный термин, канон резолвится в себя),
валидация битого/неизвестного type в YAML (OntologyValidationError, GRAPH-003),
смоук-проверка ariadna.ttl (8 классов + 6 свойств contracts.py текстово присутствуют,
TechSolution subClassOf Process), кеширование обратного индекса canonical_name.

Офлайн, без Neo4j — только файлы ontology/*.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ariadna.contracts import EntityType, RelationType
from ariadna.graph import ontology as onto

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LIVE_SYNONYMS = onto.DEFAULT_SYNONYMS_PATH
LIVE_TTL = onto.DEFAULT_TTL_PATH


# ══════════════════════════ 1. Боевой словарь synonyms.yaml ══════════════════════════

# ─── test_live_synonyms_loads_and_has_minimum_size ──────────────────────
# Назначение: боевой словарь загружается без исключений и содержит ≥ 40 записей
#   (паспорт заявляет 45).
# Уровень: ✅ реализовано (module-tester A-06)
def test_live_synonyms_loads_and_has_minimum_size():
    data = onto.load_synonyms(LIVE_SYNONYMS)
    assert len(data) >= 40


# ─── test_live_synonyms_all_types_are_valid_entity_types ────────────────
# Назначение: поле type каждой записи — валидный член contracts.EntityType.
# Уровень: ✅ реализовано (module-tester A-06)
def test_live_synonyms_all_types_are_valid_entity_types():
    data = onto.load_synonyms(LIVE_SYNONYMS)
    for canonical, record in data.items():
        assert isinstance(record["type"], EntityType), canonical


# ─── test_live_synonyms_no_duplicate_canonical_names ────────────────────
# Назначение: в сыром YAML нет двух записей с одинаковым canonical (иначе вторая
#   молча перезаписала бы первую в dict load_synonyms — потеря термина незаметна).
# Уровень: ✅ реализовано (module-tester A-06)
def test_live_synonyms_no_duplicate_canonical_names():
    raw = yaml.safe_load(LIVE_SYNONYMS.read_text(encoding="utf-8"))
    canonicals = [entry["canonical"] for entry in raw["terms"]]
    assert len(canonicals) == len(set(canonicals))


# ─── test_live_synonyms_no_reverse_index_collisions ──────────────────────
# Назначение: ни один синоним (в нижнем регистре) не встречается у двух разных
#   канонических терминов — иначе обратный индекс canonical_name() коллизионно
#   резолвит термин в непредсказуемый канон (порядок словаря решает победителя).
# Уровень: ✅ реализовано (module-tester A-06)
def test_live_synonyms_no_reverse_index_collisions():
    data = onto.load_synonyms(LIVE_SYNONYMS)
    owner: dict[str, str] = {}
    collisions = []
    for canonical, record in data.items():
        keys = [canonical.strip().lower()] + [s.strip().lower() for s in record["synonyms"]]
        for key in keys:
            if key in owner and owner[key] != canonical:
                collisions.append((key, owner[key], canonical))
            else:
                owner[key] = canonical
    assert collisions == [], f"коллизии обратного индекса: {collisions}"


# ══════════════════════════ 2. Покрытие терминов жюри ══════════════════════════

JURY_TERMS = [
    "electrowinning", "электроэкстракция",
    "catholyte", "католит",
    "desalination", "обессоливание",
    "matte", "штейн",
    "slag", "шлак",
    "PGM", "платиноиды",
    "AMD", "mine water", "шахтные воды",
]


# ─── test_jury_terms_resolve_to_nonempty_canonical ───────────────────────
# Назначение: все ключевые термины 4 эталонных запросов жюри (RU/EN формы)
#   резолвятся canonical_name() в непустое каноническое имя — иначе провал
#   на приёмке жюри по конкретному запросу.
# Уровень: ✅ реализовано (module-tester A-06)
@pytest.mark.parametrize("term", JURY_TERMS)
def test_jury_terms_resolve_to_nonempty_canonical(term):
    result = onto.canonical_name(term, LIVE_SYNONYMS)
    assert result, f"термин жюри '{term}' не резолвится"


# ══════════════════════════ 3. canonical_name: поведение поиска ══════════════════════════

# ─── test_canonical_name_case_insensitive ────────────────────────────────
# Назначение: поиск не зависит от регистра термина.
# Уровень: ✅ реализовано (module-tester A-06)
def test_canonical_name_case_insensitive():
    assert onto.canonical_name("ELECTROWINNING", LIVE_SYNONYMS) == onto.canonical_name(
        "electrowinning", LIVE_SYNONYMS
    )
    assert onto.canonical_name("Catholyte", LIVE_SYNONYMS) == onto.canonical_name(
        "catholyte", LIVE_SYNONYMS
    )


# ─── test_canonical_name_unknown_term_returns_none ───────────────────────
# Назначение: термин, отсутствующий в словаре, — None, а не исключение/KeyError.
# Уровень: ✅ реализовано (module-tester A-06)
def test_canonical_name_unknown_term_returns_none():
    assert onto.canonical_name("совершенно неизвестный термин xyz123", LIVE_SYNONYMS) is None


# ─── test_canonical_names_resolve_to_themselves ──────────────────────────
# Назначение: каждое каноническое имя само является допустимым входом поиска
#   (обратный индекс включает canonical.strip().lower()).
# Уровень: ✅ реализовано (module-tester A-06)
def test_canonical_names_resolve_to_themselves():
    data = onto.load_synonyms(LIVE_SYNONYMS)
    for canonical in data:
        assert onto.canonical_name(canonical, LIVE_SYNONYMS) == canonical


# ══════════════════════════ 4. Валидация словаря ══════════════════════════

# ─── test_unknown_entity_type_raises_ontology_validation_error ──────────
# Назначение: type вне contracts.EntityType — OntologyValidationError с кодом
#   GRAPH-003 в сообщении (docs/dev/ERRORS.md), а не тихая порча данных.
# Уровень: ✅ реализовано (module-tester A-06)
def test_unknown_entity_type_raises_ontology_validation_error(tmp_path):
    bad_yaml = tmp_path / "bad_type.yaml"
    bad_yaml.write_text(
        "terms:\n"
        "  - canonical: тестовый термин\n"
        "    type: NotAnEntityType\n"
        "    synonyms: [foo]\n",
        encoding="utf-8",
    )
    with pytest.raises(onto.OntologyValidationError) as exc_info:
        onto.load_synonyms(bad_yaml)
    assert "GRAPH-003" in str(exc_info.value)
    assert "тестовый термин" in str(exc_info.value)


# ─── test_malformed_yaml_raises_meaningful_error ─────────────────────────
# Назначение: синтаксически битый YAML даёт содержательную ошибку парсера
#   (yaml.YAMLError), а не производный от него необработанный трейсбек другого
#   типа (напр. AttributeError/KeyError на None).
# Уровень: ✅ реализовано (module-tester A-06)
def test_malformed_yaml_raises_meaningful_error(tmp_path):
    bad_yaml = tmp_path / "malformed.yaml"
    bad_yaml.write_text(
        "terms:\n  - canonical: сломанный\n    type: [Material\n",
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError):
        onto.load_synonyms(bad_yaml)


# ─── test_empty_yaml_loads_as_empty_dict ─────────────────────────────────
# Назначение: полностью пустой YAML-файл (`raw or {}` в реализации) — не падает,
#   возвращает пустой словарь. Граничный случай.
# Уровень: ✅ реализовано (module-tester A-06)
def test_empty_yaml_loads_as_empty_dict(tmp_path):
    empty_yaml = tmp_path / "empty.yaml"
    empty_yaml.write_text("", encoding="utf-8")
    assert onto.load_synonyms(empty_yaml) == {}


# ─── test_missing_canonical_key_raises_key_error ─────────────────────────
# Назначение: запись без обязательного поля canonical — понятная KeyError,
#   а не молчаливая порча индекса. Граничный случай.
# Уровень: ✅ реализовано (module-tester A-06)
def test_missing_canonical_key_raises_key_error(tmp_path):
    bad_yaml = tmp_path / "missing_canonical.yaml"
    bad_yaml.write_text(
        "terms:\n  - type: Material\n    synonyms: [foo]\n",
        encoding="utf-8",
    )
    with pytest.raises(KeyError):
        onto.load_synonyms(bad_yaml)


# ══════════════════════════ 5. TTL: смоук-проверка и полнота ══════════════════════════

# ─── test_ttl_smoke_check_passes_on_live_file ────────────────────────────
# Назначение: боевой ariadna.ttl проходит смоук-проверку (существует, непустой,
#   UTF-8, содержит owl:Class/owl:ObjectProperty/@prefix).
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_smoke_check_passes_on_live_file():
    assert onto.ttl_smoke_check(LIVE_TTL) is True


# ─── test_ttl_smoke_check_false_on_missing_file ──────────────────────────
# Назначение: несуществующий путь — False, а не исключение (контракт функции —
#   bool). Граничный случай.
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_smoke_check_false_on_missing_file(tmp_path):
    assert onto.ttl_smoke_check(tmp_path / "nonexistent.ttl") is False


# ─── test_ttl_smoke_check_false_on_empty_file ────────────────────────────
# Назначение: пустой (или из пробелов) файл — False. Граничный случай.
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_smoke_check_false_on_empty_file(tmp_path):
    empty_ttl = tmp_path / "empty.ttl"
    empty_ttl.write_text("   \n\n", encoding="utf-8")
    assert onto.ttl_smoke_check(empty_ttl) is False


# ─── test_ttl_contains_all_entity_type_class_names ───────────────────────
# Назначение: все 8 имён классов = EntityType (contracts.py) встречаются
#   в тексте TTL как ariadna:<Value> — байт-текстовая проверка синхронизации
#   TTL с 🔒-контрактом, без rdflib (запрет по задаче A-06).
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_contains_all_entity_type_class_names():
    text = LIVE_TTL.read_text(encoding="utf-8")
    for member in EntityType:
        assert f"ariadna:{member.value}" in text, member.value


# ─── test_ttl_contains_all_relation_type_property_names ─────────────────
# Назначение: все 6 имён object properties = RelationType встречаются в тексте
#   TTL как ariadna:<value> (значения RelationType — уже snake_case, как в TTL).
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_contains_all_relation_type_property_names():
    text = LIVE_TTL.read_text(encoding="utf-8")
    for member in RelationType:
        assert f"ariadna:{member.value}" in text, member.value


# ─── test_ttl_techsolution_is_subclass_of_process ────────────────────────
# Назначение: TechSolution оформлен как rdfs:subClassOf ariadna:Process
#   (не отдельный EntityType — паспорт модуля и ARCHITECTURE.md фиксируют это
#   как инвариант хаба технических решений).
# Уровень: ✅ реализовано (module-tester A-06)
def test_ttl_techsolution_is_subclass_of_process():
    text = LIVE_TTL.read_text(encoding="utf-8")
    assert "ariadna:TechSolution" in text
    # Ищем именно блок ОБЪЯВЛЕНИЯ класса ("ariadna:TechSolution a owl:Class"),
    # а не упоминание имени в rdfs:comment другого класса выше по файлу.
    idx = text.index("ariadna:TechSolution a owl:Class")
    block = text[idx: idx + 300]
    assert "rdfs:subClassOf ariadna:Process" in block


# ══════════════════════════ 6. Кеш обратного индекса ══════════════════════════

# ─── test_lookup_index_is_cached_across_calls ────────────────────────────
# Назначение: повторные вызовы canonical_name с тем же путём не перечитывают
#   YAML-файл — _lookup_index кеширован через functools.lru_cache(path_str).
#   Проверяем через monkeypatch на load_synonyms: второй вызов не должен
#   инкрементировать счётчик обращений к загрузчику.
# Уровень: ✅ реализовано (module-tester A-06)
def test_lookup_index_is_cached_across_calls(monkeypatch):
    onto._lookup_index.cache_clear()
    calls = {"n": 0}
    original_load = onto.load_synonyms

    def counting_load(path):
        calls["n"] += 1
        return original_load(path)

    monkeypatch.setattr(onto, "load_synonyms", counting_load)

    onto.canonical_name("electrowinning", LIVE_SYNONYMS)
    onto.canonical_name("catholyte", LIVE_SYNONYMS)
    onto.canonical_name("desalination", LIVE_SYNONYMS)

    assert calls["n"] == 1, "load_synonyms должен вызываться один раз на путь (кеш lru_cache)"
    onto._lookup_index.cache_clear()


# ─── test_lookup_index_cache_keyed_by_path_string ────────────────────────
# Назначение: кеш ключуется строкой пути — разные пути (даже к одинаковому
#   по содержимому файлу) не делят один закешированный индекс.
# Уровень: ✅ реализовано (module-tester A-06)
def test_lookup_index_cache_keyed_by_path_string(tmp_path):
    onto._lookup_index.cache_clear()
    custom = tmp_path / "custom_synonyms.yaml"
    custom.write_text(
        "terms:\n"
        "  - canonical: уникальный термин\n"
        "    type: Material\n"
        "    synonyms: [unique_term_xyz]\n",
        encoding="utf-8",
    )
    assert onto.canonical_name("unique_term_xyz", custom) == "уникальный термин"
    # Боевой словарь не содержит unique_term_xyz — раздельный кеш подтверждён.
    assert onto.canonical_name("unique_term_xyz", LIVE_SYNONYMS) is None
    onto._lookup_index.cache_clear()
