"""Structural duplicate-code detection between methods.

Like the Long Parameter List and unused-import checks, this is a plain static
check rather than an ML prediction -- structural similarity is measured
directly, so there's nothing for a classifier to have an opinion about.

How it works: each method body is flattened into a token sequence of AST node
types, then the two sequences are compared with difflib.SequenceMatcher. Only
node *types* are emitted, never the identifier strings attached to them, so
variable names, parameter names, attribute names and the method's own name all
disappear from the comparison while control flow, operators, call shape and
nesting survive. That is the point: a copy-pasted method with every local
renamed still scores as a near-exact match.

What this deliberately does NOT claim: that a high score means the two methods
are redundant. This measures *shape*, not behavior or text. Methods that follow
the same template -- a run of similar validators, parallel getters, three
methods that each loop over a list and append to a result -- score high without
being duplicates in any sense worth fixing. False positives are more likely
here than for the other smells in this tool, and every consumer of these
results is expected to say so in its output.

Scope: within a single class. The pair-scoring helpers below take a plain list
of methods rather than a ClassInfo, so a cross-class/whole-repo variant can be
layered on later without changing the scoring itself.
"""

from __future__ import annotations

import ast
import difflib

from engine.models import ClassInfo, MethodInfo

# 0.8 = "same shape, a few statements differ". Below roughly 0.75 the matches
# stop being convincing on real code; above 0.9 only near-verbatim copies
# survive. Exposed as a parameter because the right cut-off is a judgement
# call about how much noise the reader will tolerate.
DEFAULT_SIMILARITY_THRESHOLD = 0.8

# Short methods are structurally identical to each other almost by accident:
# every one-line getter in a codebase has the same three-node body, and
# reporting those pairs would bury any real finding. Two statements is still
# too little to be worth a claim, so require at least three (docstring
# excluded -- see _code_body).
MIN_BODY_STATEMENTS = 3


def _code_body(func_node: ast.AST) -> list[ast.stmt]:
    """A function's statements with a leading docstring dropped -- prose isn't
    code, and leaving it in would let two methods score as similar on the
    strength of both being documented."""
    body = getattr(func_node, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _statement_count(func_node: ast.AST) -> int:
    """Statements anywhere in the body, including nested ones, so a method
    whose three statements are all inside one `if` still counts as three."""
    return sum(
        1 for stmt in _code_body(func_node) for node in ast.walk(stmt) if isinstance(node, ast.stmt)
    )


def _append_tokens(node: ast.AST, tokens: list[str]) -> None:
    # Only the node's *type* is emitted. Identifiers (Name.id, Attribute.attr,
    # arg.arg, FunctionDef.name, keyword.arg, ...) are plain strings rather
    # than child nodes, so they are stripped for free by never being visited.
    if isinstance(node, ast.Constant):
        # Literal values vary between copies (0.08 vs 0.1, "GET" vs "POST"),
        # but a str where the other method has an int is a real difference --
        # keep the type, drop the value.
        tokens.append(f"Const:{type(node.value).__name__}")
    else:
        tokens.append(type(node).__name__)

    children = list(ast.iter_child_nodes(node))
    if not children:
        return

    # Brackets keep nesting depth in the sequence: without them, an `if`
    # containing a loop and an `if` followed by a loop flatten to the same
    # tokens.
    tokens.append("(")
    for child in children:
        _append_tokens(child, tokens)
    tokens.append(")")


def normalize_method(method: MethodInfo) -> list[str]:
    """A method's body as a name-free token sequence. Public because the
    similarity score is meaningless without being able to see what was
    actually compared."""
    if method.body is None:
        return []

    tokens: list[str] = []
    for stmt in _code_body(method.body):
        _append_tokens(stmt, tokens)
    return tokens


def _token_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    if not tokens_a or not tokens_b:
        # SequenceMatcher scores two empty sequences as a perfect 1.0. For an
        # unparsed or empty body that's not a duplicate finding, it's an
        # absence of evidence.
        return 0.0

    # autojunk=False is required, not cosmetic: it would otherwise treat any
    # token making up >1% of a sequence of 200+ elements as junk, and these
    # sequences are *made* of repeated tokens ("(", "Name", "Load"), so the
    # heuristic discards exactly the signal being measured on longer methods.
    return difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False).ratio()


def compute_method_similarity(m1: MethodInfo, m2: MethodInfo) -> float:
    """Structural similarity of two method bodies, 0.0 to 1.0.

    1.0 means the two bodies have identical AST shape once names are stripped
    -- identical code, or code that differs only in its identifiers and literal
    values. This applies no size floor: two trivial one-line methods really do
    have identical structure, and saying so is the honest answer. Deciding
    that such a pair isn't worth reporting is find_duplicate_pairs()' job.
    """
    return _token_similarity(normalize_method(m1), normalize_method(m2))


def _comparable_methods(methods: list[MethodInfo]) -> list[tuple[MethodInfo, list[str]]]:
    """Methods substantial enough to be worth comparing, paired with their
    token sequence. Tokenizing once per method rather than once per pair keeps
    the O(n^2) loop over pairs cheap for a class with many methods."""
    comparable = []
    for method in methods:
        if method.body is None or _statement_count(method.body) < MIN_BODY_STATEMENTS:
            continue
        tokens = normalize_method(method)
        if tokens:
            comparable.append((method, tokens))
    return comparable


def find_duplicate_pairs(
    cls: ClassInfo, threshold: float = DEFAULT_SIMILARITY_THRESHOLD
) -> list[tuple[str, str, float]]:
    """Method pairs within `cls` whose bodies are at least `threshold` similar,
    as (method_a_name, method_b_name, similarity), most similar first.

    Methods below MIN_BODY_STATEMENTS are skipped entirely -- see the note
    there on why short methods match each other by accident.
    """
    comparable = _comparable_methods(cls.methods)

    pairs = []
    for i in range(len(comparable)):
        method_a, tokens_a = comparable[i]
        for j in range(i + 1, len(comparable)):
            method_b, tokens_b = comparable[j]
            similarity = _token_similarity(tokens_a, tokens_b)
            if similarity >= threshold:
                pairs.append((method_a.name, method_b.name, similarity))

    # Name tie-break keeps the order stable for equally similar pairs, which
    # matters for reproducible reports and tests.
    pairs.sort(key=lambda pair: (-pair[2], pair[0], pair[1]))
    return pairs
