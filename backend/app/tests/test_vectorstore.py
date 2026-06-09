import pytest
from app.vectorstore.chroma_store import ChromaVectorStore
from app.vectorstore.store_manager import VectorStoreManager, _get_store_executor


def test_filter_validation_nested_operators():
    """Verify filter validation supports nested operators."""
    # Valid nested filter
    valid_filter = {"page_number": {"$gte": 0, "$lte": 10}}
    result = ChromaVectorStore._validate_filter(valid_filter)
    assert result == valid_filter

    # Valid $in operator
    valid_in = {"document_type": {"$in": ["invoice", "contract"]}}
    result = ChromaVectorStore._validate_filter(valid_in)
    assert result == valid_in

    # Invalid operator should raise
    with pytest.raises(ValueError, match="Invalid filter operator"):
        ChromaVectorStore._validate_filter({"page_number": {"$regex": ".*"}})

    # Invalid top-level key should raise
    with pytest.raises(ValueError, match="Invalid filter keys"):
        ChromaVectorStore._validate_filter({"invalid_key": "value"})


def test_thread_pool_config():
    """Verify thread pool size is configurable."""
    # Test default
    executor1 = _get_store_executor()
    assert executor1._max_workers == 2  # Default

    # Test custom size
    executor2 = _get_store_executor(max_workers=4)
    assert executor2._max_workers == 4

    # Clean up
    executor1.shutdown(wait=True)
    executor2.shutdown(wait=True)


def test_manager_shutdown():
    """Verify manager cleanup doesn't crash."""
    manager = object.__new__(VectorStoreManager)  # Skip __init__
    manager.shutdown()  # Should not crash even if not initialized
