import textwrap

from engine.metrics import (
    cbo,
    class_length,
    cyclomatic_complexity,
    depth_of_inheritance,
    fan_in,
    fan_out,
    lcom,
    method_length,
)
from engine.parser import parse_file


def _write_and_parse(tmp_path, source, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return parse_file(str(file_path))


def test_cyclomatic_complexity_counts_decision_points(tmp_path):
    # base complexity 1, + 3 if-statements = 4
    source = """
    class Checker:
        def check(self, x):
            if x == 1:
                return "one"
            if x == 2:
                return "two"
            if x == 3:
                return "three"
            return "other"
    """
    classes = _write_and_parse(tmp_path, source)
    method = classes[0].methods[0]

    assert cyclomatic_complexity(method) == 4


def test_cyclomatic_complexity_counts_loops_and_bool_ops(tmp_path):
    # base complexity 1, + for + while + (2-value boolop = +1) = 4
    source = """
    class Looper:
        def run(self, items, flag_a, flag_b):
            for item in items:
                pass
            while flag_a:
                flag_a = False
            if flag_a and flag_b:
                pass
    """
    classes = _write_and_parse(tmp_path, source)
    method = classes[0].methods[0]

    assert cyclomatic_complexity(method) == 5


def test_method_length(tmp_path):
    source = """
    class Simple:
        def five_lines(self):
            a = 1
            b = 2
            c = 3
            return a + b + c
    """
    classes = _write_and_parse(tmp_path, source)
    method = classes[0].methods[0]

    assert method_length(method) == method.end_line - method.start_line
    assert method_length(method) == 4


def test_class_length(tmp_path):
    source = """
    class Simple:
        def method_one(self):
            pass

        def method_two(self):
            pass
    """
    classes = _write_and_parse(tmp_path, source)
    cls = classes[0]

    assert class_length(cls) == cls.end_line - cls.start_line


def test_lcom_cohesive_vs_non_cohesive(tmp_path):
    cohesive_source = """
    class Cohesive:
        def __init__(self):
            self.value = 0

        def increment(self):
            self.value += 1

        def decrement(self):
            self.value -= 1
    """
    cohesive_classes = _write_and_parse(tmp_path, cohesive_source, "cohesive.py")
    cohesive_cls = cohesive_classes[0]
    # all 3 method pairs share `value` -> 0 non-sharing pairs / 3 total = 0.0
    assert lcom(cohesive_cls) == 0.0

    non_cohesive_source = """
    class NonCohesive:
        def set_a(self):
            self.a = 1

        def set_b(self):
            self.b = 2
    """
    non_cohesive_classes = _write_and_parse(tmp_path, non_cohesive_source, "non_cohesive.py")
    non_cohesive_cls = non_cohesive_classes[0]
    # the only method pair shares no fields -> 1 non-sharing pair / 1 total = 1.0
    assert lcom(non_cohesive_cls) == 1.0


def test_cbo_and_fan_out_detect_instantiation(tmp_path):
    source = """
    class Engine:
        def start(self):
            return True

    class Car:
        def drive(self):
            engine = Engine()
            return engine.start()
    """
    classes = _write_and_parse(tmp_path, source)
    car = next(c for c in classes if c.name == "Car")

    assert cbo(car, classes) == 1
    assert fan_out(car, classes) >= 1


def test_fan_in_detects_callers(tmp_path):
    source = """
    class Engine:
        def start(self):
            return True

    class Car:
        def drive(self):
            engine = Engine()
            return engine.start()
    """
    classes = _write_and_parse(tmp_path, source)
    engine = next(c for c in classes if c.name == "Engine")

    assert fan_in(engine, classes) == 1


def test_depth_of_inheritance(tmp_path):
    source = """
    class Animal:
        def speak(self):
            pass

    class Dog(Animal):
        def bark(self):
            pass

    class Puppy(Dog):
        def yip(self):
            pass
    """
    classes = _write_and_parse(tmp_path, source)
    animal = next(c for c in classes if c.name == "Animal")
    dog = next(c for c in classes if c.name == "Dog")
    puppy = next(c for c in classes if c.name == "Puppy")

    assert depth_of_inheritance(animal, classes) == 0
    assert depth_of_inheritance(dog, classes) == 1
    assert depth_of_inheritance(puppy, classes) == 2
