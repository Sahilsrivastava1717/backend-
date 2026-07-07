"""
Content Writing-Time Tracking
Client calls POST /api/v1/content/{doc_id}/time-log periodically (e.g. every
30s while the editor tab is focused) to accumulate seconds spent per user
per document per day. Admin overview reads this via /api/v1/admin/content/writing-time.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/content", tags=["content-time"])


class TimeLogIn(BaseModel):
    seconds: int = Field(..., gt=0, le=300)  # one heartbeat tick, capped at 5 min


@router.post("/{doc_id}/time-log")
async def log_time(doc_id: str, data: TimeLogIn, current_user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(400, detail="Invalid document id")
    doc = db["content_documents"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(404, detail="Document not found")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db["content_time_logs"].update_one(
        {"document_id": doc_id, "user_id": current_user["id"], "log_date": today},
        {"$inc": {"seconds": data.seconds}, "$setOnInsert": {
            "document_id": doc_id, "user_id": current_user["id"], "log_date": today,
        }},
        upsert=True,
    )
    return {"ok": True}