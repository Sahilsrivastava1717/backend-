"""
Team Hub — per-team page backing /app/team/{slug}.
Returns everything the frontend needs in one call: team users, live-status
settings, today/week activities (from xp_events), lead-derived call/email
signals, attendance sessions, leaves, content docs, and tasks.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/team-hub", tags=["team-hub"])

SLUG_TO_ROLE = {
    "sales": "sales",
    "seo": "seo",
    "content": "content_writer",
    "developers": "developer",
    "managers": "manager",
}

TEAM_META = {
    "sales": {"label": "Sales", "emoji": "💼"},
    "seo": {"label": "SEO", "emoji": "🔍"},
    "content": {"label": "Content", "emoji": "✍️"},
    "developers": {"label": "Developers", "emoji": "💻"},
    "managers": {"label": "Managers", "emoji": "🧭"},
}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _iso(d):
    if isinstance(d, datetime):
        return d.isoformat()
    return d


def _user_out(u: dict) -> dict:
    return {
        "id": str(u["_id"]),
        "name": u.get("full_name") or u.get("username"),
        "email": u.get("email"),
        "last_seen": _iso(u.get("last_seen")),
        "avatar_url": u.get("avatar_url"),
    }


@router.get("/{slug}")
async def get_team_hub(slug: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    role = SLUG_TO_ROLE.get(slug)
    if not role:
        raise HTTPException(404, detail="Unknown team")

    db = get_db()
    domain = _domain_of(current_user.get("email", ""))

    all_users = list(db["users"].find({"email": {"$regex": f"@{domain}$", "$options": "i"}}))

    def _norm(v):
        return (v or "").strip().lower().replace(" ", "_")

    # Employees created via the Employees flow are stored with role=None,
    # so they'd never match on `role` alone — fall back to the employee
    # record's `department` field (mapped the same way as team roles) for
    # any user whose `role` is missing.
    dept_role_by_user = {}
    emp_docs = list(db["employees"].find({}))
    for e in emp_docs:
        uid = e.get("user_id")
        dept = _norm(e.get("department"))
        if uid and dept:
            dept_role_by_user[uid] = dept

    def _effective_role(u):
        r = _norm(u.get("role"))
        if r:
            return r
        return dept_role_by_user.get(str(u["_id"]), "")

    target_role = _norm(role)
    team_users = [u for u in all_users if _effective_role(u) == target_role]
    team_uids = [str(u["_id"]) for u in team_users]

    settings_doc = db["team_settings"].find_one({"domain": domain}) or {}
    settings = {
        "idle_threshold_minutes": settings_doc.get("idle_threshold_minutes", 5),
        "inactive_threshold_minutes": settings_doc.get("inactive_threshold_minutes", 20),
    }

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    # Activities (from xp_events, normalized to activity-log shape)
    xp = list(db["xp_events"].find({"user_id": {"$in": team_uids}}))
    activities = []
    for e in xp:
        created = e.get("created_at")
        if isinstance(created, datetime) and not created.tzinfo:
            created = created.replace(tzinfo=timezone.utc)
        activities.append({
            "id": str(e.get("_id")),
            "user_id": e.get("user_id"),
            "type": e.get("event_type", "remark"),
            "notes": e.get("reason"),
            "created_at": _iso(created),
        })

    # Leads (for call/email signal derivation + performance score)
    leads = list(db["leads"].find({
        "$or": [
            {"assigned_user_id": {"$in": team_uids}},
            {"created_by": {"$in": team_uids}},
        ]
    }))
    for l in leads:
        l["id"] = str(l.pop("_id"))
        for k in ("last_activity_at", "updated_at", "created_at"):
            if isinstance(l.get(k), datetime):
                l[k] = _iso(l[k])

    # Attendance sessions
    oid_uids = [ObjectId(uid) for uid in team_uids]
    sessions = list(db["attendance_sessions"].find({
        "user_id": {"$in": oid_uids + team_uids},
        "login_at": {"$gte": today_start.replace(tzinfo=None)},
    }))
    sessions_out = []
    for s in sessions:
        sessions_out.append({
            "id": str(s["_id"]),
            "user_id": str(s.get("user_id")),
            "login_at": _iso(s.get("login_at")),
            "logout_at": _iso(s.get("logout_at")) if s.get("logout_at") else None,
        })

    # Leaves
    leaves = list(db["leaves"].find({"user_id": {"$in": team_uids}}))
    leaves_out = []
    for l in leaves:
        leaves_out.append({
            "id": str(l["_id"]),
            "user_id": l.get("user_id"),
            "status": l.get("status"),
            "start_date": l.get("start_date"),
            "end_date": l.get("end_date"),
        })

    # Content documents (only meaningful for content_writer team)
    content_docs = []
    if role == "content_writer":
        docs = list(db["content_documents"].find({
            "$or": [
                {"owner_id": {"$in": team_uids}},
                {"assigned_reviewer_id": {"$in": team_uids}},
            ]
        }))
        for d in docs:
            content_docs.append({
                "id": str(d["_id"]),
                "title": d.get("title"),
                "owner_id": d.get("owner_id"),
                "assigned_reviewer_id": d.get("assigned_reviewer_id"),
                "category": d.get("category"),
                "status": d.get("status"),
                "word_count": d.get("word_count", 0),
            })

    # Tasks (for SEO / Developer / Manager teams)
    tasks = []
    if role in ("seo", "developer", "manager"):
        task_docs = list(db["tasks"].find({"assigned_to": {"$in": team_uids}}))
        for t in task_docs:
            tasks.append({
                "id": str(t["_id"]),
                "title": t.get("title"),
                "assigned_to": t.get("assigned_to"),
                "status": t.get("status", "pending"),
                "due_date": _iso(t.get("due_date")) if t.get("due_date") else None,
            })

    return {
        "meta": TEAM_META[slug],
        "settings": settings,
        "users": [_user_out(u) for u in team_users],
        "activities": activities,
        "leads": leads,
        "sessions": sessions_out,
        "leaves": leaves_out,
        "content_documents": content_docs,
        "tasks": tasks,
        "server_time": _iso(now),
        "today_start": _iso(today_start),
        "week_start": _iso(week_start),
    }