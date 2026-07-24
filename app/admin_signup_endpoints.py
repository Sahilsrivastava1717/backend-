"""
Admin Signup Endpoints
Public (unauthenticated) endpoint for a brand-new org to create its
first admin account. Blocked if that email domain already has an admin.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

from app.auth_utils import hash_password, get_users_collection, create_access_token

router = APIRouter(prefix="/api/v1/admin-signup", tags=["admin-signup"])


class AdminSignupRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    org_name: str = Field(..., min_length=1, max_length=100)


def _to_user_resp(u: dict) -> dict:
    return {
        "id": u["id"],
        "username": u.get("username", ""),
        "email": u.get("email", ""),
        "full_name": u.get("full_name"),
        "role": u.get("role"),
        "is_admin": u.get("is_admin", False),
        "is_active": u.get("is_active", True),
        "created_at": u.get("created_at", datetime.utcnow()).isoformat(),
        "org_name": u.get("org_name"),
    }


@router.post("", status_code=201)
async def signup_admin(data: AdminSignupRequest):
    col = get_users_collection()
    domain = data.email.split("@")[-1].lower() if "@" in data.email else None
    if not domain:
        raise HTTPException(400, detail="Invalid email")

    # Block if this domain already has an admin — new orgs only.
    existing_admin = col.find_one({
        "email": {"$regex": f"@{domain}$", "$options": "i"},
        "is_admin": True,
    })
    if existing_admin:
        raise HTTPException(
            400,
            detail=f"An admin already exists for @{domain}. Ask them to invite you instead."
        )

    if col.find_one({"email": data.email}):
        raise HTTPException(400, detail="Email already registered")

    base_username = data.email.split("@")[0].lower().replace(".", "_")
    username = base_username
    suffix = 1
    while col.find_one({"username": username}):
        username = f"{base_username}{suffix}"
        suffix += 1

    user_doc = {
        "username": username,
        "email": data.email,
        "full_name": data.full_name,
        "password_hash": hash_password(data.password),
        "temp_password": data.password,
        "role": "admin",
        "is_active": True,
        "is_admin": True,
        "org_name": data.org_name,
        "created_at": datetime.utcnow(),
        "offer_code": None,
        "avatar_url": None,
        "job_title": None,
        "designation": None,
    }

    result = col.insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    user_doc.pop("_id", None)

    token = create_access_token({"sub": user_doc["id"], "email": user_doc["email"]})

    resp = user_doc.copy()
    resp.pop("password_hash", None)

    return {
        "user": _to_user_resp(resp),
        "access_token": token,
        "token_type": "bearer",
        "message": "Admin account created successfully",
    }