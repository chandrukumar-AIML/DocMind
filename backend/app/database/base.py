# backend/app/database/base.py
"""Base model and metadata for SQLAlchemy."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, declared_attr
from sqlalchemy import MetaData

# DVMELTSS-S: Consistent naming convention for all tables
metadata: MetaData = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 declarative base with common utilities.
    
    All models should inherit from this class.
    """
    metadata = metadata
    
    @declared_attr.directive
    def __tablename__(cls) -> str:
        """Auto-generate table names from class names (snake_case)."""
        import re
        name = cls.__name__
        # Convert CamelCase to snake_case
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    
    def to_dict(self, exclude: set[str] | None = None) -> dict:
        """Convert model instance to dictionary."""
        exclude = exclude or set()
        return {
            c.key: getattr(self, c.key)
            for c in self.__table__.columns
            if c.key not in exclude
        }
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

