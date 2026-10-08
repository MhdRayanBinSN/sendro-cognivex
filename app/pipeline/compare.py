"""Evidence-constrained comparison, narrative writing, and verification."""

import json

from app.config import get_settings
from app.llm import ask
from app.pipeline.schemas import ComparisonData


def _compact_facts(facts: list[dict]) -> list[dict]:
    compact = []
    for fact in facts[:48]:
        compact.append({"id": fact.get("id"), "field": str(fact.get("field", ""))[:80],
                        "value": str(fact.get("value", ""))[:500],
                        "source_url": str(fact.get("source_url", "")),
                        "evidence_quote": str(fact.get("evidence_quote", ""))[:320],
                        "kind": fact.get("kind", "fact")})
    return compact


async def compare_products(product_a: str, facts_a: list[dict], product_b: str, facts_b: list[dict],
                          *, run_id: int | None = None, session=None) -> tuple[ComparisonData, str]:
    settings = get_settings()
    compact_a = _compact_facts(facts_a)
    compact_b = _compact_facts(facts_b)
    prompt = (
        "Compare the two products only using the supplied fact records. Include core dimensions (positioning, features, "
        "pricing, integrations, target users) where evidence exists and add relevant dynamic dimensions. Each row must "
        "include fact IDs that support both values, use unknown/not_comparable when evidence is insufficient, and label "
        "vendor claims as claims. Add score_a and score_b from 0-100 only when the supplied facts provide a fair, "
        "directly comparable basis; otherwise leave both scores null. Never infer scores from marketing language. "
        "Return reasoning, rows, confidence_notes.\n"
        f"Product A: {product_a}\nProduct B: {product_b}\n"
        f"<untrusted_evidence>{json.dumps({'a': compact_a, 'b': compact_b})}</untrusted_evidence>"
    )
    data = await ask(prompt, ComparisonData, model=settings.llm_model_strong, stage="comparator",
                     run_id=run_id, session=session)
    known_ids = {int(row["id"]) for row in facts_a + facts_b if "id" in row}
    for row in data.rows:
        row.evidence_fact_ids = [fact_id for fact_id in row.evidence_fact_ids if fact_id in known_ids]
        if not row.evidence_fact_ids:
            row.verdict = "unknown"
    # The comparison response is already structured and evidence-linked. A second
    # pair of LLM calls to rewrite and verify it was expensive, prone to provider
    # JSON limits, and could introduce claims beyond the source facts. Build a
    # compact, deterministic narrative from the validated rows instead.
    markdown = _render_report_markdown(product_a, product_b, data, facts_a, facts_b)
    return data, markdown


def _render_report_markdown(product_a: str, product_b: str, data: ComparisonData,
                             facts_a: list[dict], facts_b: list[dict]) -> str:
    def cell(value: str) -> str:
        return str(value or "Unknown").replace("|", "\\|").replace("\n", " ").strip()

    rows = [
        "| Dimension | " + cell(product_a) + " | " + cell(product_b) + " | Evidence read |",
        "|---|---|---|---|",
    ]
    for row in data.rows:
        references = ", ".join(f"F{item}" for item in row.evidence_fact_ids) or "No linked facts"
        rows.append(f"| {cell(row.dimension)} | {cell(row.a_value)} | {cell(row.b_value)} "
                    f"| {row.verdict.replace('_', ' ')} · {references} |")

    evidence_links: dict[str, list[str]] = {product_a: [], product_b: []}
    for label, facts in ((product_a, facts_a), (product_b, facts_b)):
        seen: set[tuple[str, str]] = set()
        for fact in facts:
            field = cell(fact.get("field", "Observation"))
            value = cell(fact.get("value", ""))
            fact_id = fact.get("id")
            key = (field, value)
            if key in seen:
                continue
            seen.add(key)
            evidence_links[label].append(f"- **{field}:** {value}" + (f" (F{fact_id})" if fact_id else ""))
            if len(evidence_links[label]) >= 8:
                break

    notes = data.confidence_notes.strip() or "Confidence depends on the coverage and quality of the linked public sources."
    rationale = data.reasoning.strip() or "Dimensions are compared only where supplied evidence supports a side-by-side read."
    return (
        f"# {product_a} vs {product_b}\n\n"
        "## At a glance\n\n"
        "This report summarizes source-backed product information. It does not assign an overall winner; "
        "scores are shown only when the evidence supports a fair direct comparison.\n\n"
        "## Comparison\n\n" + "\n".join(rows) +
        f"\n\n## Evidence read\n\n{rationale}\n\n"
        f"### {product_a}\n\n" + ("\n".join(evidence_links[product_a]) or "No verified facts were collected.") +
        f"\n\n### {product_b}\n\n" + ("\n".join(evidence_links[product_b]) or "No verified facts were collected.") +
        f"\n\n## Confidence and limitations\n\n{notes}\n\n"
        "See the source cards and fact links above for the underlying public evidence."
    )
