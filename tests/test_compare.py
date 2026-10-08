from app.pipeline.compare import _render_report_markdown
from app.pipeline.schemas import ComparisonData, ComparisonRow


def test_report_markdown_uses_only_compact_evidence_linked_comparison():
    data = ComparisonData(
        reasoning="Both values are supported by product documentation.",
        rows=[ComparisonRow(
            dimension="Export", a_value="HTML", b_value="React", verdict="not_comparable",
            evidence_fact_ids=[11, 22],
        )],
        confidence_notes="Pricing was not established by the available pages.",
    )

    markdown = _render_report_markdown(
        "Alpha", "Beta", data,
        [{"id": 11, "field": "Export", "value": "HTML"}],
        [{"id": 22, "field": "Export", "value": "React"}],
    )

    assert "| Export | HTML | React | not comparable · F11, F22 |" in markdown
    assert "Pricing was not established" in markdown
    assert "F11" in markdown and "F22" in markdown
    assert "overall winner" in markdown
