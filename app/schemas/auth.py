# Authentication and user management schemas
from __future__ import annotations
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr
from enum import Enum


class UserLogin(BaseModel):
    # Accept any string; invalid formats will be treated as auth failure (401)
    email: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class LogoutResponse(BaseModel):
    message: str


class RoleEnum(str, Enum):
    admin = "admin"
    analyst = "analyst"
    viewer = "viewer"


class UserRegister(BaseModel):
    company_id: UUID
    email: EmailStr
    full_name: str
    password: str
    role: RoleEnum
