import pytest
from pydantic import ValidationError
from app.models.requests import QueryRequest, IngestRequest
from app.models.responses import CitationModel


def test_query_request_validation():
    """Verify QueryRequest validates correctly."""
    # Valid request
    req = QueryRequest(question="What is revenue?")
    assert req.question == "What is revenue?"
    assert req.stream is True  # default

    # Invalid: empty question
    with pytest.raises(ValidationError, match="Question cannot be empty"):
        QueryRequest(question="   ")

    # Invalid: bad session_id
    with pytest.raises(ValidationError, match="session_id may contain only"):
        QueryRequest(question="test", session_id="bad@id")

    # Invalid: page range logic
    with pytest.raises(ValidationError, match="start.*must be <= end"):
        QueryRequest(question="test", filter_page_range=[10, 5])


def test_ingest_request_tag_validation():
    """Verify tag validation catches invalid formats."""
    # Valid tags
    req = IngestRequest(tags=["invoice", "Q3-2024", "finance_report"])
    assert len(req.tags) == 3

    # Invalid: special characters
    with pytest.raises(ValidationError, match="contains invalid characters"):
        IngestRequest(tags=["bad@tag"])

    # Invalid: too many tags
    with pytest.raises(ValidationError, match="Maximum 10 tags"):
        IngestRequest(tags=[f"tag{i}" for i in range(11)])


def test_citation_model_conversion():
    """Verify CitationModel.from_citation() works with typed input."""
    # Mock internal Citation dataclass
    from dataclasses import dataclass

    @dataclass
    class MockCitation:
        source_file: str
        page_number: int
        block_type: str
        chunk_text: str
        rerank_score: float

    internal = MockCitation(
        source_file="test.pdf",
        page_number=3,
        block_type="table",
        chunk_text="Revenue: $1M" * 100,  # Long text
        rerank_score=0.89456,
    )

    # Convert to API model
    api_citation = CitationModel.from_citation(internal)  # type: ignore

    assert api_citation.page_display == 4  # 3 + 1
    assert len(api_citation.chunk_text) <= 300  # Truncated
    assert api_citation.rerank_score == 0.8946  # Rounded to 4 decimals


def test_filter_dict_typing():
    """Verify build_filter_dict returns typed filter dict."""
    req = QueryRequest(
        question="test",
        filter_source_file="invoice.pdf",
        filter_page_range=[0, 10],
    )
    filters = req.build_filter_dict()

    assert filters is not None
    assert filters["source_file"] == "invoice.pdf"
    assert filters["page_number"] == {"$gte": 0, "$lte": 10}
    # Verify operator keys are Literal types (runtime check via dict access)
    assert "$gte" in filters["page_number"]
