"""
backend/app/schemas/auth.py
===========================
Pydantic schemas for OPERANTA authentication.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("A valid email address is required.")
        return value.lower()


class UserPublic(BaseModel):
    id: str
    merchant_id: str
    email: str
    display_name: Optional[str] = None
    role: str = "owner"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class RegisterRequest(BaseModel):
    """
    SaaS signup payload.

    - ``workspace_access_code`` blank -> create a new isolated merchant workspace.
    - ``workspace_access_code`` == server-configured demo code -> route to the
      existing Northstar demo workspace.
    - Any other non-empty code -> rejected as invalid (never silently create a
      different workspace).
    """
    business_name: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    confirm_password: str = Field(min_length=10, max_length=256)
    workspace_access_code: Optional[str] = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("A valid email address is required.")
        return value.lower()

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("Password must be at least 10 characters long.")
        return value.strip()

    @model_validator(mode="after")
    def _check_password_confirmation(self) -> "RegisterRequest":
        if self.password != self.confirm_password.strip():
            raise ValueError("Password confirmation does not match.")
        return self


class PayPalIntegrationStatus(BaseModel):
    """Public, secret-free view of a merchant's PayPal integration status."""
    merchant_id: str
    connected: bool
    environment: Optional[str] = None
    client_id_masked: Optional[str] = None
    webhook_id_masked: Optional[str] = None
    verified_at: Optional[str] = None


class PayPalIntegrationUpsert(BaseModel):
    """Payload to connect / update a merchant's PayPal Sandbox integration."""
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1, max_length=512)
    environment: str = Field(default="sandbox", pattern="^(sandbox|live)$")
    webhook_id: Optional[str] = Field(default=None, max_length=128)
