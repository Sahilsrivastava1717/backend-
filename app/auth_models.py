from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, date


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str] = None
    created_at: datetime
    is_active: bool = True
    is_admin: bool = False
    role: Optional[str] = None

    # ── Extended profile fields ──
    personal_email: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None  # stored as ISO string (YYYY-MM-DD)
    gender: Optional[str] = None
    job_title: Optional[str] = None
    designation: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = Field(None, min_length=3, max_length=50)

    # ── Extended profile fields ──
    personal_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=30)
    date_of_birth: Optional[str] = None  # expects "YYYY-MM-DD"
    gender: Optional[str] = None
    job_title: Optional[str] = Field(None, max_length=100)
    designation: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    bio: Optional[str] = Field(None, max_length=500)
    emergency_contact_name: Optional[str] = Field(None, max_length=100)
    emergency_contact_phone: Optional[str] = Field(None, max_length=30)
    emergency_contact_relation: Optional[str] = None


















   