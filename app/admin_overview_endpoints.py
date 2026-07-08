"""
Admin Overview — cross-team performance, productivity signals, growth analytics.
Aggregates users, xp_events (as "activities"), leads, leaves, attendance_sessions.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta, timezone
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/overview", tags=["admin-overview"])

TEAM_ROLES = ["sales", "seo", "content_writer", "developer", "manager"]
TEAM_META = {
    "sales": {"label": "Sales", "emoji": "💼", "slug": "sales"},
    "seo": {"label": "SEO", "emoji": "🔍", "slug": "seo"},
    "content_writer": {"label": "Content", "emoji": "✍️", "slug": "content"},
    "developer": {"label": "Developers", "emoji": "💻", "slug": "developers"},
    "manager": {"label": "Managers", "emoji": "🧭", "slug": "managers"},
}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _parse_last_seen(u: dict):
    ls = u.get("last_seen")
    if not ls:
        return None
    if isinstance(ls, str):
        try:
            return datetime.fromisoformat(ls.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(ls, datetime):
        return ls if ls.tzinfo else ls.replace(tzinfo=timezone.utc)
    return None


def _live_status(u: dict, active_session_ids: set, idle_min: int, inactive_min: int) -> str:
    uid = str(u["_id"])
    if uid not in active_session_ids:
        return "offline"
    ls = _parse_last_seen(u)
    if not ls:
        return "away"
    diff_min = (datetime.now(timezone.utc) - ls).total_seconds() / 60
    if diff_min <= idle_min:
        return "active"
    if diff_min <= idle_min * 2:
        return "idle"
    if diff_min <= inactive_min:
        return "away"
    return "inactive"


@router.get("")
async def overview(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()

    domain = _domain_of(current_user.get("email", ""))
    users = list(db["users"].find({"email": {"$regex": f"@{domain}$", "$options": "i"}}))
    user_ids = [str(u["_id"]) for u in users]

    settings = db["team_settings"].find_one({"domain": domain}) or {"idle_threshold_minutes": 5, "inactive_threshold_minutes": 20}
    idle_min = settings.get("idle_threshold_minutes", 5)
    inactive_min = settings.get("inactive_threshold_minutes", 20)

    # Users with an active (not-checked-out) attendance session today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    active_sessions = list(db["attendance_sessions"].find({
        "user_id": {"$in": [ObjectId(uid) for uid in user_ids] + user_ids},
        "login_at": {"$gte": today_start.replace(tzinfo=None)},
        "logout_at": None,
    }))
    active_session_ids = {str(s["user_id"]) for s in active_sessions}

    now = datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days = now - timedelta(days=7)
    fourteen_days = now - timedelta(days=14)

    xp_events = list(db["xp_events"].find({"user_id": {"$in": user_ids}}))
    leads = list(db["leads"].find({"created_by": {"$in": user_ids}}))
    leaves = list(db["leaves"].find({"user_id": {"$in": user_ids}}))
    sessions_7d = list(db["attendance_sessions"].find({
        "user_id": {"$in": [ObjectId(uid) for uid in user_ids] + user_ids},
        "login_at": {"$gte": seven_days.replace(tzinfo=None)},
    }))

    def acts_since(uids, since):
        return sum(1 for e in xp_events if e.get("user_id") in uids and _ev_dt(e) >= since)

    def _ev_dt(e):
        d = e.get("created_at")
        if isinstance(d, datetime):
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    def on_leave_today(uids):
        return sum(1 for l in leaves if l.get("status") == "approved" and l.get("start_date", "") <= today_key <= l.get("end_date", "") and l.get("user_id") in uids)

    # ── Per-team breakdown ──
    teams = []
    for role in TEAM_ROLES:
        team_users = [u for u in users if u.get("role") == role]
        team_uids = [str(u["_id"]) for u in team_users]
        team_leads = [l for l in leads if l.get("created_by") in team_uids]
        closed = sum(1 for l in team_leads if l.get("status") in ("closed", "closed_won"))
        counts = {"active": 0, "idle": 0, "away": 0, "inactive": 0, "offline": 0}
        for u in team_users:
            counts[_live_status(u, active_session_ids, idle_min, inactive_min)] += 1
        teams.append({
            "role": role, "meta": TEAM_META[role], "members": len(team_users),
            "acts_today": acts_since(team_uids, today_start),
            "acts_week": acts_since(team_uids, week_start),
            "leads": len(team_leads), "closed": closed,
            "on_leave": on_leave_today(team_uids),
            "active_now": counts["active"], "idle_now": counts["idle"],
            "away_now": counts["away"], "inactive_now": counts["inactive"], "offline_now": counts["offline"],
        })

    # ── Underperformers (last 7d) ──
    underperformers = []
    for u in users:
        uid = str(u["_id"])
        acts7 = sum(1 for e in xp_events if e.get("user_id") == uid and _ev_dt(e) >= seven_days)
        user_sess = [s for s in sessions_7d if str(s.get("user_id")) == uid]
        total_min = 0
        for s in user_sess:
            start = s["login_at"]
            end = s.get("logout_at") or now.replace(tzinfo=None)
            if isinstance(start, datetime) and isinstance(end, datetime):
                total_min += max(0, int((end - start).total_seconds() / 60))
        underperformers.append({
            "user": {"id": uid, "name": u.get("full_name") or u.get("username")},
            "acts7": acts7, "total_hours": round(total_min / 60, 1),
            "team": u.get("role") or "—",
        })
    underperformers.sort(key=lambda r: r["acts7"])
    underperformers = underperformers[:8]

    # ── Growth ──
    this_week_acts = sum(1 for e in xp_events if _ev_dt(e) >= seven_days)
    last_week_acts = sum(1 for e in xp_events if fourteen_days <= _ev_dt(e) < seven_days)
    this_week_leads = sum(1 for l in leads if isinstance(l.get("created_at"), datetime) and (l["created_at"].replace(tzinfo=timezone.utc) if not l["created_at"].tzinfo else l["created_at"]) >= seven_days)
    last_week_leads = sum(1 for l in leads if isinstance(l.get("created_at"), datetime) and fourteen_days <= (l["created_at"].replace(tzinfo=timezone.utc) if not l["created_at"].tzinfo else l["created_at"]) < seven_days)
    closed_all = sum(1 for l in leads if l.get("status") in ("closed", "closed_won"))
    conv = round((closed_all / len(leads)) * 100) if leads else 0
    acts_delta = round(((this_week_acts - last_week_acts) / last_week_acts) * 100) if last_week_acts else 0
    leads_delta = round(((this_week_leads - last_week_leads) / last_week_leads) * 100) if last_week_leads else 0

    live_counts = {"active": 0, "idle": 0, "away": 0, "inactive": 0, "offline": 0}
    for u in users:
        live_counts[_live_status(u, active_session_ids, idle_min, inactive_min)] += 1

    pending_leaves = sum(1 for l in leaves if l.get("status") == "pending")

    return {
        "team_size": len(users),
        "live_counts": live_counts,
        "pending_leaves": pending_leaves,
        "growth": {
            "this_week_acts": this_week_acts, "last_week_acts": last_week_acts, "acts_delta": acts_delta,
            "this_week_leads": this_week_leads, "last_week_leads": last_week_leads, "leads_delta": leads_delta,
            "conv": conv, "closed_all": closed_all,
        },
        "teams": teams,
        "underperformers": underperformers,
    }