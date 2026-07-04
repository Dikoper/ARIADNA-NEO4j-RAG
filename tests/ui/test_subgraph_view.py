"""Тесты конвертации словаря fetch_subgraph в элементы streamlit-agraph (A-13):
окраска contradicts красным (У-3), обрезка по max_nodes, фолбэк-список."""
from __future__ import annotations

from ui.subgraph_view import (
    CONTRADICTS_EDGE_COLOR,
    build_agraph_elements,
    edge_color,
    format_flat_node_list,
    node_color,
)


def _sample_subgraph():
    return {
        "nodes": [
            {"id": "n1", "name": "Никель", "type": "Material", "is_tech_solution": False},
            {"id": "n2", "name": "Электроэкстракция", "type": "Process", "is_tech_solution": True},
            {"id": "n3", "name": "Обратный осмос", "type": "Process", "is_tech_solution": True},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "uses_material", "confidence": 0.8, "is_contradicts": False},
            {"source": "n2", "target": "n3", "type": "contradicts", "confidence": 0.6, "is_contradicts": True},
        ],
    }


def test_edge_color_contradicts_is_red_status_color():
    edge = {"is_contradicts": True}
    assert edge_color(edge) == CONTRADICTS_EDGE_COLOR


def test_edge_color_non_contradicts_is_neutral():
    edge = {"is_contradicts": False}
    assert edge_color(edge) != CONTRADICTS_EDGE_COLOR


def test_node_color_known_type_is_stable():
    assert node_color("Material") == node_color("Material")
    assert node_color("Material") != node_color("Process")


def test_node_color_unknown_type_falls_back_without_crashing():
    assert node_color("НеизвестныйТип") == node_color("")


def test_build_agraph_elements_counts_and_ids():
    subgraph = _sample_subgraph()
    nodes, edges, config = build_agraph_elements(subgraph)
    assert {n.id for n in nodes} == {"n1", "n2", "n3"}
    assert len(edges) == 2
    assert config.height


def test_build_agraph_elements_contradicts_edge_is_red():
    subgraph = _sample_subgraph()
    _, edges, _ = build_agraph_elements(subgraph)
    contradicts_edges = [e for e in edges if "n3" in (e.to, e.source)]
    assert any(e.color == CONTRADICTS_EDGE_COLOR for e in contradicts_edges)


def test_build_agraph_elements_non_contradicts_edge_not_red():
    subgraph = _sample_subgraph()
    _, edges, _ = build_agraph_elements(subgraph)
    non_contradicts = [e for e in edges if e.color != CONTRADICTS_EDGE_COLOR]
    assert len(non_contradicts) == 1


def test_build_agraph_elements_respects_max_nodes():
    subgraph = _sample_subgraph()
    nodes, edges, _ = build_agraph_elements(subgraph, max_nodes=1)
    assert len(nodes) == 1
    # Единственный узел не участвует ни в одном ребре двух других — рёбра пусты.
    assert edges == []


def test_build_agraph_elements_hub_nodes_get_bigger_shape():
    subgraph = _sample_subgraph()
    nodes, _, _ = build_agraph_elements(subgraph)
    hub = next(n for n in nodes if n.id == "n2")
    non_hub = next(n for n in nodes if n.id == "n1")
    assert hub.size > non_hub.size
    assert hub.shape == "diamond"
    assert non_hub.shape == "dot"


def test_build_agraph_elements_empty_input():
    nodes, edges, config = build_agraph_elements({"nodes": [], "edges": []})
    assert nodes == []
    assert edges == []
    assert config is not None


def test_format_flat_node_list_empty():
    assert "не найдены" in format_flat_node_list([])


def test_format_flat_node_list_lists_all_ids():
    text = format_flat_node_list(["n1", "n2"])
    assert "n1" in text
    assert "n2" in text
