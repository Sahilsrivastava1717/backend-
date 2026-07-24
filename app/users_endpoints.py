"""
Admin Users Endpoints
Create, list, update role, reset password, delete team members.
Admin-only. Domain-scoped (same email domain as admin).
"""
from fastapi import APIRouter, HTTPException, Depends, status
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from bson import ObjectId

from app.auth_utils import get_current_user, hash_password, get_users_collection
from app.auth_models import UserResponse

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


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
        "avatar_url": u.get("avatar_url"),
        "job_title": u.get("job_title"),
        "designation": u.get("designation"),
        "temp_password": u.get("temp_password"),
        "last_login": u.get("last_login"),
        "org_name": u.get("org_name"),
    }


# ── Pydantic models ───────────────────────────────────────────────────────────

VALID_ROLES = {"sales", "seo", "content_writer", "developer", "manager", "admin"}


class CreateUserRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str = "sales"
    offer_code: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    role: str


class ResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_users(current_user: dict = Depends(get_current_user)):
    """List all users in the same domain as the admin."""
    _require_admin(current_user)
    col = get_users_collection()

    admin_email = current_user.get("email", "")
    domain = admin_email.split("@")[-1] if "@" in admin_email else None

    query = {}
    if domain:
        query["email"] = {"$regex": f"@{domain}$", "$options": "i"}

    users = []
    for u in col.find(query).sort("created_at", 1):
        u["id"] = str(u["_id"])
        u.pop("_id", None)
        u.pop("password_hash", None)
        users.append(_to_user_resp(u))

    return {"users": users, "total": len(users)}


@router.post("", status_code=201)
async def create_user(
    data: CreateUserRequest,
    current_user: dict = Depends(get_current_user)
):
    """Admin creates a new team member (same domain enforced)."""
    _require_admin(current_user)

    if data.role not in VALID_ROLES:
        raise HTTPException(400, detail=f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}")

    col = get_users_collection()

    # Domain check — new user must be same domain as admin
    admin_email = current_user.get("email", "")
    admin_domain = admin_email.split("@")[-1] if "@" in admin_email else None
    new_domain = data.email.split("@")[-1] if "@" in data.email else None
    if admin_domain and new_domain and admin_domain != new_domain:
        raise HTTPException(400, detail=f"New member email must belong to @{admin_domain}")

    if col.find_one({"email": data.email}):
        raise HTTPException(400, detail="Email already registered")

    # Build username from email prefix
    base_username = data.email.split("@")[0].lower().replace(".", "_")
    username = base_username
    suffix = 1
    while col.find_one({"username": username}):
        username = f"{base_username}{suffix}"
        suffix += 1

    offer_code = None
    if data.offer_code and data.role == "sales":
        code = data.offer_code.strip().upper()
        if col.find_one({"offer_code": code}):
            raise HTTPException(400, detail="Offer code already in use")
        offer_code = code

    is_admin_flag = (data.role == "admin")

    # FIX: org_name was hardcoded as "ezsignly" here previously. It must
    # instead be inherited from the admin creating this user — org_name is
    # only ever set at admin-signup time, so every team member created
    # under that admin should carry the same org_name forward.
    org_name = current_user.get("org_name")

    user_doc = {
        "username": username,
        "email": data.email,
        "full_name": data.full_name,
        "password_hash": hash_password(data.password),
        "temp_password": data.password,   # stored so admin can view it
        "role": data.role,
        "is_active": True,
        "is_admin": is_admin_flag,
        "created_at": datetime.utcnow(),
        "offer_code": offer_code,
        "avatar_url": None,
        "job_title": None,
        "designation": None,
        "org_name": org_name,
    }

    result = col.insert_one(user_doc)
    user_doc["id"] = str(result.inserted_id)
    user_doc.pop("_id", None)
    user_doc.pop("password_hash", None)

    return {"user": _to_user_resp(user_doc), "message": "User created successfully"}


@router.patch("/{user_id}/role")
async def update_role(
    user_id: str,
    data: UpdateRoleRequest,
    current_user: dict = Depends(get_current_user)
):
    """Change a user's role."""
    _require_admin(current_user)

    if data.role not in VALID_ROLES:
        raise HTTPException(400, detail=f"Invalid role")

    col = get_users_collection()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, detail="Invalid user id")

    update_fields = {
        "role": data.role,
        "is_admin": data.role == "admin",
    }

    if data.role != "sales":
        update_fields["offer_code"] = None

    result = col.update_one({"_id": oid}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(404, detail="User not found")

    return {"message": "Role updated"}


@router.patch("/{user_id}/password")
async def reset_password(
    user_id: str,
    data: ResetPasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    """Admin resets a user's password."""
    _require_admin(current_user)

    col = get_users_collection()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, detail="Invalid user id")

    result = col.update_one(
        {"_id": oid},
        {"$set": {
            "password_hash": hash_password(data.password),
            "temp_password": data.password,
            "password_changed_at": datetime.utcnow().isoformat(),
            "password_changed_source": "admin_reset",
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(404, detail="User not found")

    return {"message": "Password reset successfully"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a user (cannot delete yourself or the main admin)."""
    _require_admin(current_user)

    if user_id == current_user.get("id"):
        raise HTTPException(400, detail="Cannot delete your own account")

    col = get_users_collection()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, detail="Invalid user id")

    target = col.find_one({"_id": oid})
    if not target:
        raise HTTPException(404, detail="User not found")

    col.delete_one({"_id": oid})
    return {"message": "User deleted"}