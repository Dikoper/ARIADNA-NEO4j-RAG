"""Программный доступ к семантическому фасаду онтологии: словарь синонимов RU/EN
и смоук-проверка OWL-файла (ontology/).

Вход: `ontology/synonyms.yaml` (canonical/type/synonyms), `ontology/ariadna.ttl`
(текстовая смоук-проверка, без парсинга — rdflib не тянем, см. docs/dev/modules/
graph.md, A-06). Выход: dict синонимов + `canonical_name()` — поиск канонического
RU-имени по любому синониму без учёта регистра.
Зависимости: stdlib + pyyaml, `ariadna.contracts.EntityType` (валидация типов).
Инвариант: не пишет в Neo4j (это делает graph/loader) — чистая функция словаря.
Потребители: extraction (A-08, подсказки канонизации в промпт LLM), graph (A-09,
дедупликация узлов по синонимам).
Паспорт: docs/dev/modules/graph.md.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ariadna.contracts import EntityType

# Неизвестный EntityType в synonyms.yaml — docs/dev/ERRORS.md.
GRAPH_UNKNOWN_ENTITY_TYPE = "GRAPH-003"

DEFAULT_SYNONYMS_PATH = Path("ontology/synonyms.yaml")
DEFAULT_TTL_PATH = Path("ontology/ariadna.ttl")

_KNOWN_ENTITY_TYPES = {member.value for member in EntityType}


# ─── OntologyValidationError ───────────────────────────────────────────
# Назначение: ошибка валидации словаря синонимов (неизвестный EntityType,
#   GRAPH-003) — отдельный класс, чтобы потребители могли ловить её точечно.
# Входные связи: сообщение с кодом ошибки и контекстом воспроизведения
# Выходные данные: исключение
# Уровень: ✅ реализовано (A-06, worklogs/graph.md#2026-07-03)
class OntologyValidationError(ValueError):
    pass


# ─── load_synonyms ──────────────────────────────────────────────────────
# Назначение: читает ontology/synonyms.yaml, валидирует поле `type` каждой
#   записи против contracts.EntityType и возвращает словарь синонимов.
# Входные связи: путь к YAML (по умолчанию ontology/synonyms.yaml)
# Выходные данные: dict[str, dict] — canonical (RU) -> {"type": EntityType,
#   "synonyms": list[str]}; порядок — как в файле (Python dict, py3.7+)
# Уровень: ✅ реализовано (A-06, worklogs/graph.md#2026-07-03)
def load_synonyms(path: Path = DEFAULT_SYNONYMS_PATH) -> dict[str, dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms = raw.get("terms", [])
    result: dict[str, dict] = {}
    for entry in terms:
        canonical = entry["canonical"]
        type_value = entry["type"]
        if type_value not in _KNOWN_ENTITY_TYPES:
            raise OntologyValidationError(
                f"{GRAPH_UNKNOWN_ENTITY_TYPE}: неизвестный EntityType '{type_value}' "
                f"у термина '{canonical}' в {path} (допустимые: {sorted(_KNOWN_ENTITY_TYPES)})"
            )
        result[canonical] = {
            "type": EntityType(type_value),
            "synonyms": list(entry.get("synonyms", [])),
        }
    return result


# Назначение: строит обратный индекс «синоним/канон в нижнем регистре -> каноническое
#   имя» для быстрого регистронезависимого поиска в canonical_name(); кешируется по
#   пути файла — словарь синонимов не меняется в рамках одного процесса.
# Уровень: ✅ реализовано (A-06, worklogs/graph.md#2026-07-03)
@functools.lru_cache(maxsize=8)
def _lookup_index(path_str: str) -> dict[str, str]:
    synonyms = load_synonyms(Path(path_str))
    index: dict[str, str] = {}
    for canonical, record in synonyms.items():
        index[canonical.strip().lower()] = canonical
        for alt in record["synonyms"]:
            index[alt.strip().lower()] = canonical
    return index


# ─── canonical_name ──────────────────────────────────────────────────────
# Назначение: находит каноническое (RU) имя термина по любому синониму/переводу/
#   аббревиатуре без учёта регистра, например «electrowinning» -> «электроэкстракция».
# Входные связи: contracts.EntityType (косвенно, через load_synonyms); ontology/synonyms.yaml
# Выходные данные: str (каноническое имя) или None, если термин не найден в словаре
# Уровень: ✅ реализовано (A-06, worklogs/graph.md#2026-07-03)
def canonical_name(term: str, path: Path = DEFAULT_SYNONYMS_PATH) -> str | None:
    return _lookup_index(str(path)).get(term.strip().lower())


# ─── ttl_smoke_check ─────────────────────────────────────────────────────
# Назначение: смоук-проверка ontology/ariadna.ttl — файл существует, непустой,
#   текстово читается как UTF-8 и содержит базовые Turtle-маркеры (owl:Class,
#   owl:ObjectProperty). Глубокая валидация синтаксиса TTL — вне задачи A-06
#   (rdflib не тянем в зависимости).
# Входные связи: путь к TTL (по умолчанию ontology/ariadna.ttl)
# Выходные данные: bool — True, если смоук-проверка пройдена
# Уровень: ✅ реализовано (A-06, worklogs/graph.md#2026-07-03)
def ttl_smoke_check(path: Path = DEFAULT_TTL_PATH) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return False
    required_markers = ("owl:Class", "owl:ObjectProperty", "@prefix")
    return all(marker in text for marker in required_markers)


if __name__ == "__main__":
    data = load_synonyms()
    print(f"synonyms.yaml: {len(data)} канонических терминов")
    print(f"ariadna.ttl смоук-проверка: {ttl_smoke_check()}")
    print(f"electrowinning -> {canonical_name('electrowinning')}")
