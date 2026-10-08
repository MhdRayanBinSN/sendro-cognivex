from app.pipeline.extract import build_context_pack, verify_quotes
from app.pipeline.schemas import ExtractedFact, FactExtraction


def test_quote_verifier_drops_unmatched_or_wrong_source_facts():
    extraction = FactExtraction(reasoning="", facts=[
        ExtractedFact(field="feature", value="A", source_url="https://example.com", evidence_quote="Exact phrase"),
        ExtractedFact(field="feature", value="B", source_url="https://other.test", evidence_quote="Exact phrase"),
        ExtractedFact(field="feature", value="C", source_url="https://example.com", evidence_quote="Not present"),
    ])
    verified = verify_quotes(extraction, "The Exact phrase appears here.", "https://example.com/")
    assert len(verified.facts) == 1
    assert verified.facts[0].value == "A"


def test_context_pack_prefers_relevant_passages():
    text = "Introductory product overview. " + "Filler text. " * 20 + "Pricing starts at $20. " + "More filler. " * 20
    context = build_context_pack(text, 120)

    assert "Introductory product overview" in context
    assert "Pricing starts at $20" in context
    assert len(context) <= 120
