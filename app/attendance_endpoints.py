"""
Attendance Endpoints — Check-in/out with photo + IST time
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from typing import Optional
from collections import defaultdict

from app.auth_utils import get_current_user
from app.mongodb import get_db
from app.attendance_models import CheckInRequest, CheckOutRequest

router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])

# IST offset
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(IST)


def utcnow():
    return datetime.now(timezone.utc)


def to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_day_start(dt_ist):
    """Get start of IST day as UTC"""
    day_start_ist = dt_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_ist.astimezone(timezone.utc)


def ist_day_end(dt_ist):
    """Get end of IST day as UTC"""
    day_end_ist = dt_ist.replace(hour=23, minute=59, second=59, microsecond=999999)
    return day_end_ist.astimezone(timezone.utc)


def serialize_session(s):
    login_ist = to_ist(s["login_at"]) if s.get("login_at") else None
    logout_ist = to_ist(s["logout_at"]) if s.get("logout_at") else None
    return {
        "id": str(s["_id"]),
        "user_id": str(s["user_id"]),
        "login_at": login_ist.isoformat() if login_ist else None,
        "logout_at": logout_ist.isoformat() if logout_ist else None,
        "checkin_photo_url": s.get("checkin_photo_url"),
        "checkout_photo_url": s.get("checkout_photo_url"),
        "checkin_note": s.get("checkin_note"),
        "checkout_note": s.get("checkout_note"),
        "is_active": s.get("logout_at") is None,
    }


# ── GET /today ─────────────────────────────────────────────────────────────────
@router.get("/today")
async def get_today(current_user: dict = Depends(get_current_user)):
    db = get_db()
    uid = ObjectId(current_user["id"])
    
    curr_ist = now_ist()
    day_start_utc = ist_day_start(curr_ist)
    day_end_utc = ist_day_end(curr_ist)

    session = db["attendance_sessions"].find_one({
        "user_id": uid,
        "login_at": {"$gte": day_start_utc.replace(tzinfo=None), "$lte": day_end_utc.replace(tzinfo=None)},
        "logout_at": None,
    })

    if not session:
        return {"active": False, "session": None}
    return {"active": True, "session": serialize_session(session)}


# ── GET /sessions ──────────────────────────────────────────────────────────────
@router.get("/sessions")
async def get_sessions(
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    
    curr_ist = now_ist()
    y = year or curr_ist.year
    m = month or curr_ist.month

    # Get IST month boundaries as UTC
    month_start_ist = datetime(y, m, 1, 0, 0, 0, tzinfo=IST)
    if m == 12:
        month_end_ist = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=IST)
    else:
        month_end_ist = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=IST)

    month_start_utc = month_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    month_end_utc = month_end_ist.astimezone(timezone.utc).replace(tzinfo=None)

    sessions = list(db["attendance_sessions"].find({
        "user_id": uid,
        "login_at": {"$gte": month_start_utc, "$lt": month_end_utc}
    }).sort("login_at", -1))

    return [serialize_session(s) for s in sessions]


# ── POST /checkin ──────────────────────────────────────────────────────────────
@router.post("/checkin")
async def checkin(
    data: CheckInRequest,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    uid = ObjectId(current_user["id"])

    curr_ist = now_ist()
    day_start_utc = ist_day_start(curr_ist).replace(tzinfo=None)
    day_end_utc = ist_day_end(curr_ist).replace(tzinfo=None)
    now_utc = utcnow().replace(tzinfo=None)

    # Check if already checked in today (IST) without checkout
    active = db["attendance_sessions"].find_one({
        "user_id": uid,
        "login_at": {"$gte": day_start_utc, "$lte": day_end_utc},
        "logout_at": None,
    })
    if active:
        raise HTTPException(status_code=400, detail="Already checked in today. Please check out first.")

    session = {
        "user_id": uid,
        "login_at": now_utc,
        "logout_at": None,
        "checkin_photo_url": data.photo_url,
        "checkout_photo_url": None,
        "checkin_note": data.note,
        "checkout_note": None,
        "ist_checkin_time": curr_ist.strftime("%H:%M:%S"),
        "ist_date": curr_ist.strftime("%Y-%m-%d"),
        "created_at": now_utc,
    }
    result = db["attendance_sessions"].insert_one(session)
    session["_id"] = result.inserted_id

    # Update user last_seen
    db["users"].update_one({"_id": uid}, {"$set": {"last_seen": now_utc.isoformat()}})

    return serialize_session(session)


# ── POST /checkout ─────────────────────────────────────────────────────────────
@router.post("/checkout")
async def checkout(
    data: CheckOutRequest,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    uid = ObjectId(current_user["id"])

    curr_ist = now_ist()
    day_start_utc = ist_day_start(curr_ist).replace(tzinfo=None)
    day_end_utc = ist_day_end(curr_ist).replace(tzinfo=None)
    now_utc = utcnow().replace(tzinfo=None)

    active = db["attendance_sessions"].find_one({
        "user_id": uid,
        "login_at": {"$gte": day_start_utc, "$lte": day_end_utc},
        "logout_at": None,
    })
    if not active:
        raise HTTPException(status_code=400, detail="No active check-in found for today.")

    db["attendance_sessions"].update_one(
        {"_id": active["_id"]},
        {"$set": {
            "logout_at": now_utc,
            "checkout_photo_url": data.photo_url,
            "checkout_note": data.note,
            "ist_checkout_time": curr_ist.strftime("%H:%M:%S"),
        }}
    )
    active["logout_at"] = now_utc
    active["checkout_photo_url"] = data.photo_url
    active["checkout_note"] = data.note

    return serialize_session(active)


# ── GET /stats ─────────────────────────────────────────────────────────────────
@router.get("/stats")
async def get_stats(
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    uid = ObjectId(current_user["id"])

    curr_ist = now_ist()
    y = year or curr_ist.year
    m = month or curr_ist.month

    month_start_ist = datetime(y, m, 1, 0, 0, 0, tzinfo=IST)
    if m == 12:
        month_end_ist = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=IST)
    else:
        month_end_ist = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=IST)

    month_start_utc = month_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    month_end_utc = month_end_ist.astimezone(timezone.utc).replace(tzinfo=None)

    sessions = list(db["attendance_sessions"].find({
        "user_id": uid,
        "login_at": {"$gte": month_start_utc, "$lt": month_end_utc}
    }).sort("login_at", 1))

    if not sessions:
        return {
            "days_present": 0,
            "total_minutes": 0,
            "avg_minutes_per_day": 0,
            "longest_streak": 0,
            "on_time_days": 0,
            "avg_checkin_time": None,
        }

    # Group by IST day
    by_day = defaultdict(list)
    for s in sessions:
        login_utc = s["login_at"]
        if login_utc.tzinfo is None:
            login_utc = login_utc.replace(tzinfo=timezone.utc)
        login_ist = login_utc.astimezone(IST)
        day_key = login_ist.strftime("%Y-%m-%d")
        by_day[day_key].append(s)

    days_present = len(by_day)
    total_minutes = 0
    on_time_days = 0
    checkin_minutes_list = []
    now_utc_aware = utcnow()

    for day, day_sessions in by_day.items():
        day_total = 0
        for s in day_sessions:
            login = s["login_at"].replace(tzinfo=timezone.utc) if s["login_at"].tzinfo is None else s["login_at"]
            logout = s["logout_at"]
            if logout:
                logout = logout.replace(tzinfo=timezone.utc) if logout.tzinfo is None else logout
            else:
                logout = now_utc_aware
            day_total += max(0, int((logout - login).total_seconds() / 60))
        total_minutes += day_total

        # On-time check: before 9:15 AM IST
        first = min(day_sessions, key=lambda x: x["login_at"])
        login_utc = first["login_at"].replace(tzinfo=timezone.utc) if first["login_at"].tzinfo is None else first["login_at"]
        login_ist = login_utc.astimezone(IST)
        cutoff = login_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if login_ist <= cutoff:
            on_time_days += 1

        checkin_minutes_list.append(login_ist.hour * 60 + login_ist.minute)

    # Streak
    sorted_days = sorted(by_day.keys())
    longest_streak = 1 if sorted_days else 0
    current_streak = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.strptime(sorted_days[i-1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 1

    avg_day = total_minutes // days_present if days_present else 0
    avg_checkin_min = sum(checkin_minutes_list) // len(checkin_minutes_list) if checkin_minutes_list else 0
    ah, am = divmod(avg_checkin_min, 60)
    ampm = "PM" if ah >= 12 else "AM"
    hh = ((ah + 11) % 12) + 1
    avg_checkin_str = f"{hh}:{str(am).zfill(2)} {ampm}"

    return {
        "days_present": days_present,
        "total_minutes": total_minutes,
        "avg_minutes_per_day": avg_day,
        "longest_streak": longest_streak,
        "on_time_days": on_time_days,
        "avg_checkin_time": avg_checkin_str,
    }