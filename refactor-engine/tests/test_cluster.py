from engine.models import ClassInfo, MethodInfo
from engine.suggester.cluster import (
    analyze_extraction_clusters,
    find_extraction_clusters,
)
from engine.suggester.graph import build_method_graph


def _method(name, fields_accessed=None, calls_made=None):
    return MethodInfo(
        name=name,
        class_name="Sample",
        start_line=1,
        end_line=2,
        fields_accessed=fields_accessed or [],
        calls_made=calls_made or [],
    )


def _graph(methods):
    return build_method_graph(ClassInfo(name="Sample", file_path="<test>", methods=methods))


def test_two_separate_clusters_split_correctly():
    """The core capability the whole suggester depends on: two groups with
    no shared state MUST come back as two clusters."""
    g = _graph(
        [
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["y"]),
            _method("method_d", fields_accessed=["y"]),
        ]
    )
    clusters = find_extraction_clusters(g)

    assert len(clusters) == 2
    assert {frozenset(c) for c in clusters} == {
        frozenset({"method_a", "method_b"}),
        frozenset({"method_c", "method_d"}),
    }

    analysis = analyze_extraction_clusters(g)
    assert analysis.has_clean_separation is True
    assert analysis.unclustered_methods == set()


def test_cohesive_class_returns_single_cluster_and_flags_no_separation():
    """A genuinely cohesive class must NOT be split artificially."""
    g = _graph(
        [
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["x"]),
        ]
    )
    analysis = analyze_extraction_clusters(g)

    assert len(analysis.clusters) == 1
    assert analysis.clusters[0] == {"method_a", "method_b", "method_c"}
    assert analysis.has_clean_separation is False
    assert "no clear extraction boundary" in analysis.note


def test_three_natural_clusters():
    g = _graph(
        [
            _method("a1", fields_accessed=["x"]),
            _method("a2", fields_accessed=["x"]),
            _method("b1", fields_accessed=["y"]),
            _method("b2", fields_accessed=["y"]),
            _method("c1", fields_accessed=["z"]),
            _method("c2", fields_accessed=["z"]),
        ]
    )
    clusters = find_extraction_clusters(g)

    assert len(clusters) == 3
    assert {frozenset(c) for c in clusters} == {
        frozenset({"a1", "a2"}),
        frozenset({"b1", "b2"}),
        frozenset({"c1", "c2"}),
    }


def test_calls_only_edges_are_ignored():
    """Delegation is not a reason to extract two methods together, so a
    calls-only relationship must not create a cluster."""
    g = _graph(
        [
            _method("caller", fields_accessed=["x"], calls_made=["callee"]),
            _method("callee", fields_accessed=["y"]),
        ]
    )
    # The call edge exists in the graph...
    assert g.has_edge("caller", "callee")
    assert g["caller"]["callee"]["edge_type"] == {"calls"}

    # ...but must not produce an extraction cluster.
    analysis = analyze_extraction_clusters(g)
    assert analysis.clusters == []
    assert analysis.has_clean_separation is False
    assert analysis.unclustered_methods == {"caller", "callee"}


def test_min_cluster_size_filters_small_groups():
    g = _graph(
        [
            _method("big_a", fields_accessed=["x"]),
            _method("big_b", fields_accessed=["x"]),
            _method("big_c", fields_accessed=["x"]),
            _method("pair_a", fields_accessed=["y"]),
            _method("pair_b", fields_accessed=["y"]),
        ]
    )
    assert len(find_extraction_clusters(g, min_cluster_size=2)) == 2

    clusters = find_extraction_clusters(g, min_cluster_size=3)
    assert len(clusters) == 1
    assert clusters[0] == {"big_a", "big_b", "big_c"}

    analysis = analyze_extraction_clusters(g, min_cluster_size=3)
    assert analysis.has_clean_separation is False
    assert analysis.unclustered_methods == {"pair_a", "pair_b"}


def test_constructor_is_excluded_by_default():
    """__init__ touches every field, so it is adjacent to every method and
    distorts the boundaries -- it gets absorbed into whichever cluster the
    modularity pass happens to favour, polluting that suggestion with a
    method that cannot actually be extracted."""
    methods = [
        _method("__init__", fields_accessed=["x", "y"]),
        _method("method_a", fields_accessed=["x"]),
        _method("method_b", fields_accessed=["x"]),
        _method("method_c", fields_accessed=["y"]),
        _method("method_d", fields_accessed=["y"]),
    ]
    g = _graph(methods)

    analysis = analyze_extraction_clusters(g)
    assert analysis.excluded_methods == {"__init__"}
    assert len(analysis.clusters) == 2
    assert all("__init__" not in c for c in analysis.clusters)
    assert {frozenset(c) for c in analysis.clusters} == {
        frozenset({"method_a", "method_b"}),
        frozenset({"method_c", "method_d"}),
    }

    # Kept in, __init__ lands inside one of the clusters and contaminates it.
    kept = analyze_extraction_clusters(g, exclude_constructor=False)
    assert kept.excluded_methods == set()
    assert any("__init__" in c for c in kept.clusters)
