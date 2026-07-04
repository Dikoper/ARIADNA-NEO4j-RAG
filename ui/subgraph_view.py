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
_NODE_LABEL_MAX_CHARS = 32  # длиннее — подпись «наезжает» на соседние узлы (A-23)

# Русские подписи типов сущностей — для легенды и hover-подсказок (A-23).
# Ключи = contracts.EntityType.value; НЕ пользовательские константы-строки
# (dict-литерал не сканируется тестом жаргона), но тексты всё равно чистые.
ENTITY_TYPE_LABELS_RU: dict[str, str] = {
    "Material": "Материал",
    "Process": "Процесс",
    "Equipment": "Оборудование",
    "Property": "Свойство",
    "Experiment": "Эксперимент",
    "Publication": "Публикация",
    "Expert": "Эксперт",
    "Facility": "Установка",
}

# Русские подписи типов связей для hover-подсказок рёбер (A-23) — подписи
# НА самих рёбрах убраны (главный источник визуальных наслоений при 40-60
# узлах), тип связи виден при наведении курсора; исключение — противоречия.
_RELATION_LABELS_RU: dict[str, str] = {
    "USES_MATERIAL": "использует материал",
    "OPERATES_AT_CONDITION": "работает при условии",
    "PRODUCES_OUTPUT": "даёт результат",
    "DESCRIBED_IN": "описано в",
    "VALIDATED_BY": "подтверждено",
    "CONTRADICTS": "противоречит",
}
CONTRADICTS_EDGE_LABEL = "⚠"  # единственная подпись, остающаяся на ребре


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


# ─── _filter_nodes ──────────────────────────────────────────────────────
# Назначение: единая точка отбора узлов для рендера и легенды (A-23) —
#   фильтр по типам сущностей (None/пусто = все), затем срез до max_nodes
#   (fetch_subgraph уже отсортировал по значимости — режем хвост, не голову).
# Входные связи: subgraph["nodes"], allowed_types — значения EntityType.value
# Выходные данные: list[dict] — узлы к отображению
# Уровень: ✅ реализовано (A-23)
def _filter_nodes(subgraph: dict, *, max_nodes: int, allowed_types: set[str] | None) -> list[dict]:
    raw_nodes = [n for n in (subgraph.get("nodes") or []) if n.get("id")]
    if allowed_types:
        raw_nodes = [n for n in raw_nodes if n.get("type") in allowed_types]
    return raw_nodes[:max_nodes]


# ─── _short_label ───────────────────────────────────────────────────────
# Назначение: подпись узла без наслоений — обрезка до _NODE_LABEL_MAX_CHARS
#   с многоточием; полное имя остаётся в hover-подсказке узла.
# Уровень: ✅ реализовано (A-23)
def _short_label(name: str) -> str:
    return name if len(name) <= _NODE_LABEL_MAX_CHARS else name[: _NODE_LABEL_MAX_CHARS - 1] + "…"


# ─── build_agraph_elements ─────────────────────────────────────────────
# Назначение: конвертирует словарь fetch_subgraph() в списки Node/Edge для
#   streamlit_agraph.agraph(); фильтры A-23: allowed_types — типы сущностей
#   (None/пусто = все), min_edge_confidence — порог уверенности рёбер
#   (противоречия НЕ отсекаются порогом: У-3 обязан оставаться видимым);
#   рёбра — только между оставшимися узлами (иначе agraph падает на висячих).
#   Подписи рёбер убраны с линий (наслоения) — тип связи в hover; на ребре
#   остаётся только маркер противоречия. Физика: forceAtlas2Based с
#   avoidOverlap — узлы расталкиваются и не слипаются в ком.
# Входные связи: subgraph — {"nodes": [...], "edges": [...]} (см. докстринг модуля)
# Выходные данные: (nodes: list[Node], edges: list[Edge], config: Config)
# Уровень: ✅ реализовано (A-13, фильтры и читаемость — A-23)
def build_agraph_elements(
    subgraph: dict,
    *,
    max_nodes: int = 60,
    allowed_types: set[str] | None = None,
    min_edge_confidence: float = 0.0,
) -> tuple[list[Node], list[Edge], Config]:
    raw_nodes = _filter_nodes(subgraph, max_nodes=max_nodes, allowed_types=allowed_types)
    kept_ids = {n["id"] for n in raw_nodes}

    nodes = []
    for n in raw_nodes:
        name = n.get("name") or n["id"]
        type_ru = ENTITY_TYPE_LABELS_RU.get(n.get("type", ""), "тип не указан")
        nodes.append(Node(
            id=n["id"],
            label=_short_label(name),
            title=f"{name} · {type_ru}"
                  + (" · техническое решение" if n.get("is_tech_solution") else ""),
            color=node_color(n.get("type", "")),
            size=_HUB_NODE_SIZE if n.get("is_tech_solution") else _DEFAULT_NODE_SIZE,
            shape="diamond" if n.get("is_tech_solution") else "dot",
            font={"size": 13, "color": "#1f1f1c"},
        ))

    edges = []
    for e in (subgraph.get("edges") or []):
        if e.get("source") not in kept_ids or e.get("target") not in kept_ids:
            continue
        is_contradicts = bool(e.get("is_contradicts"))
        confidence = float(e.get("confidence", 0))
        if not is_contradicts and confidence < min_edge_confidence:
            continue
        # стенд отдаёт тип UPPER_SNAKE, но нормализуем регистр сами — подпись
        # не должна зависеть от источника словаря (онтология в нижнем регистре)
        relation_ru = _RELATION_LABELS_RU.get(e.get("type", "").upper(), e.get("type", ""))
        edges.append(Edge(
            source=e["source"],
            target=e["target"],
            color=edge_color(e),
            label=CONTRADICTS_EDGE_LABEL if is_contradicts else "",
            title=f"{relation_ru} · уверенность {confidence:.2f}"
                  + (" — ПРОТИВОРЕЧИЕ" if is_contradicts else ""),
        ))

    config = Config(
        width="100%",
        height=560,
        directed=True,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#f0efec",
        collapsible=False,
        edges={"smooth": {"type": "continuous"}, "arrows": {"to": {"scaleFactor": 0.6}}},
    )
    # Настройка раскладки против наслоений (A-23): солвер forceAtlas2Based
    # расталкивает узлы равномернее barnesHut на 20-60 узлах; avoidOverlap>0
    # запрещает перекрытие тел узлов. Мутируем готовый dict Config.physics —
    # передать solver kwargs-ом нельзя: лишний top-level ключ уехал бы в
    # options vis-network (Config.__dict__ сериализуется целиком).
    config.physics["solver"] = "forceAtlas2Based"
    config.physics["forceAtlas2Based"] = {
        "gravitationalConstant": -60,
        "centralGravity": 0.005,
        "springLength": 130,
        "springConstant": 0.08,
        "avoidOverlap": 0.8,
    }
    config.physics["stabilization"] = {"enabled": True, "fit": True, "iterations": 300}
    return nodes, edges, config


# ─── legend_items ───────────────────────────────────────────────────────
# Назначение: легенда цветов для отображаемого подграфа (A-23) — пары
#   (подпись RU, hex-цвет) ТОЛЬКО для типов, реально присутствующих среди
#   отобранных узлов (та же фильтрация, что у build_agraph_elements), в
#   фиксированном порядке EntityType; неизвестные типы не выдумываются.
# Входные связи: subgraph, те же фильтры max_nodes/allowed_types
# Выходные данные: list[tuple[str, str]] — [(«Материал», "#2a78d6"), ...]
# Уровень: ✅ реализовано (A-23)
def legend_items(
    subgraph: dict,
    *,
    max_nodes: int = 60,
    allowed_types: set[str] | None = None,
) -> list[tuple[str, str]]:
    present = {n.get("type") for n in _filter_nodes(subgraph, max_nodes=max_nodes, allowed_types=allowed_types)}
    return [
        (label_ru, _ENTITY_TYPE_COLORS[type_value])
        for type_value, label_ru in ENTITY_TYPE_LABELS_RU.items()
        if type_value in present
    ]


# Иконки вкладки «Эксперты и организации» (A-24) — только два типа онтологии,
# отвечающие требованию «показ связанных экспертов и лабораторий».
_PEOPLE_ORG_ICONS: dict[str, str] = {"Expert": "👤", "Facility": "🏭"}


# ─── list_experts_and_facilities ────────────────────────────────────────
# Назначение: строки вкладки «Эксперты и организации» (A-24) — узлы типов
#   Expert/Facility из ПОЛНОГО подграфа ответа (без фильтров сайдбара: панель
#   отвечает на «кто связан с темой», а не «что видно на схеме сейчас»);
#   формат «👤 Имя» / «🏭 Название», порядок стенда (значимость) сохранён.
# Входные связи: subgraph — словарь fetch_subgraph (см. докстринг модуля)
# Выходные данные: list[str]; пустой список — вызывающий код рисует честную
#   подпись «не найдены», не выдумывая людей
# Уровень: ✅ реализовано (A-24)
def list_experts_and_facilities(subgraph: dict) -> list[str]:
    rows = []
    for n in (subgraph.get("nodes") or []):
        icon = _PEOPLE_ORG_ICONS.get(n.get("type", ""))
        if icon and (n.get("name") or n.get("id")):
            rows.append(f"{icon} {n.get('name') or n['id']}")
    return rows


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
