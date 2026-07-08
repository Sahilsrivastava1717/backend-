"""
Leaves & Holidays Endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1", tags=["leaves"])

LEAVE_TYPES = {"casual", "sick", "vacation", "personal", "unpaid"}
LEAVE_STATUS = {"pending", "approved", "rejected", "cancelled"}


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "reviewed_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _domain_user_ids(current_user: dict) -> list:
    from app.mongodb import get_users_collection
    domain = _domain_of(current_user.get("email", ""))
    if not domain:
        return [current_user["id"]]
    users = get_users_collection().find({"email": {"$regex": f"@{domain}$", "$options": "i"}})
    return [str(u["_id"]) for u in users]


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


# ── Leaves ────────────────────────────────────────────────────────────────────

class LeaveCreate(BaseModel):
    start_date: str
    end_date: str
    type: str = "casual"
    reason: Optional[str] = None
    user_id: Optional[str] = None  # admin-only: assign to someone else


class LeaveReview(BaseModel):
    status: str  # approved | rejected


@router.get("/leaves")
async def list_leaves(current_user: dict = Depends(get_current_user)):
    db = get_db()
    is_admin = current_user.get("is_admin", False)
    q = {} if is_admin else {"user_id": current_user["id"]}
    if is_admin:
        q["user_id"] = {"$in": _domain_user_ids(current_user)}
    docs = list(db["leaves"].find(q).sort("created_at", -1))
    return [_to_resp(d) for d in docs]


@router.post("/leaves", status_code=201)
async def create_leave(data: LeaveCreate, current_user: dict = Depends(get_current_user)):
    if data.type not in LEAVE_TYPES:
        raise HTTPException(400, detail="Invalid leave type")
    db = get_db()
    is_admin = current_user.get("is_admin", False)
    target_user = data.user_id if (is_admin and data.user_id) else current_user["id"]
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": target_user, "start_date": data.start_date, "end_date": data.end_date,
        "type": data.type, "reason": data.reason,
        # Admin-assigned leaves are auto-approved; self-requested leaves need review.
        "status": "approved" if is_admin else "pending",
        "reviewed_by": current_user["id"] if is_admin else None,
        "reviewed_at": now if is_admin else None,
        "created_at": now,
    }
    result = db["leaves"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.patch("/leaves/{leave_id}")
async def review_leave(leave_id: str, data: LeaveReview, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if data.status not in {"approved", "rejected"}:
        raise HTTPException(400, detail="Invalid status")
    db = get_db()
    result = db["leaves"].update_one(
        {"_id": ObjectId(leave_id)},
        {"$set": {"status": data.status, "reviewed_by": current_user["id"], "reviewed_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, detail="Leave not found")
    return {"message": "Updated"}


@router.delete("/leaves/{leave_id}")
async def delete_leave(leave_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    doc = db["leaves"].find_one({"_id": ObjectId(leave_id)})
    if not doc:
        raise HTTPException(404, detail="Not found")
    if not current_user.get("is_admin") and doc.get("user_id") != current_user["id"]:
        raise HTTPException(403, detail="Not allowed")
    db["leaves"].delete_one({"_id": ObjectId(leave_id)})
    return {"message": "Deleted"}


# ── Holidays ──────────────────────────────────────────────────────────────────

class HolidayCreate(BaseModel):
    date: str  # YYYY-MM-DD
    name: str = Field(..., min_length=1, max_length=120)


@router.get("/holidays")
async def list_holidays(current_user: dict = Depends(get_current_user)):
    docs = list(get_db()["holidays"].find({}).sort("date", 1))
    return [_to_resp(d) for d in docs]


@router.post("/holidays", status_code=201)
async def create_holiday(data: HolidayCreate, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    doc = {"date": data.date, "name": data.name.strip(), "created_by": current_user["id"], "created_at": datetime.now(timezone.utc)}
    result = get_db()["holidays"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.delete("/holidays/{holiday_id}")
async def delete_holiday(holiday_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    result = get_db()["holidays"].delete_one({"_id": ObjectId(holiday_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Not found")
    return {"message": "Deleted"}