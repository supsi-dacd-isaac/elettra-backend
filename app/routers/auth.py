from datetime import timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from authlib.integrations.starlette_client import OAuth
from jose import JWTError

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

oauth = OAuth()
if settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret and settings.oidc_redirect_uri:
    oauth.register(
        name="eduid",
        server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": settings.oidc_scopes},
    )

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


@router.get("/sso/login")
async def sso_login(request: Request):
    if "eduid" not in oauth:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC login not configured")
    return await oauth.eduid.authorize_redirect(request, settings.oidc_redirect_uri)


@router.get("/sso/callback")
async def sso_callback(request: Request, db: AsyncSession = Depends(get_async_session)):
    if "eduid" not in oauth:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OIDC login not configured")
    try:
        token = await oauth.eduid.authorize_access_token(request)
        userinfo = token.get("userinfo") or await oauth.eduid.parse_id_token(request, token)

        email = (userinfo.get("email") or "").lower().strip()
        name = userinfo.get("name") or userinfo.get("given_name") or email
        if not email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="edu-ID did not return an email")

        result = await db.execute(select(Users).where(Users.email == email))
        user = result.scalar_one_or_none()
        if not user:
            company_id = settings.oidc_default_company_id
            if not company_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing company mapping for SSO user")

            user = Users(
                company_id=company_id,
                email=email,
                full_name=name,
                role=settings.oidc_default_role,
                password_hash=get_password_hash(secrets.token_hex(32)),
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # Issue cookie
        access_token = create_access_token(data={"sub": str(user.id)})
        response = Response(status_code=status.HTTP_302_FOUND)
        response.headers["Location"] = "/"
        cookie_kwargs = {
            "key": settings.session_cookie_name,
            "value": access_token,
            "httponly": True,
            "secure": settings.cookie_secure,
            "samesite": (settings.cookie_samesite or "lax").capitalize(),
            "path": "/",
        }
        if settings.cookie_domain:
            cookie_kwargs["domain"] = settings.cookie_domain
        response.set_cookie(**cookie_kwargs)
        return response
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ID token") from exc

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
async def logout(response: Response):
    """Logout user by clearing auth cookie"""
    delete_kwargs = {
        "key": settings.session_cookie_name,
        "path": "/",
    }
    if settings.cookie_domain:
        delete_kwargs["domain"] = settings.cookie_domain
    response.delete_cookie(**delete_kwargs)
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
