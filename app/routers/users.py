"""
User management endpoints
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from uuid import UUID

from app.database import get_db, User
from app.schemas import UserResponse, UserCreate
from app.core.auth import verify_jwt_token, require_role

router = APIRouter()

@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: User = Depends(verify_jwt_token)):
    """
    Get current authenticated user information
    """
    return UserResponse.from_orm(current_user)

@router.get("/", response_model=List[UserResponse])
async def get_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of users (admin only)
    
    - **skip**: Number of records to skip
    - **limit**: Maximum number of records to return
    """
    result = await db.execute(
        select(User)
        .where(User.company_id == current_user.company_id)
        .offset(skip)
        .limit(limit)
    )
    users = result.scalars().all()
    return [UserResponse.from_orm(user) for user in users]

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Get specific user by ID (admin only)
    """
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.company_id == current_user.company_id
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return UserResponse.from_orm(user)

@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete user (admin only)
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    result = await db.execute(
        delete(User).where(
            User.id == user_id,
            User.company_id == current_user.company_id
        )
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    await db.commit()
    return {"message": "User deleted successfully"} 