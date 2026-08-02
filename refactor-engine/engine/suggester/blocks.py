"""Statement-level cohesion analysis, powering Extract Method suggestions.

cluster.py groups a CLASS's methods by shared field access. This module does
the analogous thing one level down: it groups a METHOD's top-level
STATEMENTS by shared variable flow, so a long method can be split along the
logical steps it is actually made of.

SEGMENTATION RULE: a statement continues the current block if it reads any
name the block has already written -- a genuine def-use dependency. When a
statement reads nothing the block produced, the data-flow chain is broken and
a new block starts. That discontinuity is precisely where a long method stops
doing one thing and starts doing the next: a bare initializer like
`subtotal = 0.0` reads nothing from the validation code above it, so it opens
a new block.

Note that connectivity is deliberately keyed on READS, not writes. A later
statement re-assigning a variable an earlier block wrote is killing that
value, not consuming it, so it is not a dependency.

Blocks shorter than MIN_BLOCK_STATEMENTS are merged forward into the block
that follows them -- a two-line initializer sequence is the start of the next
step, not a step of its own -- and a method that yields only one block is
reported as having no useful split, since "extract the whole method" is not
a suggestion.

KNOWN LIMITATION -- MIN_BLOCK_STATEMENTS absorption:
An undersized segment (below MIN_BLOCK_STATEMENTS) has to be attached to a
neighbour, and when neither direction shows a genuine data link the merge
falls back to a default (forward). That default is a guess, not a detected
dependency -- see the next limitation for how a guess like this can attach
an initializer to a block it doesn't conceptually belong with.

KNOWN LIMITATION -- transitive dependency chaining:
The segmentation rule is single-hop: a statement continues the block if it
reads a name the block *has already written*, checked pairwise against each
new statement. There is no check for whether the statement is still doing
the same THING as the block's earlier statements, only whether it happens to
consume a value the block produced. Because "consumes the previous output"
chains transitively -- A feeds B, B feeds C, so A/B/C end up in one block
even though A and C share no direct relationship -- unrelated concerns that
happen to be linked by a short intermediate variable get glued together.

Concrete case (see the "shipping cost" step in the process_order() example
this module was built against): a shipping cap gets merged into receipt
construction because of a two-hop chain through an intermediate variable:

    shipping_cost = 0.0 if subtotal > 100 else shipping_cost   # writes shipping_cost
    total_with_shipping = total + shipping_cost                # reads shipping_cost -> chains
    receipt = {..., "total": total_with_shipping}               # reads total_with_shipping -> chains
    self.processed.append(receipt)                              # reads receipt -> chains
    return receipt                                              # reads receipt -> chains

Each link is locally valid -- every statement really does read the previous
one's output -- but "shipping cost" and "build the receipt" are different
concerns to a human reader, and the chain has no way to notice that: it only
asks "did this line touch something the last one touched," never "is this
still the same step." The MIN_BLOCK_STATEMENTS default-forward-merge above
made the case worse here (the 2-line shipping initializer had no measurable
link in either direction and fell forward onto this already-merged block by
default), but the chain itself -- shipping_cost -> total_with_shipping ->
receipt -- forms in pass 1, before any absorption logic runs. Distinct
limitation, distinct fix: absorption needs a better tie-break; chaining needs
a real notion of "concern" (e.g. capping the number of hops a dependency can
be inherited across, or weighting a link by how many statements have
elapsed) that this module does not attempt.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

MIN_BLOCK_STATEMENTS = 3


def _target_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _internally_bound_names(node: ast.AST) -> set[str]:
    """Names a statement binds for its own use: loop targets, `with ... as x`,
    `except ... as e`, comprehension targets.

    A naive walk sees these as reads -- `for item in items: use(item)` loads
    `item` -- but the value originates inside the statement, not from the
    surrounding code. Counting them as reads would make consecutive loops over
    unrelated data look data-dependent on each other and collapse every block
    into one.
    """
    bound: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.For, ast.AsyncFor)):
            bound |= _target_names(child.target)
        elif isinstance(child, ast.comprehension):
            bound |= _target_names(child.target)
        elif isinstance(child, ast.withitem) and child.optional_vars is not None:
            bound |= _target_names(child.optional_vars)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
    return bound


def is_local(name: str) -> bool:
    """True for plain local variables, False for `self.attr`-style names.

    Instance attributes are tracked for dependency purposes but excluded from
    output/return analysis: a helper that mutates self.total doesn't need to
    return it.
    """
    return "." not in name


@dataclass
class StatementInfo:
    node: ast.stmt
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    write_counts: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    returned: set[str] = field(default_factory=set)
    has_raise: bool = False
    has_loop: bool = False


def analyze_statement(node: ast.stmt, self_name: str | None) -> StatementInfo:
    info = StatementInfo(node=node)
    bound = _internally_bound_names(node)

    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            if self_name and child.value.id == self_name:
                qualified = f"{self_name}.{child.attr}"
                if isinstance(child.ctx, ast.Store):
                    info.writes.add(qualified)
                    info.write_counts[qualified] = info.write_counts.get(qualified, 0) + 1
                else:
                    info.reads.add(qualified)
        elif isinstance(child, ast.Name):
            if isinstance(child.ctx, ast.Store):
                info.writes.add(child.id)
                info.write_counts[child.id] = info.write_counts.get(child.id, 0) + 1
            else:
                info.reads.add(child.id)
        elif isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                info.calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                info.calls.append(func.attr)
        elif isinstance(child, ast.Raise):
            info.has_raise = True
        elif isinstance(child, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)):
            info.has_loop = True
        elif isinstance(child, ast.Return) and child.value is not None:
            info.returned |= {
                n.id for n in ast.walk(child.value) if isinstance(n, ast.Name)
            }

    # `total += x` both reads and writes `total`, but the AST marks the
    # AugAssign target as Store only.
    for child in ast.walk(node):
        if isinstance(child, ast.AugAssign):
            target = child.target
            if isinstance(target, ast.Name):
                info.reads.add(target.id)
            elif (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and self_name
                and target.value.id == self_name
            ):
                info.reads.add(f"{self_name}.{target.attr}")

    info.reads -= bound
    if self_name:
        # The bare receiver name is noise -- `self.x` already recorded above.
        info.reads.discard(self_name)
        info.writes.discard(self_name)

    return info


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _live_after(infos: list[StatementInfo]) -> list[set[str]]:
    """Backward liveness: live_after[i] is the set of names read at some point
    after statement i without being reassigned first."""
    live_after: list[set[str]] = [set() for _ in infos]
    live: set[str] = set()
    for i in range(len(infos) - 1, -1, -1):
        live_after[i] = set(live)
        live = (live - infos[i].writes) | infos[i].reads
    return live_after


def _segment_reads_writes(
    segment: list[int], infos: list[StatementInfo]
) -> tuple[set[str], set[str]]:
    """External reads and all writes for a run of statements. Reads already
    satisfied inside the segment don't count -- only values it needs from
    elsewhere, which is what makes a link to a neighbouring segment real."""
    reads: set[str] = set()
    writes: set[str] = set()
    written: set[str] = set()
    for idx in segment:
        reads |= infos[idx].reads - written
        written |= infos[idx].writes
        writes |= infos[idx].writes
    return reads, writes


@dataclass
class StatementBlock:
    infos: list[StatementInfo]
    start_line: int
    end_line: int
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    write_counts: dict[str, int] = field(default_factory=dict)
    external_reads: set[str] = field(default_factory=set)
    outputs: list[str] = field(default_factory=list)
    suggested_params: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    returned: set[str] = field(default_factory=set)
    has_raise: bool = False
    has_loop: bool = False

    @property
    def statement_count(self) -> int:
        return len(self.infos)

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


_COMPOUND_TYPES = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.If,
    ast.With,
    ast.AsyncWith,
    ast.Try,
)


def _build_block(infos: list[StatementInfo], live_at_end: set[str]) -> StatementBlock:
    block = StatementBlock(
        infos=infos,
        start_line=infos[0].node.lineno,
        end_line=max(getattr(i.node, "end_lineno", i.node.lineno) for i in infos),
    )

    written_so_far: set[str] = set()
    for info in infos:
        # A read only counts as "external" if the block hasn't produced that
        # value itself yet -- this is what separates a block's real inputs
        # from its own internal plumbing.
        #
        # Compound statements need one extra subtraction: a whole `for` loop
        # is analysed as a single unit, so a temporary assigned and consumed
        # entirely inside its body (`line_total`) shows up as both a read and
        # a write of that one statement and would otherwise be mistaken for
        # an input the helper needs passed in.
        hidden = info.writes if isinstance(info.node, _COMPOUND_TYPES) else set()
        block.external_reads |= info.reads - written_so_far - hidden
        written_so_far |= info.writes

        block.reads |= info.reads
        block.writes |= info.writes
        block.calls.extend(info.calls)
        block.returned |= info.returned
        block.has_raise = block.has_raise or info.has_raise
        block.has_loop = block.has_loop or info.has_loop
        for name, count in info.write_counts.items():
            block.write_counts[name] = block.write_counts.get(name, 0) + count

    block.outputs = sorted(
        name for name in (block.writes & live_at_end) if is_local(name)
    )

    called = set(block.calls)
    block.suggested_params = sorted(
        name
        for name in block.external_reads
        if is_local(name) and name not in called
    )
    return block


def split_into_blocks(method, min_statements: int = MIN_BLOCK_STATEMENTS) -> list[StatementBlock]:
    """Segment a method's top-level statements into data-cohesive blocks."""
    if method.body is None:
        return []

    statements = [s for s in getattr(method.body, "body", []) if not _is_docstring(s)]
    if not statements:
        return []

    self_name = method.params[0] if method.params else None
    infos = [analyze_statement(s, self_name) for s in statements]
    live_after = _live_after(infos)

    # Pass 1: split on def-use discontinuity.
    segments: list[list[int]] = []
    current: list[int] = []
    current_writes: set[str] = set()

    for idx, info in enumerate(infos):
        if current and (info.reads & current_writes):
            current.append(idx)
        else:
            if current:
                segments.append(current)
            current = [idx]
            current_writes = set()
        current_writes |= info.writes

    if current:
        segments.append(current)

    # Pass 2: absorb undersized segments into a neighbour, choosing the
    # direction by data flow rather than position. A bare `discount = 0.0`
    # reads nothing, so pass 1 always splits it off on its own -- but it
    # belongs with the statements that consume `discount`, which sit AFTER
    # it. Merging blindly backward would staple every initializer onto the
    # end of the preceding step.
    #
    # Smallest-first matters: resolving the one-statement initializers first
    # lets a still-undersized neighbour see the completed step it feeds into
    # and link to it on a later pass.
    segments = [list(s) for s in segments]
    while len(segments) > 1:
        undersized = [i for i, s in enumerate(segments) if len(s) < min_statements]
        if not undersized:
            break

        i = min(undersized, key=lambda j: (len(segments[j]), j))
        reads_i, writes_i = _segment_reads_writes(segments[i], infos)

        forward_link = backward_link = 0
        if i + 1 < len(segments):
            next_reads, _ = _segment_reads_writes(segments[i + 1], infos)
            forward_link = len(writes_i & next_reads)
        if i > 0:
            _, prev_writes = _segment_reads_writes(segments[i - 1], infos)
            backward_link = len(reads_i & prev_writes)

        if i + 1 >= len(segments):
            merge_forward = False
        elif i == 0:
            merge_forward = True
        else:
            # Ties (including no link either way -- see the
            # MIN_BLOCK_STATEMENTS-absorption limitation in the module
            # docstring) fall forward: an initializer introduces the step
            # that follows it.
            merge_forward = forward_link >= backward_link

        if merge_forward:
            segments[i + 1] = segments[i] + segments[i + 1]
        else:
            segments[i - 1] = segments[i - 1] + segments[i]
        del segments[i]

    return [
        _build_block([infos[i] for i in indices], live_after[indices[-1]])
        for indices in segments
    ]
