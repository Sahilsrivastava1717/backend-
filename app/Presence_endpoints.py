"""
Presence heartbeat — frontend pings this every ~20-30s while tab is active
so admin overview can compute live online/idle/away/inactive status from
`users.last_seen`.
"""
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/presence", tags=["presence"])


@router.post("/heartbeat")
async def heartbeat(current_user: dict = Depends(get_current_user)):
    db = get_db()
    now = datetime.now(timezone.utc)
    db["users"].update_one({"_id": ObjectId(current_user["id"])}, {"$set": {"last_seen": now.isoformat()}})
    return {"ok": True}