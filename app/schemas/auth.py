from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import UserRole


class UserLoginRequest(BaseModel):
    email: str = Field(..., examples=["planner@flowgrid.io"])
    password: str = Field(..., min_length=6, examples=["SecurePass123!"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user_id: int
    email: str
    role: UserRole


class UserCreateRequest(BaseModel):
    email: str = Field(..., examples=["approver@flowgrid.io"])
    password: str = Field(..., min_length=6, examples=["SecurePass123!"])
    full_name: str = Field(..., examples=["Jane Approver"])
    role: UserRole = Field(default=UserRole.VIEWER)


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
