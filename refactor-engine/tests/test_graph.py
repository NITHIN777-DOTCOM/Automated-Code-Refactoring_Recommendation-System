from engine.models import ClassInfo, MethodInfo
from engine.suggester.graph import build_method_graph, graph_to_dict


def _method(name, class_name="Sample", fields_accessed=None, calls_made=None):
    return MethodInfo(
        name=name,
        class_name=class_name,
        start_line=1,
        end_line=2,
        fields_accessed=fields_accessed or [],
        calls_made=calls_made or [],
    )


def _class(name, methods):
    return ClassInfo(name=name, file_path="<test>", methods=methods)


def test_all_methods_share_one_field_forms_complete_graph():
    cls = _class(
        "Sample",
        [
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["x"]),
        ],
    )
    g = build_method_graph(cls)

    assert set(g.nodes()) == {"method_a", "method_b", "method_c"}
    assert g.number_of_edges() == 3  # complete graph on 3 nodes

    for u, v in [("method_a", "method_b"), ("method_a", "method_c"), ("method_b", "method_c")]:
        assert g.has_edge(u, v)
        assert g[u][v]["weight"] == 1
        assert g[u][v]["edge_type"] == {"shares_fields"}


def test_two_separate_clusters_have_no_cross_edges():
    cls = _class(
        "Sample",
        [
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["y"]),
            _method("method_d", fields_accessed=["y"]),
        ],
    )
    g = build_method_graph(cls)

    assert g.number_of_edges() == 2
    assert g.has_edge("method_a", "method_b")
    assert g.has_edge("method_c", "method_d")
    for cross in [("method_a", "method_c"), ("method_a", "method_d"),
                  ("method_b", "method_c"), ("method_b", "method_d")]:
        assert not g.has_edge(*cross)


def test_calls_only_relationship_has_zero_weight():
    cls = _class(
        "Sample",
        [
            _method("method_a", fields_accessed=["x"], calls_made=["method_b"]),
            _method("method_b", fields_accessed=["y"]),
        ],
    )
    g = build_method_graph(cls)

    assert g.number_of_edges() == 1
    assert g.has_edge("method_a", "method_b")
    assert g["method_a"]["method_b"]["weight"] == 0
    assert g["method_a"]["method_b"]["edge_type"] == {"calls"}


def test_shared_fields_and_calls_on_same_pair_stay_distinguishable():
    cls = _class(
        "Sample",
        [
            _method("method_a", fields_accessed=["x", "y"], calls_made=["method_b"]),
            _method("method_b", fields_accessed=["x", "y"]),
        ],
    )
    g = build_method_graph(cls)

    edge = g["method_a"]["method_b"]
    assert edge["weight"] == 2
    assert edge["edge_type"] == {"shares_fields", "calls"}
