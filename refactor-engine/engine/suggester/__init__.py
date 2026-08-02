from engine.models import ClassInfo
from engine.suggester.cluster import find_extraction_clusters
from engine.suggester.graph import build_method_graph
from engine.suggester.suggest import (
    generate_extract_method_suggestions,
    generate_move_method_suggestions,
    generate_suggestions,
)


def suggest_refactoring(
    cls: ClassInfo, min_cluster_size: int = 2, exclude_constructor: bool = True
) -> list[dict]:
    """Extract Class suggestions, for smells rooted in low cohesion."""
    graph = build_method_graph(cls)
    clusters = find_extraction_clusters(
        graph, min_cluster_size=min_cluster_size, exclude_constructor=exclude_constructor
    )
    return generate_suggestions(cls, clusters)


def suggest_method_extractions(cls: ClassInfo) -> list[dict]:
    """Extract Method suggestions, for Long Method. Returns an empty list when
    no method decomposes cleanly -- the caller decides what to say about that."""
    return generate_extract_method_suggestions(cls)


def suggest_method_moves(cls: ClassInfo, all_classes: list[ClassInfo]) -> list[dict]:
    """Move Method suggestions, for Feature Envy. Needs the full class list:
    attributing a method's outbound calls to a destination class is only
    possible relative to the other classes in the scan."""
    return generate_move_method_suggestions(cls, all_classes)
