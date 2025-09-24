## SWITCH edu-ID OIDC SSO (Backend-Native) – Implementation Plan

This guide adds backend-native Single Sign-On (SSO) using SWITCH edu-ID (eduid.ch) via OpenID Connect (OIDC). It replaces localStorage tokens with secure HttpOnly cookies and keeps existing role checks.

### Overview
- User clicks “Continue with SWITCH edu-ID”.
- FastAPI redirects to edu-ID OIDC authorize.
- edu-ID redirects to `/auth/sso/callback` with `code`.
- Backend exchanges `code`→tokens, validates ID token, maps/creates local `users` row.
- Backend issues short-lived access token as HttpOnly, Secure, SameSite=Lax cookie.
- SPA uses `fetch(..., { credentials: 'include' })`; no token in localStorage.

---

### 1) Prerequisites (edu-ID)
1. Register an OIDC client in the SWITCH AAI Resource Registry.
2. Collect the metadata from SWITCH (cannot be guessed):
   - Issuer / discovery URL specific to your client (use `https://issuer/.well-known/openid-configuration`).
   - `client_id` and `client_secret`.
   - Approved redirect URIs.
   - Attributes released (`email`, `name`, `sub` at minimum; request affiliations if you plan to map agencies automatically).
3. Add redirect URIs (prod and dev), e.g.:
   - `https://app.example.com/auth/sso/callback`
   - `https://dev.example.com/auth/sso/callback`
4. Scopes: `openid email profile` (add more later if needed).
5. Decide post-login provisioning strategy:
   - **Onboarding screen**: after first SSO login, prompt user to choose agency/role (stores selection in DB).
   - **Invite-based**: admins provision accounts ahead of time, SSO only verifies identity.
   - **Attribute mapping**: map edu-ID attributes to agency/role if SWITCH releases them.

---

### 2) Dependencies
Add to `requirements.txt`:
```text
authlib>=1.3.0
```

Rebuild images later with docker-compose.

---

### 3) Configuration
Extend your config (example YAML). Keep values in your secrets store or env vars in production.
```yaml
auth:
  secret_key: "CHANGE_ME"                 # already present
  algorithm: "HS256"                      # already present
  access_token_expire_minutes: 15          # short-lived

  oidc:
    issuer: "https://<edu-id-issuer>"     # use the issuer from edu-ID
    client_id: "<client_id>"
    client_secret: "<client_secret>"
    redirect_uri: "https://app.example.com/auth/sso/callback"
    scopes: "openid email profile"

    # Local provisioning defaults
    default_role: "viewer"
    default_company_id: "<agency-uuid>"

    # Cookie/session settings
    session_cookie_name: "elettra_access_token"
    cookie_domain: "example.com"          # omit if same host
    cookie_secure: true
    cookie_samesite: "lax"

cors:
  origins: ["https://app.example.com", "https://dev.example.com"]
```

Update `app/core/config.py` to load these fields (illustrative snippet – add alongside existing fields):
```python
# app/core/config.py (add fields into Settings)
from pydantic import Field
from pydantic.alias_generators import AliasPath
from typing import Optional

class Settings(BaseSettings):
    # ... existing fields ...

    oidc_issuer: str = Field(..., validation_alias=AliasPath("auth", "oidc", "issuer"))
    oidc_client_id: str = Field(..., validation_alias=AliasPath("auth", "oidc", "client_id"))
    oidc_client_secret: str = Field(..., validation_alias=AliasPath("auth", "oidc", "client_secret"))
    oidc_redirect_uri: str = Field(..., validation_alias=AliasPath("auth", "oidc", "redirect_uri"))
    oidc_scopes: str = Field(default="openid email profile", validation_alias=AliasPath("auth", "oidc", "scopes"))
    oidc_default_role: str = Field(default="viewer", validation_alias=AliasPath("auth", "oidc", "default_role"))
    oidc_default_company_id: str = Field(..., validation_alias=AliasPath("auth", "oidc", "default_company_id"))
    session_cookie_name: str = Field(default="elettra_access_token", validation_alias=AliasPath("auth", "oidc", "session_cookie_name"))
    cookie_domain: Optional[str] = Field(default=None, validation_alias=AliasPath("auth", "oidc", "cookie_domain"))
    cookie_secure: bool = Field(default=True, validation_alias=AliasPath("auth", "oidc", "cookie_secure"))
    cookie_samesite: str = Field(default="lax", validation_alias=AliasPath("auth", "oidc", "cookie_samesite"))
```

Optional env overrides (extend `override_env_map`):
```python
override_env_map.update({
    "OIDC_ISSUER": (["auth", "oidc", "issuer"], str),
    "OIDC_CLIENT_ID": (["auth", "oidc", "client_id"], str),
    "OIDC_CLIENT_SECRET": (["auth", "oidc", "client_secret"], str),
    "OIDC_REDIRECT_URI": (["auth", "oidc", "redirect_uri"], str),
    "OIDC_SCOPES": (["auth", "oidc", "scopes"], str),
    "OIDC_DEFAULT_ROLE": (["auth", "oidc", "default_role"], str),
    "OIDC_DEFAULT_COMPANY_ID": (["auth", "oidc", "default_company_id"], str),
    "OIDC_COOKIE_NAME": (["auth", "oidc", "session_cookie_name"], str),
    "OIDC_COOKIE_DOMAIN": (["auth", "oidc", "cookie_domain"], str),
    "OIDC_COOKIE_SECURE": (["auth", "oidc", "cookie_secure"], lambda s: s.lower() in ("1","true","yes","on")),
    "OIDC_COOKIE_SAMESITE": (["auth", "oidc", "cookie_samesite"], str),
})
```

---

### 4) Database mapping
Your `users` table requires `company_id`, `email`, `full_name`, `password_hash`, `role`.

First pass (no schema change):
- Identify users by email; JIT-provision if missing.
- Set `password_hash` to a random hash (unusable for password login).

Optional (recommended) – add OIDC linkage:
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_issuer text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oidc_sub text;
CREATE UNIQUE INDEX IF NOT EXISTS users_oidc_issuer_sub_udx ON users (oidc_issuer, oidc_sub);
```

---

### 5) Backend implementation (FastAPI + Authlib)
Register OIDC client (module-scope in `app/routers/auth.py`):
```python
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError
import secrets

from app.core.config import get_settings
from app.core.auth import create_access_token, get_password_hash
from app.database import get_async_session
from app.models import Users

router = APIRouter()
settings = get_settings()

oauth = OAuth()
oauth.register(
    name="eduid",
    server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    client_kwargs={"scope": settings.oidc_scopes},
)

@router.get("/sso/login")
async def sso_login(request: Request):
    return await oauth.eduid.authorize_redirect(request, settings.oidc_redirect_uri)

@router.get("/sso/callback")
async def sso_callback(request: Request, db: AsyncSession = Depends(get_async_session)):
    try:
        token = await oauth.eduid.authorize_access_token(request)
        userinfo = token.get("userinfo") or await oauth.eduid.parse_id_token(request, token)

        email = (userinfo.get("email") or "").lower().strip()
        name = userinfo.get("name") or userinfo.get("given_name") or email
        sub = userinfo.get("sub")
        if not email:
            raise HTTPException(status_code=400, detail="edu-ID did not return an email")

        # Find by OIDC (if columns exist) or fallback to email
        stmt = select(Users).where(Users.email == email)
        user = (await db.execute(stmt)).scalar_one_or_none()
        if not user:
            company_id = settings.oidc_default_company_id
            if not company_id:
                raise HTTPException(status_code=400, detail="Missing default company mapping for SSO users")

            user = Users(
                company_id=company_id,
                email=email,
                full_name=name,
                role=settings.oidc_default_role,
                password_hash=get_password_hash(secrets.token_hex(32)),
            )
            # If you added columns: user.oidc_issuer = settings.oidc_issuer; user.oidc_sub = sub
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # Issue our own short-lived access token (JWT) and set cookie
        access_token = create_access_token(data={"sub": str(user.id)})
        response = Response(status_code=302)
        response.headers["Location"] = "/"  # SPA landing page
        response.set_cookie(
            key=settings.session_cookie_name,
            value=access_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=(settings.cookie_samesite or "lax").capitalize(),
            path="/",
            domain=getattr(settings, "cookie_domain", None),
        )
        return response
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid ID token")

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        domain=getattr(settings, "cookie_domain", None),
    )
    return {"message": "Logged out"}
```

Accept cookie-based auth in dependency (`app/core/auth.py`):
```python
from fastapi import Request

async def verify_jwt_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_async_session)
) -> Users:
    token = None
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        token = request.cookies.get(get_settings().session_cookie_name)
    # ... keep the rest of the function as-is (decode JWT, load user) ...
```

No changes are needed to other routers because they already depend on `get_current_user`.

---

### 6) Frontend adjustments
- Remove JWT localStorage usage in `AuthContext.tsx` and stop sending `Authorization: Bearer` from the SPA.
- Use cookies by adding `credentials: 'include'` to fetch calls.
- Add a button that redirects to backend login:
```tsx
// On the login page button handler
window.location.href = joinUrl(getEffectiveBaseUrl(), '/auth/sso/login');
```
- After the callback, the cookie is set; SPA can navigate to the planner and call `/auth/me` with `credentials: 'include'`.

---

### 7) Security and proxy notes
- Enforce HTTPS and HSTS at the proxy.
- Keep `SameSite=Lax` for session cookie (works for top-level redirects from IdP).
- Restrict CORS to your SPA origin(s) and keep `allow_credentials=True`.
- Disable debug fallback CORS in production (`settings.debug=false`).
- Rate limit `/auth/login` and `/auth/sso/callback` at the proxy.

---

### 8) Testing
- Backend: `./run_tests.sh`. Add unit tests for cookie-based auth and SSO callback logic (mock Authlib).
- Manual E2E: Login via edu-ID dev client → redirected back → cookie set → `/auth/me` returns profile.
- Frontend: Verify auth-protected pages using MCP Playwright hitting `http://localhost:55557`.

---

### 9) Deployment
1. docker-compose build (backend changed) then up.
2. Confirm all services are healthy, backend on 8002, frontend 55557.
3. Verify `/auth/sso/login` performs redirect to edu-ID.
4. Verify cookie is `HttpOnly; Secure; SameSite=Lax` and API calls succeed without Authorization header.

---

### 10) Rollout & follow-ups
- Migrate early adopters; monitor logs and error rates.
- Optional: add refresh-token cookie + `/auth/refresh` for longer sessions.
- Optional: add `oidc_issuer`/`oidc_sub` columns for robust linking.
- Optional: add role/agency mapping rules by email domain or edu-ID attributes.


