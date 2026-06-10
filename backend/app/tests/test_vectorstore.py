import pytest
from app.vectorstore.store_manager import VectorStoreManager, _get_store_executor
from app.core.vectorstore_utils import validate_filter


def test_filter_validation_nested_operators():
    """Verify filter validation supports nested operators."""
    # Valid nested filter
    valid_filter = {"page_number": {"$gte": 0, "$lte": 10}}
    is_valid, error = validate_filter(valid_filter)
    assert is_valid is True
    assert error is None

    # Valid $in operator
    valid_in = {"document_type": {"$in": ["invoice", "contract"]}}
    is_valid, error = validate_filter(valid_in)
    assert is_valid is True

    # Invalid operator should fail with descriptive message
    is_valid, error = validate_filter({"page_number": {"$regex": ".*"}})
    assert is_valid is False
    assert "Invalid filter operator" in (error or "")

    # Invalid top-level key should fail with descriptive message
    is_valid, error = validate_filter({"invalid_key": "value"})
    assert is_valid is False
    assert "Invalid filter keys" in (error or "")


def test_thread_pool_config():
    """Verify thread pool size is configurable."""
    executor1 = _get_store_executor()
    assert executor1._max_workers == 2  # Default

    executor2 = _get_store_executor(max_workers=4)
    assert executor2._max_workers == 4

    executor1.shutdown(wait=True)
    executor2.shutdown(wait=True)


def test_manager_shutdown():
    """Verify manager cleanup doesn't crash."""
    manager = object.__new__(VectorStoreManager)  # Skip __init__
    manager.shutdown()  # Should not crash even if not initialized
