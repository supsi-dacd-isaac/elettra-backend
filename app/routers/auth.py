"""
Authentication endpoints
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, User
from app.schemas import UserLogin, Token, UserResponse
from app.core.auth import verify_password, create_access_token, verify_jwt_token
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()

@router.post("/login", response_model=Token)
async def login(
    user_credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    """
    Login endpoint for user authentication
    
    **Example Usage:**
    ```json
    {
        "email": "analyst@company.com",
        "password": "securepassword"
    }
    ```
    """
    
    # Debug: Print the email we're looking for
    print(f"DEBUG: Looking for user with email: {user_credentials.email}")
    
    # Find user by email
    result = await db.execute(select(User).where(User.email == user_credentials.email))
    user = result.scalar_one_or_none()
    
    # Debug: Check if user was found
    if user:
        print(f"DEBUG: User found - ID: {user.id}, Email: {user.email}")
        print(f"DEBUG: Stored hash: {user.password_hash}")
        print(f"DEBUG: Provided password: {user_credentials.password}")
        
        # Test password verification
        password_valid = verify_password(user_credentials.password, user.password_hash)
        print(f"DEBUG: Password verification result: {password_valid}")
    else:
        print("DEBUG: No user found with that email")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not password_valid:
        print("DEBUG: Password verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role},
        expires_delta=access_token_expires
    )
    
    return Token(
        access_token=access_token,
        user=UserResponse.from_orm(user)
    )

@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: User = Depends(verify_jwt_token)):
    """Get current authenticated user information"""
    return UserResponse.from_orm(current_user) 