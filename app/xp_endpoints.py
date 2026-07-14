"""
XP & Leaderboard Endpoints
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from datetime import datetime, timedelta, timezone
from typing import Optional
from bson import ObjectId

from app.auth_utils import get_current_user, get_user_by_id
from app.mongodb import get_db, get_users_collection
from app.xp_models import (
    XPEventCreate, XPEventResponse, LeaderboardEntry,
    LeaderboardResponse, MyXPResponse, BreakdownEntry,
    TeamFairnessRow, TeamFairnessResponse
)
from app.xp_utils import get_level

router = APIRouter(prefix="/api/v1/xp", tags=["xp"])


def get_xp_collection():
    return get_db()["xp_events"]


def serialize_event(e: dict) -> dict:
    e["id"] = str(e["_id"])
    e.pop("_id", None)
    return e


def parse_period(period: str, custom_month: Optional[str] = None):
    """Returns (from_dt, to_dt, label) for a given period string."""
    now = datetime.utcnow()
    if period == "all":
        return None, None, "All time"
    if period == "7d":
        return now - timedelta(days=7), now, "Last 7 days"
    if period == "30d":
        return now - timedelta(days=30), now, "Last 30 days"
    if period == "current_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(seconds=1)
        else:
            end = now.replace(month=now.month + 1, day=1) - timedelta(seconds=1)
        label = now.strftime("%B %Y")
        return start, end, label
    if period == "custom_month" and custom_month:
        y, m = map(int, custom_month.split("-"))
        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(y, m + 1, 1) - timedelta(seconds=1)
        return start, end, start.strftime("%B %Y")
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now, now.strftime("%B %Y")


def build_query(from_dt, to_dt, user_id=None):
    q = {}
    if user_id:
        q["user_id"] = user_id
    if from_dt and to_dt:
        q["created_at"] = {"$gte": from_dt, "$lte": to_dt}
    return q


def build_breakdown(events: list) -> dict:
    bd = {}
    for e in events:
        t = e["event_type"]
        if t not in bd:
            bd[t] = {"points": 0, "count": 0}
        bd[t]["points"] += e["points"]
        bd[t]["count"] += 1
    return bd


def _naive(dt):
    """Normalize any datetime to naive UTC so comparisons never mix
    aware/naive values (this was the actual cause of team-fairness 500s —
    completed_at and due_date weren't consistently tz-aware or tz-naive)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def get_domain_scoped_users(current_user: dict) -> list:
    """
    Return only the users that share the current user's email domain
    (e.g. ezsignly.com users see only ezsignly.com users, gmail.com users
    see only gmail.com users), mirroring the scoping used for teammates
    in chat_endpoints.get_teammates.
    """
    users_col = get_users_collection()

    email = current_user.get("email", "")
    domain = email.split("@")[-1] if "@" in email else None
    if not domain:
        return []

    escaped_domain = domain.replace(".", "\\.")
    return list(users_col.find({
        "email": {"$regex": f"@{escaped_domain}$", "$options": "i"}
    }))


# ── Award XP (manual, admin or self) ─────────────────────────────────────────
@router.post("/award", response_model=XPEventResponse, status_code=status.HTTP_201_CREATED)
async def award_xp(data: XPEventCreate, current_user: dict = Depends(get_current_user)):
    col = get_xp_collection()

    target_id = data.target_user_id or current_user["id"]
    if data.target_user_id and data.target_user_id != current_user["id"]:
        target = get_user_by_id(data.target_user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

    doc = {
        "user_id": target_id,
        "event_type": data.event_type,
        "points": data.points,
        "reason": data.reason,
        "created_at": datetime.utcnow(),
    }
    result = col.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


# ── My XP ─────────────────────────────────────────────────────────────────────
@router.get("/me", response_model=MyXPResponse)
async def my_xp(
    period: str = Query("current_month"),
    custom_month: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    col = get_xp_collection()
    from_dt, to_dt, label = parse_period(period, custom_month)
    user_id = current_user["id"]

    period_events = list(col.find(build_query(from_dt, to_dt, user_id)).sort("created_at", -1))
    period_xp = sum(e["points"] for e in period_events)

    all_events = list(col.find({"user_id": user_id}))
    total_xp = sum(e["points"] for e in all_events)

    level_info = get_level(period_xp)
    breakdown = build_breakdown(period_events)
    recents = [serialize_event(e) for e in period_events[:30]]

    return MyXPResponse(
        period_xp=period_xp,
        total_xp=total_xp,
        level=level_info["level"],
        level_title=level_info["title"],
        progress=level_info["progress"],
        to_next=level_info["to_next"],
        recent_events=recents,
        breakdown={k: BreakdownEntry(**v) for k, v in breakdown.items()},
    )


# ── Leaderboard ───────────────────────────────────────────────────────────────
@router.get("/leaderboard", response_model=LeaderboardResponse)
async def leaderboard(
    period: str = Query("current_month"),
    custom_month: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    col = get_xp_collection()
    from_dt, to_dt, label = parse_period(period, custom_month)

    users = get_domain_scoped_users(current_user)

    entries = []
    for u in users:
        uid = str(u["_id"])

        period_events = list(col.find(build_query(from_dt, to_dt, uid)).sort("created_at", -1))
        period_xp = sum(e["points"] for e in period_events)

        all_xp = sum(e["points"] for e in col.find({"user_id": uid}))

        breakdown = build_breakdown(period_events)
        recents = [serialize_event(e) for e in period_events[:8]]

        entries.append(LeaderboardEntry(
            id=uid,
            username=u.get("username", ""),
            full_name=u.get("full_name"),
            period_xp=period_xp,
            total_xp=all_xp,
            breakdown={k: BreakdownEntry(**v) for k, v in breakdown.items()},
            recents=recents,
        ))

    entries.sort(key=lambda x: (-x.period_xp, -x.total_xp))

    return LeaderboardResponse(entries=entries, period_label=label)


# ── Team Fairness ─────────────────────────────────────────────────────────────
@router.get("/team-fairness", response_model=TeamFairnessResponse)
async def team_fairness(
    period: str = Query("current_month"),
    custom_month: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    col = get_xp_collection()
    tasks_col = get_db()["tasks"]
    from_dt, to_dt, label = parse_period(period, custom_month)

    users = get_domain_scoped_users(current_user)

    teams: dict = {}
    for u in users:
        # `.get("role", "unassigned")` only falls back when the key is
        # *missing* — several users have "role": None stored explicitly,
        # so .get returns None instead of the default, and that None was
        # then flowing into TeamFairnessRow.team (str), a 500. `or` covers
        # both "missing" and "present but falsy/None".
        role = u.get("role") or "unassigned"
        if role not in teams:
            teams[role] = []
        teams[role].append(str(u["_id"]))

    rows = []
    for team, uids in teams.items():
        task_query = {"assigned_to": {"$in": uids}}
        if from_dt and to_dt:
            task_query["due_date"] = {"$gte": from_dt, "$lte": to_dt}
        due_tasks = list(tasks_col.find(task_query))

        # FIX: normalize both sides to naive UTC before comparing — mixing
        # tz-aware and tz-naive datetimes here raised a TypeError that
        # crashed this endpoint with a 500 ("Failed to load XP data").
        done_on_time = sum(
            1 for t in due_tasks
            if t.get("status") == "done"
            and t.get("completed_at")
            and t.get("due_date")
            and _naive(t["completed_at"]) <= _naive(t["due_date"])
        )

        total_due = len(due_tasks)
        rate = round((done_on_time / total_due * 100)) if total_due > 0 else 0

        total_xp = 0
        for uid in uids:
            q = build_query(from_dt, to_dt, uid)
            total_xp += sum(e["points"] for e in col.find(q))
        avg_xp = round(total_xp / len(uids)) if uids else 0

        rows.append(TeamFairnessRow(
            team=team,
            members=len(uids),
            due=total_due,
            done_on_time=done_on_time,
            rate=rate,
            avg_xp=avg_xp,
        ))

    rows.sort(key=lambda x: -x.rate)
    return TeamFairnessResponse(rows=rows, period_label=label)


# ── My recent events (simple list) ───────────────────────────────────────────
@router.get("/events", response_model=list)
async def my_events(
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    col = get_xp_collection()
    events = list(col.find({"user_id": current_user["id"]}).sort("created_at", -1).limit(limit))
    return [serialize_event(e) for e in events]