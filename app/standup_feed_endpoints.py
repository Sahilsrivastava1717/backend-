"""
Team Standup Feed — admin-only view of everyone's daily standup/EOD report.
Reads from the real `standups` collection your attendance endpoints already
write to: one doc per user per IST day, keyed by (user_id, ist_date), with
fields priorities, submitted_at, completed, blockers, mood, eod_submitted_at.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
import csv, io

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/standup-feed", tags=["standup-feed"])


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _domain_users(current_user: dict):
    from app.mongodb import get_users_collection
    domain = _domain_of(current_user.get("email", ""))
    if not domain:
        return []
    return list(get_users_collection().find({"email": {"$regex": f"@{domain}$", "$options": "i"}}))


def _fmt_time(dt):
    if not dt:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _build_feed(ist_date: str, current_user: dict):
    db = get_db()
    users = [u for u in _domain_users(current_user) if not u.get("is_admin", False)]
    user_ids = [str(u["_id"]) for u in users]

    reports = list(db["standups"].find({"ist_date": ist_date, "user_id": {"$in": user_ids}}))
    report_by_user = {r["user_id"]: r for r in reports}

    members = []
    submitted_standup = 0
    submitted_eod = 0
    with_blockers = 0
    mood_counts: dict = {}

    for u in users:
        uid = str(u["_id"])
        r = report_by_user.get(uid)
        has_standup = bool(r and r.get("submitted_at"))
        has_eod = bool(r and r.get("eod_submitted_at"))
        if has_standup:
            submitted_standup += 1
        if has_eod:
            submitted_eod += 1
        if r and r.get("blockers"):
            with_blockers += 1
        if r and r.get("mood"):
            mood_counts[r["mood"]] = mood_counts.get(r["mood"], 0) + 1

        members.append({
            "id": uid,
            "name": u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0],
            "email": u.get("email"),
            "report": {
                "priorities": r.get("priorities", []) if r else [],
                "completed": r.get("completed", []) if r else [],
                "blockers": r.get("blockers") if r else None,
                "mood": r.get("mood") if r else None,
                "standup_submitted_at": _fmt_time(r.get("submitted_at")) if r else None,
                "eod_submitted_at": _fmt_time(r.get("eod_submitted_at")) if r else None,
            } if r else None,
        })

    top_mood = None
    if mood_counts:
        top_mood = max(mood_counts.items(), key=lambda kv: kv[1])[0]

    return {
        "date": ist_date,
        "members": members,
        "stats": {
            "total": len(users),
            "submitted_standup": submitted_standup,
            "submitted_eod": submitted_eod,
            "with_blockers": with_blockers,
            "top_mood": top_mood,
            "mood_counts": mood_counts,
        },
    }


@router.get("")
async def get_feed(date: str = Query(...), current_user: dict = Depends(get_current_user)):
    """date = 'YYYY-MM-DD' (IST calendar date, matches `standups.ist_date`)."""
    _require_admin(current_user)
    return _build_feed(date, current_user)


@router.get("/export.csv")
async def export_csv(date: str = Query(...), current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    data = _build_feed(date, current_user)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Member", "Email", "Mood", "Priorities", "Completed", "Blockers", "Standup at", "EOD at"])
    for m in data["members"]:
        r = m["report"] or {}
        w.writerow([
            m["name"], m["email"], r.get("mood") or "—",
            " | ".join(r.get("priorities", [])), " | ".join(r.get("completed", [])),
            r.get("blockers") or "", r.get("standup_submitted_at") or "—", r.get("eod_submitted_at") or "—",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=team-standup-{date}.csv"},
    )