"""
Admin Attendance Endpoints
Domain-scoped team attendance views: daily breakdown, leaderboard, calendar,
login history, live status, offline activity/duration, CSV export, alerts.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from collections import defaultdict
import io
import csv

from app.auth_utils import get_current_user
from app.mongodb import get_db, get_users_collection

router = APIRouter(prefix="/api/v1/admin/attendance", tags=["admin-attendance"])

IST = timezone(timedelta(hours=5, minutes=30))


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


def _domain_users(current_user: dict):
    email = current_user.get("email", "")
    domain = email.split("@")[-1] if "@" in email else None
    if not domain:
        return []
    escaped = domain.replace(".", "\\.")
    users = list(get_users_collection().find({"email": {"$regex": f"@{escaped}$", "$options": "i"}}))
    return [u for u in users if not u.get("is_admin", False)]


def _name_of(u):
    return u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0]


def to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_day_bounds(date_ist):
    start = date_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    end = date_ist.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)


def _sessions_for(user_ids, start_utc=None, end_utc=None):
    db = get_db()
    object_ids = [ObjectId(uid) for uid in user_ids]
    query = {"$or": [{"user_id": {"$in": object_ids}}, {"user_id": {"$in": user_ids}}]}
    if start_utc and end_utc:
        query = {"$and": [query, {"login_at": {"$gte": start_utc, "$lte": end_utc}}]}
    return list(db["attendance_sessions"].find(query).sort("login_at", -1))


def _uid_str(s):
    return str(s.get("user_id"))


# ── Overview KPIs ──────────────────────────────────────────────────────────
@router.get("/overview")
async def overview(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]

    curr_ist = datetime.now(IST)
    day_start, day_end = ist_day_bounds(curr_ist)
    sessions_today = _sessions_for(user_ids, day_start, day_end)

    active_now = len({_uid_str(s) for s in sessions_today if s.get("logout_at") is None})
    open_alerts = get_db()["inactivity_logs"].count_documents({
        "user_id": {"$in": user_ids}, "acknowledged": {"$ne": True}
    })

    return {
        "team": len(users),
        "active_now": active_now,
        "inactive": 0,
        "open_alerts": open_alerts,
    }


# ── Daily attendance (grouped by day, with per-user blocks) ────────────────
@router.get("/daily")
async def daily_attendance(
    date: str = Query(None, description="YYYY-MM-DD, defaults to today (IST)"),
    user_id: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    users = _domain_users(current_user)
    if user_id and user_id != "all":
        users = [u for u in users if str(u["_id"]) == user_id]
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    curr_ist = datetime.now(IST)
    target = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=IST) if date else curr_ist
    day_start, day_end = ist_day_bounds(target)
    sessions = _sessions_for(user_ids, day_start, day_end)

    by_user = defaultdict(list)
    for s in sessions:
        by_user[_uid_str(s)].append(s)

    rows = []
    total_minutes = 0
    for uid, sess_list in by_user.items():
        sess_list.sort(key=lambda s: s["login_at"])
        first_in = to_ist(sess_list[0]["login_at"])
        last_session = sess_list[-1]
        is_online = last_session.get("logout_at") is None
        last_out = None if is_online else to_ist(last_session["logout_at"])
        minutes = 0
        for s in sess_list:
            login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
            logout = s.get("logout_at")
            logout = (logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout) if logout else datetime.now(timezone.utc)
            minutes += max(0, int((logout - login).total_seconds() / 60))
        total_minutes += minutes
        rows.append({
            "user_id": uid,
            "name": name_map.get(uid, "Unknown"),
            "first_in": first_in.isoformat() if first_in else None,
            "last_out": last_out.isoformat() if last_out else None,
            "online": is_online,
            "total_minutes": minutes,
            "blocks": len(sess_list),
        })
    rows.sort(key=lambda r: r["first_in"] or "")

    return {
        "date": target.strftime("%Y-%m-%d"),
        "rows": rows,
        "total_worked_minutes": total_minutes,
        "avg_minutes": (total_minutes // len(rows)) if rows else 0,
        "users_count": len(rows),
    }


# ── Login/logout history ────────────────────────────────────────────────────
@router.get("/sessions")
async def sessions_history(
    from_date: str = Query(None),
    to_date: str = Query(None),
    user_id: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    start_utc = end_utc = None
    if from_date:
        start_utc, _ = ist_day_bounds(datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=IST))
    if to_date:
        _, end_utc = ist_day_bounds(datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=IST))

    sessions = _sessions_for(user_ids, start_utc, end_utc) if (start_utc or end_utc) else _sessions_for(user_ids)
    if user_id != "all":
        sessions = [s for s in sessions if _uid_str(s) == user_id]

    out = []
    for s in sessions:
        login_ist = to_ist(s["login_at"])
        logout_ist = to_ist(s.get("logout_at"))
        minutes = None
        if logout_ist:
            minutes = int((logout_ist - login_ist).total_seconds() / 60)
        out.append({
            "id": str(s["_id"]),
            "user_id": _uid_str(s),
            "name": name_map.get(_uid_str(s), "Unknown"),
            "login_at": login_ist.isoformat() if login_ist else None,
            "logout_at": logout_ist.isoformat() if logout_ist else None,
            "duration_minutes": minutes,
        })
    return {"sessions": out, "total": len(out), "users": [{"id": uid, "name": name_map[uid]} for uid in user_ids]}


# ── Live team status ─────────────────────────────────────────────────────────
@router.get("/live")
async def live_team(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    curr_ist = datetime.now(IST)
    day_start, day_end = ist_day_bounds(curr_ist)
    sessions_today = _sessions_for(user_ids, day_start, day_end)

    by_user = defaultdict(list)
    for s in sessions_today:
        by_user[_uid_str(s)].append(s)

    rows = []
    for u in users:
        uid = str(u["_id"])
        sess_list = by_user.get(uid, [])
        online = any(s.get("logout_at") is None for s in sess_list)
        last_login = None
        today_minutes = 0
        for s in sess_list:
            login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
            if last_login is None or login > last_login:
                last_login = login
            logout = s.get("logout_at")
            logout = (logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout) if logout else datetime.now(timezone.utc)
            today_minutes += max(0, int((logout - login).total_seconds() / 60))
        last_seen = u.get("last_seen")
        mins_ago = None
        if last_seen:
            try:
                ls = datetime.fromisoformat(last_seen.replace("Z", "+00:00")) if isinstance(last_seen, str) else last_seen
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                mins_ago = int((datetime.now(timezone.utc) - ls).total_seconds() / 60)
            except Exception:
                mins_ago = None
        rows.append({
            "id": uid,
            "name": _name_of(u),
            "email": u.get("email", ""),
            "status": "online" if online else "offline",
            "last_active_minutes_ago": mins_ago,
            "last_login": to_ist(last_login).isoformat() if last_login else None,
            "today_active_minutes": today_minutes,
            "sessions_today": len(sess_list),
        })
    rows.sort(key=lambda r: (r["status"] != "online", -(r["today_active_minutes"])))
    return {"users": rows}


# ── Calendar (per-day totals for a user in a given month) ──────────────────
@router.get("/calendar")
async def calendar(
    user_id: str = Query(...),
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    curr_ist = datetime.now(IST)
    y = year or curr_ist.year
    m = month or curr_ist.month

    month_start_ist = datetime(y, m, 1, tzinfo=IST)
    month_end_ist = datetime(y + 1, 1, 1, tzinfo=IST) if m == 12 else datetime(y, m + 1, 1, tzinfo=IST)
    start_utc = month_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = month_end_ist.astimezone(timezone.utc).replace(tzinfo=None)

    sessions = _sessions_for([user_id], start_utc, end_utc)

    by_day = defaultdict(int)
    for s in sessions:
        login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
        login_ist = login.astimezone(IST)
        logout = s.get("logout_at")
        logout = (logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout) if logout else datetime.now(timezone.utc)
        minutes = max(0, int((logout - login).total_seconds() / 60))
        by_day[login_ist.strftime("%Y-%m-%d")] += minutes

    leaves = list(get_db()["leaves"].find({"user_id": user_id, "status": "approved"})) if "leaves" in get_db().list_collection_names() else []
    leave_dates = set()
    for l in leaves:
        d = l.get("date")
        if d:
            leave_dates.add(d if isinstance(d, str) else d.strftime("%Y-%m-%d"))

    days_in_month = (month_end_ist - month_start_ist).days
    working_days = present = absent = 0
    today_str = curr_ist.strftime("%Y-%m-%d")
    day_cursor = month_start_ist
    days_out = {}
    for i in range(days_in_month):
        d = day_cursor + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        is_weekend = d.weekday() >= 5
        minutes = by_day.get(key, 0)
        is_leave = key in leave_dates
        is_future = key > today_str
        if not is_weekend and not is_future:
            working_days += 1
            if minutes > 0:
                present += 1
            elif not is_leave:
                absent += 1
        days_out[key] = {
            "minutes": minutes, "is_weekend": is_weekend, "is_leave": is_leave, "is_future": is_future,
        }

    return {
        "year": y, "month": m,
        "working_days": working_days, "present": present, "absent": absent,
        "leaves": len(leave_dates), "holidays": 0,
        "weekends": sum(1 for v in days_out.values() if v["is_weekend"]),
        "days": days_out,
    }


# ── Leaderboard ──────────────────────────────────────────────────────────────
@router.get("/leaderboard")
async def leaderboard(
    period: str = Query("weekly", regex="^(weekly|monthly)$"),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    curr_ist = datetime.now(IST)
    if period == "weekly":
        start_ist = (curr_ist - timedelta(days=curr_ist.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_ist = curr_ist.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_ist.astimezone(timezone.utc).replace(tzinfo=None)

    sessions = _sessions_for(user_ids, start_utc, datetime.utcnow())
    by_user = defaultdict(list)
    for s in sessions:
        by_user[_uid_str(s)].append(s)

    stats = []
    for uid, sess_list in by_user.items():
        minutes, on_time, by_day = 0, 0, defaultdict(list)
        for s in sess_list:
            login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
            logout = s.get("logout_at")
            logout = (logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout) if logout else datetime.now(timezone.utc)
            minutes += max(0, int((logout - login).total_seconds() / 60))
            by_day[login.astimezone(IST).strftime("%Y-%m-%d")].append(login.astimezone(IST))
        total_days = len(by_day)
        for day, logins in by_day.items():
            first = min(logins)
            cutoff = first.replace(hour=9, minute=15, second=0, microsecond=0)
            if first <= cutoff:
                on_time += 1
        sorted_days = sorted(by_day.keys())
        streak = longest = 1 if sorted_days else 0
        for i in range(1, len(sorted_days)):
            prev = datetime.strptime(sorted_days[i - 1], "%Y-%m-%d")
            cur = datetime.strptime(sorted_days[i], "%Y-%m-%d")
            if (cur - prev).days == 1:
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 1
        stats.append({
            "id": uid, "name": name_map.get(uid, "Unknown"),
            "minutes": minutes, "days": total_days,
            "on_time_pct": round((on_time / total_days) * 100) if total_days else 0,
            "streak_days": longest,
        })

    return {
        "period": period,
        "since": start_ist.strftime("%Y-%m-%d"),
        "most_hours": sorted(stats, key=lambda s: -s["minutes"])[:10],
        "best_on_time": sorted(stats, key=lambda s: -s["on_time_pct"])[:10],
        "longest_streaks": sorted(stats, key=lambda s: -s["streak_days"])[:10],
    }


# ── Offline activity (work logged while off-shift, i.e. after last logout) ──
@router.get("/offline-activity")
async def offline_activity(
    user_id: str = Query("all"),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    users = _domain_users(current_user)
    if user_id != "all":
        users = [u for u in users if str(u["_id"]) == user_id]
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    db = get_db()
    since = datetime.utcnow() - timedelta(days=14)
    sessions = _sessions_for(user_ids, since, datetime.utcnow())
    tasks = list(db["tasks"].find({
        "assigned_to": {"$in": user_ids}, "status": "done",
        "completed_at": {"$gte": since},
    }))

    sessions_by_user = defaultdict(list)
    for s in sessions:
        sessions_by_user[_uid_str(s)].append(s)

    def is_offline_at(uid, ts):
        for s in sessions_by_user.get(uid, []):
            login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
            logout = s.get("logout_at")
            logout = (logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout) if logout else datetime.now(timezone.utc)
            if login <= ts <= logout:
                return False
        return True

    by_day = defaultdict(list)
    offline_minutes_by_user = defaultdict(int)
    for t in tasks:
        uid = t.get("assigned_to")
        completed = t["completed_at"].replace(tzinfo=timezone.utc) if t["completed_at"].tzinfo is None else t["completed_at"]
        if not is_offline_at(uid, completed):
            continue
        ist_dt = completed.astimezone(IST)
        day_key = ist_dt.strftime("%Y-%m-%d")
        by_day[day_key].append({
            "user_id": uid, "name": name_map.get(uid, "Unknown"),
            "type": f"Task: {t.get('priority', 'medium').title()}",
            "notes": t.get("title", "") + (f" — {t['completion_remarks']}" if t.get("completion_remarks") else ""),
            "at": ist_dt.isoformat(),
        })
        offline_minutes_by_user[uid] += 1  # placeholder weight; real durations come from offline-duration endpoint

    days_out = []
    for day, entries in sorted(by_day.items(), reverse=True):
        entries.sort(key=lambda e: e["at"], reverse=True)
        days_out.append({"date": day, "count": len(entries), "entries": entries})

    return {
        "days": days_out,
        "total_offline_entries": sum(d["count"] for d in days_out),
        "by_user": [{"name": name_map[uid], "count": c} for uid, c in offline_minutes_by_user.items()],
    }


# ── Offline duration (today) — windows between check-in/out gaps ───────────
@router.get("/offline-duration")
async def offline_duration(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    curr_ist = datetime.now(IST)
    day_start, day_end = ist_day_bounds(curr_ist)
    sessions = _sessions_for(user_ids, day_start, day_end)
    by_user = defaultdict(list)
    for s in sessions:
        by_user[_uid_str(s)].append(s)

    rows = []
    for uid in user_ids:
        sess_list = sorted(by_user.get(uid, []), key=lambda s: s["login_at"])
        if not sess_list:
            continue
        check_in = to_ist(sess_list[0]["login_at"])
        last = sess_list[-1]
        online = last.get("logout_at") is None
        check_out = None if online else to_ist(last["logout_at"])

        windows = []
        for i in range(len(sess_list) - 1):
            gap_start = sess_list[i].get("logout_at")
            gap_end = sess_list[i + 1]["login_at"]
            if gap_start:
                windows.append((to_ist(gap_start), to_ist(gap_end)))
        total_offline = sum(int((w[1] - w[0]).total_seconds() / 60) for w in windows)

        rows.append({
            "user_id": uid, "name": name_map.get(uid, "Unknown"),
            "check_in": check_in.isoformat() if check_in else None,
            "check_out": check_out.isoformat() if check_out else None,
            "online": online,
            "offline_windows": len(windows),
            "total_offline_minutes": total_offline,
        })

    return {"date": curr_ist.strftime("%Y-%m-%d"), "rows": rows, "checked_in_count": len(rows)}


# ── Export (CSV) ──────────────────────────────────────────────────────────────
@router.get("/export")
async def export_csv(
    from_date: str = Query(...),
    to_date: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    start_utc, _ = ist_day_bounds(datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=IST))
    _, end_utc = ist_day_bounds(datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=IST))
    sessions = _sessions_for(user_ids, start_utc, end_utc)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Date", "Check-in", "Check-out", "Work From", "Duration (min)"])
    for s in sorted(sessions, key=lambda s: s["login_at"]):
        login_ist = to_ist(s["login_at"])
        logout_ist = to_ist(s.get("logout_at"))
        minutes = int((logout_ist - login_ist).total_seconds() / 60) if logout_ist else ""
        writer.writerow([
            name_map.get(_uid_str(s), "Unknown"),
            login_ist.strftime("%Y-%m-%d"),
            login_ist.strftime("%H:%M:%S"),
            logout_ist.strftime("%H:%M:%S") if logout_ist else "Active",
            s.get("work_from", "office"),
            minutes,
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance_{from_date}_to_{to_date}.csv"},
    )


# ── Alerts (inactivity log) ─────────────────────────────────────────────────
@router.get("/alerts")
async def alerts(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    name_map = {str(u["_id"]): _name_of(u) for u in users}

    logs = list(get_db()["inactivity_logs"].find({"user_id": {"$in": user_ids}}).sort("detected_at", -1).limit(200))
    out = []
    for l in logs:
        out.append({
            "id": str(l["_id"]),
            "user_id": l.get("user_id"),
            "name": name_map.get(l.get("user_id"), "Unknown user"),
            "minutes_inactive": l.get("minutes_inactive", 0),
            "detected_at": l.get("detected_at"),
            "acknowledged": l.get("acknowledged", False),
        })
    return {"alerts": out}


@router.post("/alerts/ack-all")
async def ack_all_alerts(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    get_db()["inactivity_logs"].update_many(
        {"user_id": {"$in": user_ids}, "acknowledged": {"$ne": True}},
        {"$set": {"acknowledged": True}},
    )
    return {"message": "All alerts acknowledged"}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    get_db()["inactivity_logs"].update_one({"_id": ObjectId(alert_id)}, {"$set": {"acknowledged": True}})
    return {"message": "Acknowledged"}