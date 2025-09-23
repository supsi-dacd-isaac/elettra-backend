# Authentication and user management schemas
from __future__ import annotations
from typing import Optional
from datetime import datetime
from uuid import UUID
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
password_policy = PasswordPolicy.from_names(
    length=8,        # min length: 8
    uppercase=1,     # need min. 1 uppercase letters
    numbers=1,       # need min. 1 digits
    special=1,       # need min. 1 special characters
)


def validate_password_strength(password: str) -> str:
    """Validate password strength using password-strength library"""
    if not password:
        raise ValueError("Password cannot be empty")
    
    # Test password against policy
    policy_test = password_policy.test(password)
    if policy_test:
        # Convert policy test results to human-readable errors
        errors = []
        for test in policy_test:
            test_name = test.name()
            if test_name == 'length':
                errors.append(f"Password must be at least {test.length} characters long")
            elif test_name == 'uppercase':
                errors.append(f"Password must contain at least {test.count} uppercase letter(s)")
            elif test_name == 'numbers':
                errors.append(f"Password must contain at least {test.count} digit(s)")
            elif test_name == 'special':
                errors.append(f"Password must contain at least {test.count} special character(s)")
        
        raise ValueError(f"Password does not meet security requirements: {'; '.join(errors)}")
    
    return password


class UserRegister(BaseModel):
    company_id: UUID
    email: EmailStr
    full_name: str
    password: str = Field(..., min_length=1, description="Password must meet security requirements")
    role: RoleEnum
    
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
