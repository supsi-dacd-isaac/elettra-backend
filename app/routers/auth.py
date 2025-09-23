from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import timedelta

from app.database import get_async_session
from app.schemas.auth import UserLogin, Token, UserRegister, LogoutResponse, UserUpdate, UserPasswordUpdate, UserProfileRead
from app.schemas.database import UsersRead
from app.models import Users
from app.core.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    get_current_user,
    verify_password
)
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()

@router.get("/check-email/{email}")
async def check_email_availability(email: str, db: AsyncSession = Depends(get_async_session)):
    """Check if an email is available for registration"""
    result = await db.execute(select(Users).where(Users.email == email))
    existing_user = result.scalar_one_or_none()
    
    return {"available": existing_user is None}

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

@router.get("/me", response_model=UsersRead)
async def read_users_me(current_user: Users = Depends(get_current_user)):
    """Get current user information"""
    return current_user

@router.put("/me", response_model=UserProfileRead)
async def update_user_profile(
    user_update: UserUpdate, 
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user)
):
    """Update current user profile information"""
    update_data = user_update.model_dump(exclude_unset=True)
    
    # Check if email is being changed and if it already exists
    if 'email' in update_data and update_data['email'] != current_user.email:
        result = await db.execute(select(Users).where(Users.email == update_data['email']))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
    
    # Update user fields
    for field, value in update_data.items():
        setattr(current_user, field, value)
    
    await db.commit()
    await db.refresh(current_user)
    
    return UserProfileRead(
        id=current_user.id,
        company_id=current_user.company_id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        created_at=current_user.created_at
    )

@router.put("/me/password")
async def update_user_password(
    password_update: UserPasswordUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user)
):
    """Update current user password"""
    # Verify current password
    if not verify_password(password_update.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Update password (validation is handled by Pydantic)
    current_user.password_hash = get_password_hash(password_update.new_password)
    await db.commit()
    
    return {"message": "Password updated successfully"}


@router.post("/logout", response_model=LogoutResponse)
async def logout(current_user: Users = Depends(get_current_user)):
    """Logout user (client should remove token from storage)"""
    return {"message": "Successfully logged out"}

@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user)
):
    """Delete the current user account.

    Note: This will fail with 409 if the user has related records due to FK constraints.
    """
    try:
        await db.delete(current_user)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete user due to existing references"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
