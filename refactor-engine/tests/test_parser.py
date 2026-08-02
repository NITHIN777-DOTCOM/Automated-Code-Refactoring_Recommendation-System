import textwrap

from engine.parser import parse_file


def _write_and_parse(tmp_path, source):
    file_path = tmp_path / "sample.py"
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))


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
