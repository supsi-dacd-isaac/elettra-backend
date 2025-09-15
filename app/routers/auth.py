from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta

from app.database import get_async_session
from app.schemas import UserLogin, Token, UserRegister, UsersRead, LogoutResponse
from app.models import Users
from app.core.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    get_current_user
)
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()

@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin, db: AsyncSession = Depends(get_async_session)):
    """Authenticate user and return access token"""
    user = await authenticate_user(user_credentials.email, user_credentials.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/register", response_model=UsersRead)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_async_session)):
    """Register a new user"""
    # Check if user already exists
    result = await db.execute(select(Users).where(Users.email == user_data.email))
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create new user
    hashed_password = get_password_hash(user_data.password)
    db_user = Users(
        company_id=user_data.company_id,
        email=user_data.email,
        full_name=user_data.full_name,
        password_hash=hashed_password,
        role=user_data.role
    )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.get("/me", response_model=UsersRead)
async def read_users_me(current_user: Users = Depends(get_current_user)):
    """Get current user information"""
    return current_user

@router.post("/logout", response_model=LogoutResponse)
async def logout(current_user: Users = Depends(get_current_user)):
    """Logout user (client should remove token from storage)"""
    return {"message": "Successfully logged out"}
