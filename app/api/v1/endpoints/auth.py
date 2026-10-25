from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.core.exceptions import ConflictException, EntityNotFoundException
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_roles,
    AuthenticatedUser,
)
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import (
    UserLoginRequest,
    TokenResponse,
    UserCreateRequest,
    UserRead,
)
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/auth", tags=["Authentication & Users"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and issue JWT access token",
    description="Validates email and password against bcrypt hash. Returns JWT signed access token."
)
def login(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = db.query(User).filter_by(email=payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expires_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    token = create_access_token(
        data={
            "sub": user.email,
            "user_id": user.id,
            "role": user.role.value,
            "name": user.full_name,
        },
        expires_delta=timedelta(seconds=expires_seconds),
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_seconds=expires_seconds,
        user_id=user.id,
        email=user.email,
        role=user.role,
    )


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new system user",
    description=(
        "Registers a new user with hashed password. "
        "Bootstraps first user as ADMIN if database is empty; otherwise requires ADMIN role."
    )
)
def register_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    current_user: Optional[AuthenticatedUser] = Depends(lambda: None),
) -> User:
    existing = db.query(User).filter_by(email=payload.email).first()
    if existing:
        raise ConflictException(f"User with email '{payload.email}' already exists.")

    total_users = db.query(User).count()
    if total_users == 0:
        # Initial system bootstrap: grant ADMIN role to first registered user
        assigned_role = UserRole.ADMIN
    else:
        # Subsequent user creations require authenticated ADMIN
        # We perform inline check or dependency validation
        auth_user = get_current_user(db=db)
        if auth_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: Only ADMIN can register or provision new users."
            )
        assigned_role = payload.role

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=assigned_role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get(
    "/me",
    response_model=UserRead,
    summary="Retrieve current authenticated user profile",
    description="Returns profile and verified role permissions for the authenticated session."
)
def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    user = db.query(User).filter_by(id=current_user.id).first()
    if not user:
        # Fallback for synthetic/test principals
        return UserRead(
            id=current_user.id,
            email=current_user.email,
            full_name=current_user.full_name,
            role=current_user.role,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    return UserRead.model_validate(user)


@router.get(
    "/users",
    response_model=PaginatedResponse[UserRead],
    summary="List all users",
    description="Admin-only directory listing of system users."
)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles([UserRole.ADMIN])),
) -> PaginatedResponse[UserRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(User)
    total = query.count()
    users = query.order_by(User.id.asc()).offset(params.offset).limit(params.page_size).all()
    items = [UserRead.model_validate(u) for u in users]
    return PaginatedResponse.create(items=items, total=total, params=params)
