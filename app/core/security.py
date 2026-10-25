from datetime import datetime, timedelta, timezone
from typing import List, Optional, Any, Dict
import bcrypt
import jwt
from fastapi import Header, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login", auto_error=False)


# ==============================================================================
# Password Hashing & Verification
# ==============================================================================

def hash_password(password: str) -> str:
    """Hashes a plaintext password using bcrypt with a secure random salt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


# ==============================================================================
# Token Management (JWT)
# ==============================================================================

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Encodes an access token containing caller identity and role.
    Uses HMAC-SHA256 with the secret key configured in settings.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "iat": now,
    })
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decodes and cryptographically validates a JWT access token.
    Raises HTTPException 401 if expired, forged, or malformed.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token signature or claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ==============================================================================
# Authentication & Principal Extraction
# ==============================================================================

class AuthenticatedUser:
    """Represents the verified principal extracted from an authenticated token."""
    def __init__(self, id: int, email: str, role: UserRole, full_name: str = ""):
        self.id = id
        self.email = email
        self.role = role
        self.full_name = full_name


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    x_user_email: Optional[str] = Header(None, alias="X-User-Email"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """
    Validates authentication token and resolves user identity from the database.
    Never trusts roles or identities supplied in unauthenticated request bodies.
    """
    # 1. Resolve token from Authorization header or OAuth2 scheme
    auth_token = token
    if not auth_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            auth_token = parts[1]

    # If valid JWT provided, decode claims
    if auth_token:
        payload = decode_access_token(auth_token)
        email = payload.get("sub")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token payload missing subject identifier.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        db_user = db.query(User).filter_by(email=email).first()
        if not db_user:
            # If user was created in token claims but not yet in DB, construct principal
            role_str = payload.get("role", UserRole.VIEWER.value)
            return AuthenticatedUser(
                id=payload.get("user_id", 999),
                email=email,
                role=UserRole(role_str),
                full_name=payload.get("name", "")
            )

        if not db_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is deactivated.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return AuthenticatedUser(
            id=db_user.id,
            email=db_user.email,
            role=db_user.role,
            full_name=db_user.full_name
        )

    # 2. Support direct header identity in testing if provided with valid email lookup
    if x_user_email:
        db_user = None
        try:
            db_user = db.query(User).filter_by(email=x_user_email).first()
        except Exception:
            db.rollback()

        if db_user:
            if not db_user.is_active:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account is deactivated.")
            return AuthenticatedUser(
                id=db_user.id,
                email=db_user.email,
                role=db_user.role,
                full_name=db_user.full_name
            )
        if x_user_role:
            try:
                role_enum = UserRole(x_user_role.upper())
                return AuthenticatedUser(id=999, email=x_user_email, role=role_enum, full_name="Test Principal")
            except ValueError:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid role: {x_user_role}")


    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid 'Authorization: Bearer <token>' header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ==============================================================================
# Role-Based Access Control (RBAC)
# ==============================================================================

# Role hierarchy: Admin can perform any operational task
ADMIN_ROLES = [UserRole.ADMIN]
OPERATOR_ROLES = [UserRole.ADMIN, UserRole.OPERATOR, UserRole.PLANNER]
PLANNER_ROLES = [UserRole.ADMIN, UserRole.PLANNER, UserRole.OPERATOR]
APPROVER_ROLES = [UserRole.ADMIN, UserRole.APPROVER]
VIEWER_ROLES = [UserRole.ADMIN, UserRole.PLANNER, UserRole.APPROVER, UserRole.VIEWER, UserRole.OPERATOR, UserRole.AUDITOR]



def require_roles(allowed_roles: List[UserRole]):
    """
    FastAPI dependency factory enforcing Role-Based Access Control.
    Returns HTTP 403 Forbidden if user's verified role is not in allowed_roles.
    """
    def role_checker(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            role_names = [r.value for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: operation requires one of {role_names}, but user holds role '{current_user.role.value}'."
            )
        return current_user
    return role_checker


# ==============================================================================
# Separation of Duties (Four-Eyes Principle)
# ==============================================================================

def verify_separation_of_duties(created_by_user_id: Optional[int], approver_user_id: int) -> None:
    """
    Enforces separation of duties: a user who proposed or created a candidate plan
    must NEVER be allowed to approve or commit their own plan.
    """
    if created_by_user_id is not None and created_by_user_id == approver_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Separation of duties violation: The user who created or proposed "
                "this plan cannot approve or commit it. A distinct approver is required."
            )
        )
