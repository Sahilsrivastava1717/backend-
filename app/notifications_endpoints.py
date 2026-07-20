"""
Notifications Endpoints
Simple per-user notification feed. Anything else in the app (tasks, leads,
leaves, mentions, etc.) can insert into the `notifications` collection with
{ user_id, title, body, type, link, read, created_at } and it'll show up here.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def _col():
    return get_db()["notifications"]


def _to_resp(n: dict) -> dict:
    n = {**n}
    n["id"] = str(n.pop("_id"))
    if isinstance(n.get("created_at"), datetime):
        n["created_at"] = n["created_at"].isoformat()
    return n


@router.get("")
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    items = list(_col().find({"user_id": current_user["id"]}).sort("created_at", -1).limit(limit))
    unread_count = _col().count_documents({"user_id": current_user["id"], "read": False})
    return {"notifications": [_to_resp(n) for n in items], "unread_count": unread_count}


@router.get("/unread-count")
async def unread_count(current_user: dict = Depends(get_current_user)):
    count = _col().count_documents({"user_id": current_user["id"], "read": False})
    return {"unread_count": count}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    try:
        oid = ObjectId(notification_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")
    result = _col().update_one(
        {"_id": oid, "user_id": current_user["id"]},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, detail="Notification not found")
    return {"message": "Marked as read"}


@router.post("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    _col().update_many(
        {"user_id": current_user["id"], "read": False},
        {"$set": {"read": True, "read_at": datetime.now(timezone.utc)}},
    )
    return {"message": "All marked as read"}


@router.delete("/{notification_id}")
async def delete_notification(notification_id: str, current_user: dict = Depends(get_current_user)):
    try:
        oid = ObjectId(notification_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")
    result = _col().delete_one({"_id": oid, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Notification not found")
    return {"message": "Deleted"}