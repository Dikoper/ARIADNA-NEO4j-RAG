"""Подграф ответа (A-13): словарь `graph.templates.fetch_subgraph` -> элементы
streamlit-agraph (У-3 — рёбра `contradicts` красным).

Вход: `{"nodes": [{"id","name","type","is_tech_solution"}], "edges": [{"source",
"target","type","confidence","is_contradicts"}]}` (интерфейс задачи A-13, НЕ
pydantic-контракт — согласован в постановке задачи). Выход: списки `Node`/`Edge`
streamlit-agraph + `Config` для виджета, либо (при недоступности fetch_subgraph)
человекочитаемая строка — плоский список имён узлов, см. `format_flat_node_list`.

Зависимости: `streamlit_agraph` (Node/Edge/Config — чистые dataclass-обёртки,
не тянут рантайм Streamlit при импорте, можно тестировать офлайн).
Цветовая схема — validated палитра dataviz-скилла (references/palette.md):
8 категориальных цветов на 8 типов сущностей (contracts.EntityType, фиксированный
порядок — идентичность, не ранг), статусный «critical»-красный (#d03b3b,
никогда не темизируется) — ТОЛЬКО для рёбер contradicts, отдельно от
категориального красного узлов Publication (#e34948) — линия и точка не
путаются визуально даже при близких оттенках.
"""
from __future__ import annotations

from streamlit_agraph import Config, Edge, Node

# Категориальная палитра (light-mode, references/palette.md) — фиксированный
# порядок слотов 1..8 = порядок EntityType в contracts.py (идентичность типа,
# не ранг сущности).
_ENTITY_TYPE_COLORS: dict[str, str] = {
    "Material": "#2a78d6",     # slot 1 blue
    "Process": "#1baf7a",      # slot 2 aqua
    "Equipment": "#eda100",    # slot 3 yellow
    "Property": "#008300",     # slot 4 green
    "Experiment": "#4a3aa7",   # slot 5 violet
    "Publication": "#e34948",  # slot 6 red (категориальный — отличим от статусного)
    "Expert": "#e87ba4",       # slot 7 magenta
    "Facility": "#eb6834",     # slot 8 orange
}
_DEFAULT_NODE_COLOR = "#8a8a86"  # неизвестный/незаданный тип — нейтральный серый

# Статусный красный (critical, references/palette.md) — ТОЛЬКО для рёбер
# contradicts (У-3); фиксирован, не участвует в категориальной ротации.
CONTRADICTS_EDGE_COLOR = "#d03b3b"
_DEFAULT_EDGE_COLOR = "#b5b4ad"  # нейтральное ребро — не должно спорить с contradicts

_HUB_NODE_SIZE = 34   # is_tech_solution=True — визуальный хаб графа (паспорт graph)
_DEFAULT_NODE_SIZE = 22


# Назначение: цвет узла по типу сущности — фиксированный категориальный слот,
#   неизвестный тип получает нейтральный серый (не выдумываем цвет).
# Входные связи: type — строка типа сущности (contracts.EntityType.value)
# Выходные данные: hex-строка цвета
# Уровень: ✅ реализовано (A-13)
def node_color(entity_type: str) -> str:
    return _ENTITY_TYPE_COLORS.get(entity_type, _DEFAULT_NODE_COLOR)


# Назначение: цвет ребра — красный (У-3), если ребро помечено is_contradicts,
#   иначе нейтральный серый; единственное место, решающее эту раскраску.
# Входные связи: edge — словарь с ключом "is_contradicts"
# Выходные данные: hex-строка цвета
# Уровень: ✅ реализовано (A-13)
def edge_color(edge: dict) -> str:
    return CONTRADICTS_EDGE_COLOR if edge.get("is_contradicts") else _DEFAULT_EDGE_COLOR


# ─── build_agraph_elements ─────────────────────────────────────────────
# Назначение: конвертирует словарь fetch_subgraph() в списки Node/Edge для
#   streamlit_agraph.agraph(); режет узлы до max_nodes (первые по порядку —
#   fetch_subgraph уже отвечает за релевантность/сортировку), рёбра — только
#   между оставшимися узлами (иначе agraph падает на висячих рёбрах).
# Входные связи: subgraph — {"nodes": [...], "edges": [...]} (см. докстринг модуля)
# Выходные данные: (nodes: list[Node], edges: list[Edge], config: Config)
# Уровень: ✅ реализовано (A-13)
def build_agraph_elements(subgraph: dict, *, max_nodes: int = 60) -> tuple[list[Node], list[Edge], Config]:
    raw_nodes = (subgraph.get("nodes") or [])[:max_nodes]
    kept_ids = {n.get("id") for n in raw_nodes if n.get("id")}

    nodes = [
        Node(
            id=n["id"],
            label=(n.get("name") or n["id"])[:40],
            title=f"{n.get('name') or n['id']} ({n.get('type') or '?'})",
            color=node_color(n.get("type", "")),
            size=_HUB_NODE_SIZE if n.get("is_tech_solution") else _DEFAULT_NODE_SIZE,
            shape="diamond" if n.get("is_tech_solution") else "dot",
        )
        for n in raw_nodes
        if n.get("id")
    ]

    edges = [
        Edge(
            source=e["source"],
            target=e["target"],
            color=edge_color(e),
            label=e.get("type", ""),
            title=f"{e.get('type', '')} (уверенность {e.get('confidence', 0):.2f})"
                  + (" — ПРОТИВОРЕЧИЕ" if e.get("is_contradicts") else ""),
        )
        for e in (subgraph.get("edges") or [])
        if e.get("source") in kept_ids and e.get("target") in kept_ids
    ]

    config = Config(
        width="100%",
        height=520,
        directed=True,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#f0efec",
        collapsible=False,
    )
    return nodes, edges, config


# ─── format_flat_node_list ──────────────────────────────────────────────
# Назначение: фолбэк-отображение, когда fetch_subgraph недоступен (ImportError/
#   ошибка стенда) — плоский список ID узлов ответа честно, без выдумывания
#   имён/типов (данных для этого нет).
# Входные связи: subgraph_node_ids — Answer.subgraph_node_ids
# Выходные данные: str для st.text/markdown ("узлы не найдены" при пустом списке)
# Уровень: ✅ реализовано (A-13)
def format_flat_node_list(subgraph_node_ids: list[str]) -> str:
    if not subgraph_node_ids:
        return "Узлы подграфа не найдены."
    return "\n".join(f"- {node_id}" for node_id in subgraph_node_ids)
