"""
Company Report Endpoints — CEO-level analytics data feed.
Returns raw, domain-scoped, date-filtered rows; the frontend does the
same aggregation/grouping the reference report page always did.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _domain_user_ids(current_user: dict, db) -> list[str]:
    domain = _domain_of(current_user.get("email", ""))
    if not domain:
        return [current_user["id"]]
    escaped = domain.replace(".", "\\.")
    users = db["users"].find({"email": {"$regex": f"@{escaped}$", "$options": "i"}})
    return [str(u["_id"]) for u in users]


def _iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/company-overview")
async def get_company_overview(
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    db = get_db()

    from_dt = _parse_dt(from_)
    to_dt = _parse_dt(to)
    date_q = {"$gte": from_dt, "$lte": to_dt}

    user_ids = _domain_user_ids(current_user, db)

    # ---- Users (non-admin, for leaderboards) ----
    user_docs = list(db["users"].find({"_id": {"$in": [ObjectId(u) for u in user_ids]}}))
    users = [{"id": str(u["_id"]), "name": u.get("full_name") or u.get("username") or "Unknown"} for u in user_docs]

    # ---- Leads ----
    leads = list(db["leads"].find({
        "created_by": {"$in": user_ids},
        "created_at": date_q,
    }))
    leads_out = [{
        "id": str(l["_id"]),
        "assigned_user_id": l.get("created_by"),
        "status": l.get("status", "new"),
        "offer_code": l.get("offer_code"),
        "created_at": _iso(l.get("created_at")),
    } for l in leads]

    # ---- Lead activities (calls/emails) — collection: lead_activities ----
    activities = list(db["lead_activities"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    })) if "lead_activities" in db.list_collection_names() else []
    activities_out = [{
        "id": str(a["_id"]),
        "user_id": a.get("user_id"),
        "type": a.get("type"),
        "created_at": _iso(a.get("created_at")),
    } for a in activities]

    # ---- Tasks ----
    tasks = list(db["tasks"].find({
        "assigned_to": {"$in": user_ids},
        "created_at": date_q,
    }))
    tasks_out = [{
        "id": str(t["_id"]),
        "assigned_to": t.get("assigned_to"),
        "status": t.get("status", "pending"),
        "due_date": _iso(t.get("due_date")),
        "completed_at": _iso(t.get("completed_at")),
        "created_at": _iso(t.get("created_at")),
    } for t in tasks]

    # ---- Backlinks — collection: backlinks ----
    backlinks = list(db["backlinks"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    })) if "backlinks" in db.list_collection_names() else []
    backlinks_out = [{
        "id": str(b["_id"]),
        "user_id": b.get("user_id"),
        "status": b.get("status", "pending"),
        "do_follow": b.get("do_follow", False),
        "created_at": _iso(b.get("created_at")),
    } for b in backlinks]

    # ---- Content documents ----
    content_docs = list(db["content_documents"].find({
        "owner_id": {"$in": user_ids},
        "created_at": date_q,
    }))
    content_docs_out = [{
        "id": str(d["_id"]),
        "owner_id": d.get("owner_id"),
        "status": d.get("status", "draft"),
        "word_count": d.get("word_count", 0),
        "created_at": _iso(d.get("created_at")),
    } for d in content_docs]

    # ---- Content time logs — collection: content_time_logs ----
    content_time = list(db["content_time_logs"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    })) if "content_time_logs" in db.list_collection_names() else []
    content_time_out = [{
        "user_id": t.get("user_id"),
        "seconds": t.get("seconds", 0),
    } for t in content_time]

    # ---- Attendance sessions ----
    sessions = list(db["attendance_sessions"].find({
        "$or": [
            {"user_id": {"$in": [ObjectId(u) for u in user_ids]}},
            {"user_id": {"$in": user_ids}},
        ],
        "login_at": date_q,
    }))
    sessions_out = [{
        "user_id": str(s["user_id"]),
        "login_at": _iso(s.get("login_at")),
        "logout_at": _iso(s.get("logout_at")),
    } for s in sessions]

    # ---- Inactivity logs — collection: inactivity_logs ----
    inactivity = list(db["inactivity_logs"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    })) if "inactivity_logs" in db.list_collection_names() else []
    inactivity_out = [{
        "user_id": i.get("user_id"),
        "minutes_inactive": i.get("minutes_inactive", 0),
    } for i in inactivity]

    # ---- Leaves ----
    leaves = list(db["leaves"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    }))
    leaves_out = [{
        "id": str(l["_id"]),
        "user_id": l.get("user_id"),
        "status": l.get("status", "pending"),
        "type": l.get("type", "casual"),
    } for l in leaves]

    # ---- XP events ----
    xp = list(db["xp_events"].find({
        "user_id": {"$in": user_ids},
        "created_at": date_q,
    }))
    xp_out = [{
        "user_id": e.get("user_id"),
        "points": e.get("points", 0),
    } for e in xp]

    return {
        "users": users,
        "leads": leads_out,
        "activities": activities_out,
        "tasks": tasks_out,
        "backlinks": backlinks_out,
        "content_docs": content_docs_out,
        "content_time": content_time_out,
        "sessions": sessions_out,
        "inactivity": inactivity_out,
        "leaves": leaves_out,
        "xp": xp_out,
    }