from engine.models import ClassInfo, MethodInfo
from engine.suggester import suggest_refactoring
from engine.suggester.suggest import generate_suggestions


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
