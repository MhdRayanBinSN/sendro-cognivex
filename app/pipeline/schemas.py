"""Shared Pydantic contracts for pipeline stages."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Candidate(BaseModel):
    name: str
    url: HttpUrl
    description: str = ""
    launch_signal: str = ""
    launch_evidence: str = Field(default="", max_length=400)
    confidence_new: float = Field(ge=0, le=1)
    is_recent_launch: bool = True
    is_product_site: bool
    source_urls: list[HttpUrl] = Field(default_factory=list)


class URLInfo(BaseModel):
    url: HttpUrl
    path: str
    depth: int = Field(ge=0)
    title: str | None = None
    anchor_text: str | None = None
    group: str = ""


class PagePlan(BaseModel):
    url: HttpUrl
    purpose: str
    reason: str
    priority: int = Field(ge=0, le=100)
    screenshot: bool = False


class PageSelection(BaseModel):
    reasoning: str
    pages: list[PagePlan]
    uncovered_topics: list[str] = Field(default_factory=list)


class ExtractedFact(BaseModel):
    field: str
    value: str
    source_url: HttpUrl
    evidence_quote: str
    kind: Literal["fact", "claim"] = "fact"


class FactExtraction(BaseModel):
    reasoning: str
    facts: list[ExtractedFact]
    unknowns: list[str] = Field(default_factory=list)


class PairSelection(BaseModel):
    reasoning: str
    product_a: str
    product_b: str
    comparability: float = Field(ge=0, le=1)
    backups: list[str] = Field(default_factory=list)


class ComparisonRow(BaseModel):
    dimension: str
    a_value: str
    b_value: str
    score_a: float | None = Field(default=None, ge=0, le=100)
    score_b: float | None = Field(default=None, ge=0, le=100)
    verdict: Literal["a", "b", "tie", "unknown", "not_comparable"]
    evidence_fact_ids: list[int]


class ComparisonData(BaseModel):
    reasoning: str
    rows: list[ComparisonRow]
    confidence_notes: str


class WrittenReport(BaseModel):
    reasoning: str
    markdown: str


class VerificationIssue(BaseModel):
    statement: str
    reason: str
    action: Literal["remove", "soften"]


class VerificationResult(BaseModel):
    reasoning: str
    issues: list[VerificationIssue]
