import pytest
from app.chunking.parent_child import (
    ParentChildChunker,
    MockBlock,
    MockOCRResult,
    MockEnriched,
)
from app.core.exceptions import ValidationError


@pytest.mark.asyncio
async def test_chunk_text_only_basic():
    """Verify basic chunking works with plain text (async generator)."""
    chunker = ParentChildChunker()
    text = "This is a test. " * 20  # ~300 chars

    children, parents = [], []
    async for child, parent in chunker.chunk_text_only(
        text=text,
        source_file="test.txt",
        page_num=0,
        block_type="paragraph",
    ):
        children.append(child)
        parents.append(parent)

    assert len(parents) >= 1
    assert len(children) >= 1
    assert all(c.metadata["chunk_type"] == "child" for c in children)
    assert all(p.metadata["chunk_type"] == "parent" for p in parents)
    assert all(c.metadata["parent_id"] for c in children)  # Linked to parent


def test_chunk_validation_errors():
    """Verify validation catches invalid inputs (sync raises before iteration)."""
    chunker = ParentChildChunker()

    # chunk_text_only raises synchronously for empty text (before returning generator)
    with pytest.raises(ValidationError, match="empty"):
        chunker.chunk_text_only(text="", source_file="test.txt")


@pytest.mark.asyncio
async def test_chunk_enriched_validation():
    """Verify chunk_enriched_document raises for invalid enriched doc."""
    chunker = ParentChildChunker()

    # None enriched raises ValidationError during async iteration
    with pytest.raises(ValidationError, match="missing ocr_result"):
        async for _ in chunker.chunk_enriched_document(
            enriched=None, source_file="test.pdf"  # type: ignore
        ):
            pass


def test_chunk_size_validation():
    """Verify chunk size config validation (private static method)."""
    # Valid config
    is_valid, msg = ParentChildChunker._validate_chunk_sizes(
        child_size=400, child_overlap=50, parent_size=2000, parent_overlap=200
    )
    assert is_valid is True
    assert msg is None

    # Child >= parent should fail
    is_valid, msg = ParentChildChunker._validate_chunk_sizes(
        child_size=2000, child_overlap=50, parent_size=2000, parent_overlap=200
    )
    assert not is_valid
    assert msg is not None
    assert "must be < parent size" in msg


@pytest.mark.asyncio
async def test_memory_guard():
    """Verify max_blocks instance limit controls processing."""
    chunker = ParentChildChunker()

    # Create a mock with many blocks — the chunker's built-in max_blocks guard kicks in
    blocks = [MockBlock(text=f"Block {i} " * 10) for i in range(200)]
    mock_ocr = MockOCRResult(blocks=blocks)
    mock_enriched = MockEnriched(ocr_result=mock_ocr)

    parents_seen = []
    async for _child, parent in chunker.chunk_enriched_document(
        enriched=mock_enriched,  # type: ignore
        source_file="large.pdf",
    ):
        parents_seen.append(parent)

    # Should produce results bounded by the chunker's max_blocks config
    assert len(parents_seen) >= 1
    assert len(parents_seen) <= chunker.max_blocks + 1  # Bounded by guard
