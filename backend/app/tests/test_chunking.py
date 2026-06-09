import pytest
from app.chunking.parent_child import (
    ParentChildChunker,
    MockBlock,
    MockOCRResult,
    MockEnriched,
)
from app.core.exceptions import ValidationError


def test_chunk_text_only_basic():
    """Verify basic chunking works with plain text."""
    chunker = ParentChildChunker()
    text = "This is a test. " * 20  # ~300 chars

    children, parents = chunker.chunk_text_only(
        text=text,
        source_file="test.txt",
        page_num=0,
        block_type="paragraph",
    )

    assert len(parents) == 1
    assert len(children) >= 1
    assert all(c.metadata["chunk_type"] == "child" for c in children)
    assert all(p.metadata["chunk_type"] == "parent" for p in parents)
    assert all(c.metadata["parent_id"] for c in children)  # Linked to parent


def test_chunk_validation_errors():
    """Verify validation catches invalid inputs."""
    chunker = ParentChildChunker()

    # Empty text
    with pytest.raises(ValidationError, match="empty"):
        chunker.chunk_text_only(text="", source_file="test.txt")

    # Invalid enriched document
    with pytest.raises(ValidationError, match="missing ocr_result"):
        chunker.chunk_enriched_document(enriched=None, source_file="test.pdf")  # type: ignore


def test_chunk_size_validation():
    """Verify chunk size config validation."""
    assert ParentChildChunker.validate_chunk_sizes(
        child_size=400, child_overlap=50, parent_size=2000, parent_overlap=200
    ) == (True, None)

    # Child >= parent should fail
    valid, msg = ParentChildChunker.validate_chunk_sizes(
        child_size=2000, child_overlap=50, parent_size=2000, parent_overlap=200
    )
    assert not valid
    assert "must be < parent size" in msg


def test_memory_guard():
    """Verify max_blocks limits processing."""
    chunker = ParentChildChunker()

    # Create mock with many blocks
    blocks = [MockBlock(text=f"Block {i} " * 10) for i in range(100)]
    mock_ocr = MockOCRResult(blocks=blocks)
    mock_enriched = MockEnriched(ocr_result=mock_ocr)

    # Process with limit
    children, parents = chunker.chunk_enriched_document(
        enriched=mock_enriched,  # type: ignore
        source_file="large.pdf",
        max_blocks=10,
    )

    # Should only process 10 blocks
    assert len(parents) <= 10
