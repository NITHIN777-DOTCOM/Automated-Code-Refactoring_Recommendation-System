"""Community detection over a class's field-sharing graph, to propose
Extract Class boundaries.

Only FIELD-SHARING edges are considered. Call edges are deliberately ignored:
one method calling another is ordinary internal delegation and says nothing
about whether the two belong in the same extracted class. Methods that
operate on the same state are the signal we want; methods that merely
delegate are not.

Clusters are found with weighted greedy modularity maximization
(networkx's greedy_modularity_communities, weight = number of shared fields),
so a pair sharing three fields pulls together harder than a pair sharing one.

NO ARTIFICIAL SPLITS: if the whole class turns out to be a single
field-sharing community, that is a legitimate finding -- a God Class with no
clean extraction boundary -- and it is reported as such rather than forced
apart. See ExtractionAnalysis.has_clean_separation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

FIELD_SHARING_EDGE = "shares_fields"

# Constructors are excluded by default for two reasons:
#   1. A constructor assigns essentially every field the class owns, so it is
#      adjacent to every other method -- a universal hub that collapses real
#      structure. On sample_repo's ReportManager, including __init__ merges
#      three distinct responsibility groups (title/author, footer, logo) into
#      one community and drops the class from 4 clusters to 3.
#   2. A constructor cannot be "extracted" anyway; it stays with the origin
#      class or is rewritten across the split, so it is not a candidate.
CONSTRUCTOR_NAMES = frozenset({"__init__", "__new__"})


@dataclass
class ExtractionAnalysis:
    """Full result of clustering. `clusters` alone is what
    find_extraction_clusters() returns; the rest is context for the
    suggester step and for explaining a result to a user."""

    clusters: list[set[str]]
    has_clean_separation: bool
    note: str
    excluded_methods: set[str] = field(default_factory=set)
    unclustered_methods: set[str] = field(default_factory=set)


def field_sharing_subgraph(
    g: nx.Graph, exclude_constructor: bool = True
) -> tuple[nx.Graph, set[str]]:
    """Project `g` down to just its field-sharing edges.

    All nodes are retained (minus excluded constructors) so that methods
    sharing no state show up as isolated nodes rather than silently
    disappearing -- they are reported as `unclustered_methods`.
    """
    excluded = (
        {n for n in g.nodes() if n in CONSTRUCTOR_NAMES} if exclude_constructor else set()
    )

    sub = nx.Graph()
    sub.add_nodes_from(n for n in g.nodes() if n not in excluded)

    for u, v, data in g.edges(data=True):
        if u in excluded or v in excluded:
            continue
        if FIELD_SHARING_EDGE in data.get("edge_type", set()):
            sub.add_edge(u, v, weight=data.get("weight", 1))

    return sub, excluded


def analyze_extraction_clusters(
    g: nx.Graph, min_cluster_size: int = 2, exclude_constructor: bool = True
) -> ExtractionAnalysis:
    sub, excluded = field_sharing_subgraph(g, exclude_constructor=exclude_constructor)

    if sub.number_of_nodes() == 0:
        return ExtractionAnalysis(
            clusters=[],
            has_clean_separation=False,
            note="Class has no analysable methods.",
            excluded_methods=excluded,
        )

    communities = [set(c) for c in greedy_modularity_communities(sub, weight="weight")]

    # Largest first, then alphabetical, so output is deterministic.
    clusters = sorted(
        (c for c in communities if len(c) >= min_cluster_size),
        key=lambda c: (-len(c), sorted(c)),
    )
    clustered = {name for c in clusters for name in c}
    unclustered = set(sub.nodes()) - clustered

    if not clusters:
        has_clean_separation = False
        note = (
            f"No group of {min_cluster_size}+ methods shares fields; "
            "no extraction candidate found."
        )
    elif len(clusters) == 1:
        has_clean_separation = False
        if clusters[0] == set(sub.nodes()):
            note = (
                "All methods form a single field-sharing community: God Class with "
                "no clear extraction boundary. Not split artificially."
            )
        else:
            note = (
                "Only one qualifying cluster; the remaining methods share no state "
                "with it. No clean multi-way separation."
            )
    else:
        has_clean_separation = True
        note = f"Found {len(clusters)} distinct field-sharing communities."

    return ExtractionAnalysis(
        clusters=clusters,
        has_clean_separation=has_clean_separation,
        note=note,
        excluded_methods=excluded,
        unclustered_methods=unclustered,
    )


def find_extraction_clusters(
    g: nx.Graph, min_cluster_size: int = 2, exclude_constructor: bool = True
) -> list[set[str]]:
    """Method-name clusters that are candidates for extraction together.

    Use analyze_extraction_clusters() when you also need to know WHY a result
    looks the way it does -- in particular whether a single returned cluster
    means "one cohesive group" or "God Class with no clean boundary".
    """
    return analyze_extraction_clusters(
        g, min_cluster_size=min_cluster_size, exclude_constructor=exclude_constructor
    ).clusters
