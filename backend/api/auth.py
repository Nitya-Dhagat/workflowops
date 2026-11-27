"""Authentication: salted scrypt password hashes, short-lived HS256 JWTs, role dependencies.

Identity always comes from the verified token, never from request bodies.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.api.deps import SessionDep
from backend.database.models import User
from backend.domain.enums import UserRole
from backend.security.passwords import verify_password_async

TOKEN_TTL = timedelta(hours=12)
ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------ tokens
def jwt_secret(request: Request) -> str:
    secret: str = request.app.state.jwt_secret
    return secret


def create_token(user: User, secret: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims = {"sub": user.id, "role": user.role, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


class Principal(BaseModel):
    id: str
    name: str
    role: UserRole
    department: str


async def current_user(
    request: Request,
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims: dict[str, Any] = jwt.decode(
            creds.credentials,
            jwt_secret(request),
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, "session expired; sign in again") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, "invalid token") from e
    user = await session.get(User, claims["sub"])
    if user is None or not user.active:  # role and status are re-read, not trusted from token
        raise HTTPException(401, "account is not active")
    return Principal(
        id=user.id, name=user.name, role=UserRole(user.role), department=user.department
    )


async def require_reviewer(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    if user.role is not UserRole.REVIEWER:
        raise HTTPException(403, "only reviewers can decide approvals")
    return user


CurrentUser = Annotated[Principal, Depends(current_user)]
Reviewer = Annotated[Principal, Depends(require_reviewer)]

# ------------------------------------------------------------------ routes
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Principal


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, session: SessionDep) -> LoginOut:
    user = await session.get(User, body.user_id)
    # Same error for unknown user and wrong password, so accounts cannot be enumerated.
    if (
        user is None
        or not user.active
        or not await verify_password_async(body.password, user.password_hash)
    ):
        raise HTTPException(401, "wrong user or password")
    principal = Principal(
        id=user.id, name=user.name, role=UserRole(user.role), department=user.department
    )
    return LoginOut(access_token=create_token(user, jwt_secret(request)), user=principal)


@router.get("/me", response_model=Principal)
async def me(user: CurrentUser) -> Principal:
    return user


@router.get("/demo-users")
async def demo_users(session: SessionDep) -> list[dict[str, str]]:
    """Active seeded accounts for the demo login picker (synthetic users only)."""
    rows = await session.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
    return [
        {"id": u.id, "name": u.name, "role": u.role}
        for u in rows
        if u.role in {UserRole.REQUESTER, UserRole.REVIEWER} and u.id in DEMO_ACCOUNTS
    ]


DEMO_ACCOUNTS = ("u_01", "u_02", "u_03", "u_10", "u_11", "u_12")
