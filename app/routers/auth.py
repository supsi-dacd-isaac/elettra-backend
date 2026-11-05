from fastapi import APIRouter, Depends, HTTPException, Request, status, Response
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from datetime import timedelta

from app.database import get_async_session
from app.schemas.auth import (
    UserLogin,
    Token,
    UserRegister,
    LogoutResponse,
    UserUpdate,
    UserPasswordUpdate,
    UserProfileRead,
    validate_password_breach,
)
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
from app.core.rate_limit import SlidingWindowRateLimiter

router = APIRouter()
settings = get_settings()

_login_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
_register_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=3600)
_email_check_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60)


def _normalized_email(value: str) -> str:
    return value.strip().casefold()


async def _email_exists(db: AsyncSession, email: str, *, exclude_user_id=None) -> bool:
    query = select(Users.id).where(func.lower(Users.email) == _normalized_email(email))
    if exclude_user_id is not None:
        query = query.where(Users.id != exclude_user_id)
    return await db.scalar(query.limit(1)) is not None

@router.get("/check-email/{email}")
async def check_email_availability(email: str, request: Request):
    """Accept an availability probe without disclosing account existence."""
    _email_check_limiter.enforce(request, subject=email)
    return {"status": "accepted"}

@router.post("/register", response_model=UsersRead)
async def register(
    user_data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """Register a new user"""
    normalized_email = _normalized_email(str(user_data.email))
    _register_limiter.enforce(request, subject=normalized_email)
    try:
        await validate_password_breach(user_data.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if await _email_exists(db, normalized_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration could not be completed",
        )

    # Create new user
    hashed_password = get_password_hash(user_data.password)
    db_user = Users(
        company_id=user_data.company_id,
        email=normalized_email,
        full_name=user_data.full_name,
        password_hash=hashed_password,
        role="analyst"  # Automatically set all new users to analyst
    )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.post("/login", response_model=Token)
async def login(
    user_credentials: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """Authenticate user and return access token"""
    _login_limiter.enforce(request, subject=user_credentials.email)
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
    if "email" in update_data:
        update_data["email"] = _normalized_email(str(update_data["email"]))
    if 'email' in update_data and update_data['email'] != current_user.email:
        if await _email_exists(db, update_data['email'], exclude_user_id=current_user.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Profile update could not be completed",
            )
    
    # Keep the self-service profile boundary explicit. Authorization fields are
    # managed only through the agency administration API.
    for field, value in update_data.items():
        if field not in {"email", "full_name"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Profile field '{field}' cannot be changed",
            )
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
    
    try:
        await validate_password_breach(password_update.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Local validation is handled by Pydantic; breach validation is async.
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
