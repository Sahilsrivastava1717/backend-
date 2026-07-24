"""
Presence — heartbeat + live status.

Frontend pings /heartbeat every ~20-30s while a tab is active, which stamps
users.last_seen. /status and /status/{user_id} derive a presence state
(active/idle/away/inactive/offline) from that timestamp using the same
thresholds the frontend's presenceFromLastSeen() uses, so admin views and
individual PresenceDot components agree.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/presence", tags=["presence"])

# ── Thresholds (minutes since last_seen) — keep in sync with
# frontend lib/use-presence.ts presenceFromLastSeen() ──
ACTIVE_MAX_MIN = 2
IDLE_MAX_MIN = 10
AWAY_MAX_MIN = 30
INACTIVE_MAX_MIN = 60 * 8  # 8 hours
# anything beyond INACTIVE_MAX_MIN, or no last_seen at all → "offline"


def _parse_last_seen(last_seen):
    if not last_seen:
        return None
    if isinstance(last_seen, datetime):
        dt = last_seen
    else:
        try:
            dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def presence_from_last_seen(last_seen) -> str:
    dt = _parse_last_seen(last_seen)
    if dt is None:
        return "offline"
    minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if minutes < ACTIVE_MAX_MIN:
        return "active"
    if minutes < IDLE_MAX_MIN:
        return "idle"
    if minutes < AWAY_MAX_MIN:
        return "away"
    if minutes < INACTIVE_MAX_MIN:
        return "inactive"
    return "offline"


@router.post("/heartbeat")
async def heartbeat(current_user: dict = Depends(get_current_user)):
    db = get_db()
    now = datetime.now(timezone.utc)
    db["users"].update_one({"_id": ObjectId(current_user["id"])}, {"$set": {"last_seen": now.isoformat()}})
    return {"ok": True}


@router.get("/status")
async def my_status(current_user: dict = Depends(get_current_user)):
    """Current user's own presence — mainly for debugging/testing thresholds."""
    db = get_db()
    u = db["users"].find_one({"_id": ObjectId(current_user["id"])})
    last_seen = u.get("last_seen") if u else None
    return {
        "user_id": current_user["id"],
        "last_seen": last_seen,
        "state": presence_from_last_seen(last_seen),
    }


@router.get("/status/{user_id}")
async def user_status(user_id: str, current_user: dict = Depends(get_current_user)):
    """Look up another user's presence — for team/admin views rendering PresenceDot."""
    db = get_db()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(400, detail="Invalid user id")
    u = db["users"].find_one({"_id": oid})
    if not u:
        raise HTTPException(404, detail="User not found")
    last_seen = u.get("last_seen")
    return {
        "user_id": user_id,
        "last_seen": last_seen,
        "state": presence_from_last_seen(last_seen),
    }


@router.get("/status-bulk")
async def bulk_status(user_ids: str, current_user: dict = Depends(get_current_user)):
    """
    Comma-separated user_ids → {user_id: {last_seen, state}}. Lets a team
    list render every row's PresenceDot with one call instead of N.
    """
    db = get_db()
    ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]
    object_ids = []
    for uid in ids:
        try:
            object_ids.append(ObjectId(uid))
        except Exception:
            continue

    users = list(db["users"].find({"_id": {"$in": object_ids}}))
    out = {}
    for u in users:
        uid = str(u["_id"])
        last_seen = u.get("last_seen")
        out[uid] = {"last_seen": last_seen, "state": presence_from_last_seen(last_seen)}
    return out