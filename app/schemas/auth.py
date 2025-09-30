# Authentication and user management schemas
from __future__ import annotations
from typing import Optional
from datetime import datetime
from uuid import UUID
import string
import re
import hashlib
from functools import lru_cache
import logging

import httpx
from pydantic import BaseModel, EmailStr, Field, field_validator
from enum import Enum
from password_strength import PasswordPolicy


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


# Define password policy
logger = logging.getLogger(__name__)

password_policy = PasswordPolicy.from_names(
    length=12,
    uppercase=1,
    numbers=1,
)

COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "123456",
    "123456789",
    "12345678",
    "qwerty",
    "abc123",
    "letmein",
    "111111",
    "123123",
    "000000",
    "iloveyou",
    "admin",
    "welcome",
    "dragon",
}

HIBP_RANGE_API = "https://api.pwnedpasswords.com/range/"


@lru_cache(maxsize=1024)
def _fetch_hibp_range(prefix: str) -> Optional[str]:
    try:
        response = httpx.get(
            f"{HIBP_RANGE_API}{prefix}",
            timeout=3.0,
            headers={"User-Agent": "ElettraBackend/PasswordCheck"},
        )
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        logger.warning("HIBP lookup failed for prefix %s: %s", prefix, exc)
        return None


def _is_compromised(password: str) -> bool:
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    data = _fetch_hibp_range(prefix)
    if not data:
        return False
    for line in data.splitlines():
        try:
            hash_suffix, _count = line.split(":")
        except ValueError:
            continue
        if hash_suffix == suffix:
            return True
    return False


def _has_sequential_patterns(password: str, length: int = 3) -> bool:
    lowered = password.lower()
    sequences = [string.ascii_lowercase, string.ascii_lowercase[::-1], string.digits, string.digits[::-1]]
    for seq in sequences:
        for i in range(len(seq) - length + 1):
            fragment = seq[i : i + length]
            if fragment in lowered:
                return True
    return False


def validate_password_strength(password: str) -> str:
    """Validate password strength using password-strength library"""
    if not password:
        raise ValueError("Password cannot be empty")
    errors = []

    # Check policy-defined requirements
    # Test password against policy
    policy_test = password_policy.test(password)
    if policy_test:
        for test in policy_test:
            test_name = test.name()
            if test_name == 'length':
                errors.append(f"Password must be at least {test.length} characters long")
            elif test_name == 'uppercase':
                errors.append(f"Password must contain at least {test.count} uppercase letter(s)")
            elif test_name == 'lowercase':
                errors.append(f"Password must contain at least {test.count} lowercase letter(s)")
            elif test_name == 'numbers':
                errors.append(f"Password must contain at least {test.count} digit(s)")
            elif test_name == 'special':
                errors.append(f"Password must contain at least {test.count} special character(s)")
            else:
                errors.append("Password does not meet required complexity")

    # Additional custom checks
    if not any(ch.islower() for ch in password):
        errors.append("Password must contain at least 1 lowercase letter")

    if password.lower() in COMMON_PASSWORDS:
        errors.append("Password is too common")

    if _is_compromised(password):
        errors.append("Password has appeared in known breaches")

    if _has_sequential_patterns(password):
        errors.append("Password cannot contain sequential characters like 'abc' or '123'")

    if re.search(r"(.)\\1\\1", password):
        errors.append("Password cannot contain three or more repeated characters in a row")

    if errors:
        raise ValueError(f"Password does not meet security requirements: {'; '.join(sorted(set(errors)))}")

    return password


class UserRegister(BaseModel):
    company_id: UUID
    email: EmailStr
    full_name: str
    password: str = Field(..., min_length=1, description="Password must meet security requirements")
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    """Schema for updating user profile information"""
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[RoleEnum] = None


class UserPasswordUpdate(BaseModel):
    """Schema for updating user password"""
    current_password: str
    new_password: str = Field(..., min_length=1, description="Password must meet security requirements")
    
    @field_validator('new_password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserProfileRead(BaseModel):
    """Schema for reading user profile (without password hash)"""
    id: UUID
    company_id: UUID
    email: str
    full_name: str
    role: str
    created_at: datetime


class PasswordBreachCheck(BaseModel):
    password: str = Field(..., min_length=1)


def is_password_compromised(password: str) -> bool:
    if not password:
        return False
    return _is_compromised(password)
