"""Models a class's methods as a graph for refactoring analysis.

Nodes are method names. Two kinds of edges can connect a pair of methods,
and they are kept distinguishable on the SAME edge rather than merged into
one score, since they represent different kinds of coupling:

- field-sharing: methods that read/write the same self.attr. `weight` is the
  number of shared fields -- "these methods work on the same data."
- calls: one method calls another. Carries no weight of its own (a call
  either happened or it didn't); tracked via the `edge_type` set so a pair
  that both shares fields AND calls each other doesn't lose either signal.
"""

from __future__ import annotations

import networkx as nx

from engine.models import ClassInfo


def build_method_graph(cls: ClassInfo) -> nx.Graph:
    g = nx.Graph()
    g.add_nodes_from(m.name for m in cls.methods)

    for i in range(len(cls.methods)):
        for j in range(i + 1, len(cls.methods)):
            m1, m2 = cls.methods[i], cls.methods[j]
            shared_fields = set(m1.fields_accessed) & set(m2.fields_accessed)
            if shared_fields:
                g.add_edge(m1.name, m2.name, weight=len(shared_fields), edge_type={"shares_fields"})

    method_names = {m.name for m in cls.methods}
    for m in cls.methods:
        for called in m.calls_made:
            if called not in method_names or called == m.name:
                continue
            if g.has_edge(m.name, called):
                g[m.name][called].setdefault("edge_type", set()).add("calls")
                g[m.name][called].setdefault("weight", 0)
            else:
                g.add_edge(m.name, called, weight=0, edge_type={"calls"})

    return g


def graph_to_dict(g: nx.Graph) -> dict:
    return {
        "nodes": list(g.nodes()),
        "edges": [
            {
                "source": u,
                "target": v,
                "weight": data.get("weight", 0),
                "edge_type": sorted(data.get("edge_type", set())),
            }
            for u, v, data in g.edges(data=True)
        ],
    }
