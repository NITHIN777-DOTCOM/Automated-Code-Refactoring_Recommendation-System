from engine.models import ClassInfo
from engine.suggester.cluster import find_extraction_clusters
from engine.suggester.graph import build_method_graph
from engine.suggester.suggest import generate_suggestions


def suggest_refactoring(
    cls: ClassInfo, min_cluster_size: int = 2, exclude_constructor: bool = True
) -> list[dict]:
    graph = build_method_graph(cls)
    clusters = find_extraction_clusters(
        graph, min_cluster_size=min_cluster_size, exclude_constructor=exclude_constructor
    )
    return generate_suggestions(cls, clusters)
