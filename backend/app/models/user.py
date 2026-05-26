# backend/app/models/user.py
"""User-related Pydantic models and enums."""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator


class UserRole(str, Enum):
    """
    Application-level role enumeration for RBAC.
    Synced with DB enum via app.database.enums.user_role_enum.
    """
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    GUEST = "guest"
    
    @classmethod
    def default(cls) -> str:
        return cls.VIEWER.value
    
    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {role.value for role in cls}


# -- Pydantic Schemas ------------------------------------------

class UserBase(BaseModel):
    email: EmailStr
    display_name: Optional[str] = Field(None, max_length=100)
    
    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v.strip()) < 2:
            raise ValueError("display_name must be at least 2 characters")
        return v.strip() if v else v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    role: UserRole = Field(default=UserRole.VIEWER)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(UserBase):
    user_id: str
    role: UserRole
    is_active: bool
    is_email_verified: bool
    workspace_id: Optional[str] = None
    
    class Config:
        from_attributes = True
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

