import textwrap

from engine.parser import find_unused_imports, find_unused_imports_detailed, parse_file


def _write_and_parse(tmp_path, source):
    file_path = tmp_path / "sample.py"
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))


def _write(tmp_path, source, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(file_path)


def test_simple_class_with_one_method(tmp_path):
    source = """
    class Greeter:
        def greet(self, name):
            return f"Hello, {name}"
    """
    classes = _write_and_parse(tmp_path, source)

    assert len(classes) == 1
    cls = classes[0]
    assert cls.name == "Greeter"
    assert len(cls.methods) == 1

    method = cls.methods[0]
    assert method.name == "greet"
    assert method.class_name == "Greeter"
    assert method.params == ["self", "name"]


def test_class_with_methods_calling_each_other(tmp_path):
    source = """
    class Worker:
        def start(self):
            self.setup()
            return self.run()

        def setup(self):
            pass

        def run(self):
            return True
    """
    classes = _write_and_parse(tmp_path, source)

    assert len(classes) == 1
    cls = classes[0]
    assert len(cls.methods) == 3

    start_method = next(m for m in cls.methods if m.name == "start")
    assert "setup" in start_method.calls_made
    assert "run" in start_method.calls_made


def test_class_with_self_attribute_reads_and_writes(tmp_path):
    source = """
    class Counter:
        def __init__(self):
            self.count = 0

        def increment(self):
            self.count = self.count + 1
    """
    classes = _write_and_parse(tmp_path, source)

    assert len(classes) == 1
    cls = classes[0]
    assert "count" in cls.fields

    increment_method = next(m for m in cls.methods if m.name == "increment")
    assert "count" in increment_method.fields_accessed


def test_multiple_classes_in_one_file(tmp_path):
    source = """
    class Base:
        def base_method(self):
            pass

    class Derived(Base):
        def derived_method(self):
            self.value = 1
    """
    classes = _write_and_parse(tmp_path, source)

    assert len(classes) == 2
    names = {c.name for c in classes}
    assert names == {"Base", "Derived"}

    derived = next(c for c in classes if c.name == "Derived")
    assert derived.base_classes == ["Base"]


def test_nested_class_is_detected_as_its_own_independent_class(tmp_path):
    source = """
    class Article:
        title = "x"

        def save(self):
            pass

        class Meta:
            ordering = ["title"]
            verbose_name = "article"
    """
    classes = _write_and_parse(tmp_path, source)

    names = {c.name for c in classes}
    assert names == {"Article", "Meta"}

    outer = next(c for c in classes if c.name == "Article")
    inner = next(c for c in classes if c.name == "Meta")

    # The outer class's own methods are unaffected by the nested class --
    # Meta contributes no FunctionDef to Article.methods.
    assert [m.name for m in outer.methods] == ["save"]
    assert outer.parent_class is None
    assert inner.parent_class == "Article"

    # The nested class is measured as an independent class in its own
    # right: no methods of its own here, but it exists as a real ClassInfo
    # with its own line span, not folded into Article's metrics.
    assert inner.methods == []
    assert inner.start_line > outer.start_line
    assert inner.end_line <= outer.end_line

    # Class-body attribute assignments are visible as fields even with zero
    # methods -- this is what makes Meta's NOPA nonzero for the Data Class
    # rule instead of silently reading 0 because nothing is `self.x`.
    assert "title" in outer.fields
    assert set(inner.fields) == {"ordering", "verbose_name"}


def test_class_body_field_does_not_double_count_a_nested_class(tmp_path):
    """A nested `class Meta:` is a ClassDef, not an ast.Assign target, so it
    must not also show up in the parent's `fields` list -- it is parsed as
    its own independent ClassInfo (see parent_class), and counting it again
    here would double-book the same entity as both a class and an attribute."""
    source = """
    class Article:
        title = "x"

        class Meta:
            ordering = ["title"]
    """
    classes = _write_and_parse(tmp_path, source)
    outer = next(c for c in classes if c.name == "Article")
    assert "Meta" not in outer.fields
    assert outer.fields == ["title"]


def test_two_levels_of_nested_classes_are_all_detected(tmp_path):
    source = """
    class Outer:
        def outer_method(self):
            pass

        class Middle:
            def middle_method(self):
                pass

            class Inner:
                def inner_method(self):
                    pass
    """
    classes = _write_and_parse(tmp_path, source)

    by_name = {c.name: c for c in classes}
    assert set(by_name) == {"Outer", "Middle", "Inner"}

    assert by_name["Outer"].parent_class is None
    assert by_name["Middle"].parent_class == "Outer"
    assert by_name["Inner"].parent_class == "Middle"

    # Each class's own methods stay its own -- no method leaks up or down
    # a nesting level.
    assert [m.name for m in by_name["Outer"].methods] == ["outer_method"]
    assert [m.name for m in by_name["Middle"].methods] == ["middle_method"]
    assert [m.name for m in by_name["Inner"].methods] == ["inner_method"]


def test_sibling_classes_with_same_named_nested_class_dont_collide(tmp_path):
    """Two Django-style models, each with its own `class Meta:` -- the
    scenario compute_all_metrics() must not let collide via a name-keyed
    dict (see engine/metrics.py compute_all_metrics docstring)."""
    from engine.metrics import compute_all_metrics

    source = """
    class Author:
        def save(self):
            pass

        class Meta:
            ordering = ["name"]

    class Book:
        def save(self):
            pass

        def publish(self):
            pass

        class Meta:
            ordering = ["title"]
            unique_together = ["title", "author"]
    """
    classes = _write_and_parse(tmp_path, source)
    metas = [c for c in classes if c.name == "Meta"]
    assert len(metas) == 2

    metrics = compute_all_metrics(classes)
    author_meta = next(c for c in metas if c.parent_class == "Author")
    book_meta = next(c for c in metas if c.parent_class == "Book")

    # Looked up by id(cls), each Meta gets ITS OWN metrics, not the other's.
    author_entry = metrics[id(author_meta)]
    book_entry = metrics[id(book_meta)]
    assert author_entry is not book_entry
    assert author_entry["class_length"] != book_entry["class_length"]


def test_obviously_unused_import_is_flagged(tmp_path):
    path = _write(tmp_path, """
    import os

    def greet(name):
        return f"Hello, {name}"
    """)

    assert find_unused_imports(path) == ["os"]


def test_file_where_every_import_is_used_reports_none(tmp_path):
    path = _write(tmp_path, """
    import json
    from collections import OrderedDict

    def load(raw):
        data = json.loads(raw)
        return OrderedDict(data)
    """)

    assert find_unused_imports(path) == []


def test_aliased_import_tracked_by_its_bound_name(tmp_path):
    path = _write(tmp_path, """
    import numpy as np
    import pandas as pd

    def total(values):
        return np.sum(values)
    """)

    assert find_unused_imports(path) == ["pd"]


def test_from_import_with_multiple_names_flags_only_the_unused_one(tmp_path):
    path = _write(tmp_path, """
    from typing import List, Optional

    def first(items: List[int]):
        return items[0]
    """)

    assert find_unused_imports(path) == ["Optional"]


def test_detailed_form_includes_line_number_and_display_text(tmp_path):
    path = _write(tmp_path, """
    import os
    import json

    def load(path):
        return json.load(open(path))
    """)

    detailed = find_unused_imports_detailed(path)

    assert len(detailed) == 1
    assert detailed[0]["name"] == "os"
    assert detailed[0]["line"] == 2
    assert detailed[0]["display"] == "os"


def test_star_import_is_not_flagged_since_usage_cant_be_determined(tmp_path):
    path = _write(tmp_path, """
    from os.path import *

    def run():
        return 1
    """)

    assert find_unused_imports(path) == []
