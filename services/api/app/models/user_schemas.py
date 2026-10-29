"""Pydantic schemas for user registration, login, and profile."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_一-鿿]+$")
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    captchaId: str | None = Field(default=None, max_length=128)
    captchaCode: str | None = Field(default=None, max_length=16)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserInfoResponse(BaseModel):
    userId: str
    email: str
    username: str
    displayName: str | None
    ownerId: str
    defaultWorkspaceId: str
    role: str = "user"  # "user" | "admin" (Sprint-03 quota)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfoResponse


class RegisterPendingResponse(BaseModel):
    """Registration succeeds without a session; an admin must activate the account."""

    pendingActivation: bool = True
    message: str = "账号已创建，待管理员激活后方可登录"
    user: UserInfoResponse
