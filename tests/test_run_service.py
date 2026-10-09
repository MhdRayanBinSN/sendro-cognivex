import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import Comparison, Product, Run, StageLog
from app.pipeline.schemas import Candidate, ComparisonData, ComparisonRow
from app.pipeline.validate import ValidationResult
import app.run_service as service


@pytest.mark.asyncio
async def test_pipeline_completes_with_injected_stage_adapters(monkeypatch):
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(service, "engine", test_engine)

    candidates = [Candidate(name=f"Product {i}", url=f"https://product{i}.example.test/",
                            description="Research product", launch_signal="Recently launched",
                            confidence_new=0.9, is_product_site=True,
                            source_urls=[f"https://source.example.test/{i}"]) for i in range(6)]

    async def fake_discover(*args, **kwargs):
        return candidates, ["recent launch query"]

    async def fake_validate(candidate, fetcher):
        return ValidationResult(candidate, True, "validated", str(candidate.url), 500)

    async def fake_select(validated, seen_domains, **kwargs):
        return validated[0], validated[1], validated[2:], "Same category and launch window"

    async def fake_research(candidate, category, run_id, session, fetcher):
        product = service._upsert_product(session, candidate)
        return {"product_id": product.id, "candidate": candidate.model_dump(mode="json"),
                "facts": [{"id": product.id, "field": "features", "value": "Example capability",
                           "source_url": str(candidate.url), "evidence_quote": "Example capability",
                           "kind": "fact"}], "screenshot_count": 3,
                "screenshot_errors": [], "uncovered_topics": []}

    async def fake_screenshot(product, run_id, session, fetcher):
        return {**product, "screenshot_count": 3, "screenshot_errors": [], "screenshot_checks": []}

    async def fake_site_metrics(candidates):
        return [{"product": item.name, "homepage_url": str(item.url), "github": None,
                 "pagespeed": None, "errors": []} for item in candidates]

    async def fake_compare(name_a, facts_a, name_b, facts_b, **kwargs):
        return ComparisonData(
            reasoning="Comparable evidence was available",
            rows=[ComparisonRow(dimension="Features", a_value="Strong", b_value="Moderate",
                                score_a=80, score_b=60, verdict="a", evidence_fact_ids=[1])],
            confidence_notes="Both have sourced observations",
        ), "# Two product comparison\n"

    monkeypatch.setattr(service, "configured_search_provider", lambda: object())
    monkeypatch.setattr(service, "discover", fake_discover)
    monkeypatch.setattr(service, "validate_candidate", fake_validate)
    monkeypatch.setattr(service, "select_pair", fake_select)
    monkeypatch.setattr(service, "_research_product", fake_research)
    monkeypatch.setattr(service, "_capture_product_screenshots", fake_screenshot)
    monkeypatch.setattr(service, "collect_site_metrics", fake_site_metrics)
    monkeypatch.setattr(service, "compare_products", fake_compare)

    with Session(test_engine) as session:
        run = Run(category="test category")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    await service.run_pipeline(run_id)
    with Session(test_engine) as session:
        run = session.get(Run, run_id)
        assert run.status == "completed", run.error
        assert session.exec(select(Comparison).where(Comparison.run_id == run_id)).first()
        comparison = session.exec(select(Comparison).where(Comparison.run_id == run_id)).first()
        assert comparison and "Evidence-backed benchmarks" in comparison.html
        assert len(session.exec(select(StageLog).where(StageLog.run_id == run_id)).all()) == 7
        assert len(session.exec(select(Product)).all()) == 2


@pytest.mark.asyncio
async def test_pipeline_marks_insufficient_candidates_partial_without_crashing(monkeypatch):
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(service, "engine", test_engine)
    candidate = Candidate(name="Only Product", url="https://only-product.example.test/",
                          description="Research product", launch_signal="Recently launched",
                          confidence_new=0.9, is_product_site=True,
                          source_urls=["https://source.example.test/only-product"])

    async def fake_discover(*args, **kwargs):
        return [candidate], ["recent launch query"]

    async def fake_validate(candidate, fetcher):
        return ValidationResult(candidate, True, "validated", str(candidate.url), 500)

    async def unexpected_select(*args, **kwargs):
        raise AssertionError("Pair selection must be skipped when fewer than two candidates are available")

    monkeypatch.setattr(service, "configured_search_provider", lambda: object())
    monkeypatch.setattr(service, "discover", fake_discover)
    monkeypatch.setattr(service, "validate_candidate", fake_validate)
    monkeypatch.setattr(service, "select_pair", unexpected_select)

    with Session(test_engine) as session:
        run = Run(category="test category")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    await service.run_pipeline(run_id)

    with Session(test_engine) as session:
        run = session.get(Run, run_id)
        assert run.status == "partial"
        assert "At least two new, validated product sites" in run.error
        assert "Found 1" in run.error
        assert session.exec(select(Comparison).where(Comparison.run_id == run_id)).first() is None
        logs = session.exec(select(StageLog).where(StageLog.run_id == run_id)).all()
        assert len(logs) == 3
        assert [log.status for log in logs] == ["completed", "completed", "failed"]
