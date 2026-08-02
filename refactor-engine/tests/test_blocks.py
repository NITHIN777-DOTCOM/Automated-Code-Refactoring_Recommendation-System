import textwrap

from engine.parser import parse_file
from engine.suggester.blocks import split_into_blocks


def _method(tmp_path, source, method_name, filename="sample.py"):
    file_path = tmp_path / filename
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    cls = parse_file(str(file_path))[0]
    return next(m for m in cls.methods if m.name == method_name)


def test_distinct_steps_split_into_separate_blocks(tmp_path):
    """Three steps with no data flowing between them must come back as three
    blocks -- this is the core guarantee the Extract Method suggester rests on."""
    source = """
    class Report:
        def build(self, rows, header_text, footer_text):
            totals = 0
            for row in rows:
                totals += row
            summary = "total=" + str(totals)

            header = header_text.strip()
            header = header.upper()
            header = "== " + header

            footer = footer_text.strip()
            footer = footer.lower()
            footer = "-- " + footer
            return summary
    """
    method = _method(tmp_path, source, "build")
    blocks = split_into_blocks(method)

    assert len(blocks) == 3
    assert [b.statement_count for b in blocks] == [3, 3, 4]


def test_single_continuous_chain_is_one_block(tmp_path):
    """Every statement consumes the previous one's output, so there is no
    split point -- the suggester must not invent one."""
    source = """
    class Pipeline:
        def run(self, raw):
            cleaned = raw.strip()
            parsed = int(cleaned)
            doubled = parsed * 2
            shifted = doubled + 10
            return shifted
    """
    method = _method(tmp_path, source, "run")
    blocks = split_into_blocks(method)

    assert len(blocks) == 1


def test_bare_initializer_attaches_to_the_step_it_opens(tmp_path):
    """`shipping = 0.0` reads nothing, so def-use segmentation splits it off
    on its own. It belongs with the statements that consume it, which come
    AFTER -- merging it backward would staple it onto the previous step."""
    source = """
    class Cart:
        def price(self, items, country):
            subtotal = 0.0
            for item in items:
                subtotal += item
            subtotal = round(subtotal, 2)

            shipping = 0.0
            if country != "US":
                shipping = 25.0
            total = subtotal + shipping
            return total
    """
    method = _method(tmp_path, source, "price")
    blocks = split_into_blocks(method)

    assert len(blocks) == 2
    # `shipping = 0.0` opens the second block, it does not close the first.
    assert blocks[0].statement_count == 3
    assert "shipping" in blocks[1].writes
    assert "shipping" not in blocks[0].writes


def test_loop_variable_is_not_reported_as_an_input(tmp_path):
    """`for item in items` binds `item` internally. Counting it as a read
    would both link unrelated loops together and suggest passing `item` in
    as a parameter."""
    source = """
    class Totals:
        def add_up(self, items):
            total = 0
            for item in items:
                total += item
            average = total / len(items)
            return average
    """
    method = _method(tmp_path, source, "add_up")
    blocks = split_into_blocks(method)

    assert len(blocks) == 1
    assert "item" not in blocks[0].suggested_params
    assert blocks[0].suggested_params == ["items"]


def test_block_outputs_only_count_values_read_later(tmp_path):
    source = """
    class Calc:
        def run(self, rows, label):
            total = 0
            for row in rows:
                total += row
            scratch = total * 2

            text = label.strip()
            text = text.upper()
            result = text + str(total)
            return result
    """
    method = _method(tmp_path, source, "run")
    blocks = split_into_blocks(method)

    assert len(blocks) == 2
    # `total` is read by the second block, `scratch` is never used again.
    assert "total" in blocks[0].outputs
    assert "scratch" not in blocks[0].outputs
