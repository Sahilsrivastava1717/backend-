"""
Team Settings Endpoints — office hours, break policy, idle/inactive thresholds.
Single settings doc per domain (company).
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/team-settings", tags=["team-settings"])

DEFAULTS = {
    "work_start_time": "09:00:00", "work_end_time": "18:00:00",
    "break_start_time": "13:00:00", "break_end_time": "14:00:00",
    "max_break_minutes": 60,
    "idle_threshold_minutes": 5, "inactive_threshold_minutes": 20,
    "on_time_grace_minutes": 15,
    "timezone": "Asia/Kolkata",
    "working_days": [1, 2, 3, 4, 5],
}


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


class SettingsUpdate(BaseModel):
    work_start_time: Optional[str] = None
    work_end_time: Optional[str] = None
    break_start_time: Optional[str] = None
    break_end_time: Optional[str] = None
    max_break_minutes: Optional[int] = None
    idle_threshold_minutes: Optional[int] = None
    inactive_threshold_minutes: Optional[int] = None
    on_time_grace_minutes: Optional[int] = None
    timezone: Optional[str] = None
    working_days: Optional[list] = None


@router.get("")
async def get_settings(current_user: dict = Depends(get_current_user)):
    db = get_db()
    domain = _domain_of(current_user.get("email", ""))
    doc = db["team_settings"].find_one({"domain": domain})
    if not doc:
        doc = {"domain": domain, **DEFAULTS, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
        result = db["team_settings"].insert_one(doc)
        doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.put("")
async def update_settings(data: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")
    db = get_db()
    domain = _domain_of(current_user.get("email", ""))
    update = {k: v for k, v in data.dict(exclude_unset=True).items()}
    update["updated_at"] = datetime.now(timezone.utc)
    update["updated_by"] = current_user["id"]
    db["team_settings"].update_one({"domain": domain}, {"$set": update}, upsert=True)
    doc = db["team_settings"].find_one({"domain": domain})
    return _to_resp(doc)