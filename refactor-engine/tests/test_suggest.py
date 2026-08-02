import textwrap

from engine.models import ClassInfo, MethodInfo
from engine.parser import parse_file
from engine.suggester import (
    suggest_method_extractions,
    suggest_method_moves,
    suggest_refactoring,
)
from engine.suggester.suggest import (
    generate_extract_method_suggestions,
    generate_move_method_suggestions,
    generate_suggestions,
)


def _parse_class(tmp_path, source, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))[0]


def _method(name, fields_accessed=None):
    return MethodInfo(
        name=name,
        class_name="Sample",
        start_line=1,
        end_line=2,
        fields_accessed=fields_accessed or [],
    )


def test_two_clusters_produce_two_extract_class_suggestions():
    cls = ClassInfo(
        name="Sample",
        file_path="<test>",
        methods=[
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["y"]),
            _method("method_d", fields_accessed=["y"]),
        ],
    )
    clusters = [{"method_a", "method_b"}, {"method_c", "method_d"}]

    suggestions = generate_suggestions(cls, clusters)

    assert len(suggestions) == 2
    for s in suggestions:
        assert s["type"] == "extract_class"
        assert s["source_class"] == "Sample"
        assert set(s["methods_to_extract"]).issubset({"method_a", "method_b", "method_c", "method_d"})
        assert s["shared_fields"]
        assert s["suggested_name"]
        assert "rationale" in s


def test_single_cluster_produces_no_clear_split():
    cls = ClassInfo(
        name="Sample",
        file_path="<test>",
        methods=[
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
        ],
    )

    suggestions = generate_suggestions(cls, [{"method_a", "method_b"}])

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "no_clear_split"
    assert suggestions[0]["source_class"] == "Sample"
    assert "note" in suggestions[0]


def test_empty_clusters_also_produces_no_clear_split():
    cls = ClassInfo(name="Sample", file_path="<test>", methods=[])

    suggestions = generate_suggestions(cls, [])

    assert suggestions[0]["type"] == "no_clear_split"


def test_naming_heuristic_prefers_shared_field_and_render_role():
    # generate_suggestions treats a single cluster as "no clean separation";
    # a second unrelated cluster is included purely to exercise the
    # extract_class path so the naming heuristic on the footer cluster can
    # be checked in isolation.
    cls = ClassInfo(
        name="ReportManager",
        file_path="<test>",
        methods=[
            _method("set_footer", fields_accessed=["footer"]),
            _method("render_footer", fields_accessed=["footer"]),
            _method("set_logo", fields_accessed=["logo_path"]),
            _method("render_logo", fields_accessed=["logo_path"]),
        ],
    )

    suggestions = generate_suggestions(
        cls, [{"set_footer", "render_footer"}, {"set_logo", "render_logo"}]
    )

    footer_suggestion = next(
        s for s in suggestions if "set_footer" in s["methods_to_extract"]
    )
    assert footer_suggestion["suggested_name"] == "ReportFooterRenderer"


def test_suggest_refactoring_end_to_end_wiring():
    cls = ClassInfo(
        name="Sample",
        file_path="<test>",
        methods=[
            _method("method_a", fields_accessed=["x"]),
            _method("method_b", fields_accessed=["x"]),
            _method("method_c", fields_accessed=["y"]),
            _method("method_d", fields_accessed=["y"]),
        ],
    )

    suggestions = suggest_refactoring(cls)

    assert len(suggestions) == 2
    assert all(s["type"] == "extract_class" for s in suggestions)


# ---------------------------------------------------------------------------
# Extract Method
# ---------------------------------------------------------------------------

# Raw: the fixture contains a `"\n"` literal that must survive into the
# generated source rather than becoming a real newline here.
_LONG_METHOD_SOURCE = r"""
class OrderProcessor:
    def __init__(self):
        self.tax_rate = 0.08

    def process_order(self, customer, items, coupon_code):
        errors = []
        if not customer:
            errors.append("customer is required")
        if not items:
            errors.append("order must contain at least one item")
        if errors:
            raise ValueError("; ".join(errors))

        subtotal = 0.0
        for item in items:
            line_total = item["price"] * item["quantity"]
            subtotal += line_total
        discount = 0.0
        if coupon_code == "SAVE10":
            discount = subtotal * 0.10
        total = (subtotal - discount) * (1 + self.tax_rate)

        lines = []
        lines.append("Customer: " + customer)
        lines.append("Total: " + str(total))
        receipt = "\n".join(lines)
        return receipt
"""


def test_long_method_splits_into_named_extract_method_suggestions(tmp_path):
    cls = _parse_class(tmp_path, _LONG_METHOD_SOURCE)

    suggestions = generate_extract_method_suggestions(cls)

    assert len(suggestions) == 3
    assert all(s["type"] == "extract_method" for s in suggestions)
    assert all(s["source_method"] == "process_order" for s in suggestions)

    names = [s["suggested_name"] for s in suggestions]
    assert names[0] == "_validate_input"
    assert names[1] == "_compute_total"
    assert names[2] == "_build_receipt"

    # Line ranges must be ordered, non-overlapping and inside the method.
    spans = [tuple(s["lines"]) for s in suggestions]
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:]))
    assert spans[0][0] >= cls.methods[1].start_line


def test_extract_method_suggestion_infers_parameters_and_rationale(tmp_path):
    cls = _parse_class(tmp_path, _LONG_METHOD_SOURCE)

    suggestions = generate_extract_method_suggestions(cls)
    validation = suggestions[0]

    assert validation["suggested_params"] == ["customer", "items"]
    assert "is 23 lines long" in validation["rationale"]
    assert "which handle validation" in validation["rationale"]
    assert "_validate_input()" in validation["rationale"]


def test_short_method_yields_no_extract_method_suggestions(tmp_path):
    source = """
    class Small:
        def run(self, raw):
            cleaned = raw.strip()
            parsed = int(cleaned)
            return parsed * 2
    """
    cls = _parse_class(tmp_path, source)

    assert generate_extract_method_suggestions(cls) == []


def test_long_but_indivisible_method_yields_no_suggestions(tmp_path):
    """Every statement feeds the next, so there is no split point. Proposing
    "extract the whole method" would be worse than saying nothing."""
    source = """
    class Chain:
        def run(self, raw):
            a = raw.strip()
            b = a.lower()
            c = b.replace("x", "y")
            d = c.title()
            e = d.strip()
            f = e + "!"
            g = f * 2
            h = g + "?"
            i = h.upper()
            j = i + "."
            k = j.strip()
            return k
    """
    cls = _parse_class(tmp_path, source)

    assert generate_extract_method_suggestions(cls) == []


def test_suggest_method_extractions_wrapper(tmp_path):
    cls = _parse_class(tmp_path, _LONG_METHOD_SOURCE)

    assert suggest_method_extractions(cls) == generate_extract_method_suggestions(cls)


# ---------------------------------------------------------------------------
# Move Method (Feature Envy)
# ---------------------------------------------------------------------------


def _parse_classes(tmp_path, source, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))


def test_move_method_identifies_the_envied_class(tmp_path):
    source = """
    class PaymentProcessor:
        def authorize(self, amount):
            return amount > 0

        def charge(self, amount):
            return amount

    class Order:
        def __init__(self):
            self.amount = 0

        def checkout(self, amount):
            self.amount = amount
            processor = PaymentProcessor()
            if processor.authorize(amount):
                return processor.charge(amount)
            return 0
    """
    classes = _parse_classes(tmp_path, source)
    order = next(c for c in classes if c.name == "Order")

    suggestions = generate_move_method_suggestions(order, classes)

    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["type"] == "move_method"
    assert s["source_method"] == "checkout"
    assert s["target_class"] == "PaymentProcessor"
    # PaymentProcessor() + authorize + charge = 3; own field `amount` = 1.
    assert s["external_references"] == 3
    assert s["own_data_uses"] == 1
    assert "calls into PaymentProcessor 3 times" in s["rationale"]
    assert "accesses its own class's data 1 time" in s["rationale"]


def test_method_using_mostly_its_own_data_is_not_flagged(tmp_path):
    """A class-level Feature Envy label doesn't make every method guilty."""
    source = """
    class Helper:
        def assist(self):
            return 1

    class Worker:
        def __init__(self):
            self.a = 1
            self.b = 2
            self.c = 3

        def run(self):
            helper = Helper()
            return self.a + self.b + self.c + helper.assist()
    """
    classes = _parse_classes(tmp_path, source)
    worker = next(c for c in classes if c.name == "Worker")

    # 2 external refs (Helper + assist) vs 3 own fields -> not envious.
    assert generate_move_method_suggestions(worker, classes) == []


def test_evenly_split_calls_produce_a_soft_note_not_a_guess(tmp_path):
    """When two classes are equally coupled, naming either would be a coin
    flip -- the suggestion must say so instead."""
    source = """
    class Alpha:
        def alpha_one(self):
            return 1

        def alpha_two(self):
            return 2

    class Beta:
        def beta_one(self):
            return 1

        def beta_two(self):
            return 2

    class Caller:
        def reach(self):
            a = Alpha()
            b = Beta()
            return a.alpha_one() + a.alpha_two() + b.beta_one() + b.beta_two()
    """
    classes = _parse_classes(tmp_path, source)
    caller = next(c for c in classes if c.name == "Caller")

    suggestions = generate_move_method_suggestions(caller, classes)

    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["type"] == "no_clear_envy_target"
    assert s["candidates"] == ["Alpha", "Beta"]
    assert "split evenly between Alpha and Beta" in s["note"]
    assert "target_class" not in s


def test_method_name_owned_by_several_classes_is_not_attributed(tmp_path):
    """`process` is defined on two classes, so a call to it can't be pinned on
    either one -- the suggester must not pick a winner off an ambiguous name."""
    source = """
    class FirstHandler:
        def process(self, x):
            return x

    class SecondHandler:
        def process(self, x):
            return x

    class Dispatcher:
        def dispatch(self, target, x):
            return target.process(x) + target.process(x)
    """
    classes = _parse_classes(tmp_path, source)
    dispatcher = next(c for c in classes if c.name == "Dispatcher")

    suggestions = generate_move_method_suggestions(dispatcher, classes)

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "no_clear_envy_target"
    assert "more than one class" in suggestions[0]["note"]


def test_single_outbound_call_is_too_thin_to_name_a_destination(tmp_path):
    source = """
    class Logger:
        def log(self, msg):
            return msg

    class Service:
        def run(self):
            logger = Logger()
            return 1
    """
    classes = _parse_classes(tmp_path, source)
    service = next(c for c in classes if c.name == "Service")

    # Only the `Logger()` construction -- below MIN_EXTERNAL_REFS_FOR_MOVE.
    assert generate_move_method_suggestions(service, classes) == []


def test_constructors_are_never_proposed_for_moving(tmp_path):
    source = """
    class Engine:
        def start(self):
            return True

        def stop(self):
            return False

    class Car:
        def __init__(self):
            engine = Engine()
            engine.start()
            engine.stop()
    """
    classes = _parse_classes(tmp_path, source)
    car = next(c for c in classes if c.name == "Car")

    suggestions = generate_move_method_suggestions(car, classes)

    assert all(s["source_method"] != "__init__" for s in suggestions)


def test_suggest_method_moves_wrapper(tmp_path):
    classes = _parse_classes(tmp_path, """
    class Engine:
        def start(self):
            return True

        def stop(self):
            return False

    class Car:
        def drive(self):
            engine = Engine()
            engine.start()
            return engine.stop()
    """)
    car = next(c for c in classes if c.name == "Car")

    assert suggest_method_moves(car, classes) == generate_move_method_suggestions(car, classes)
