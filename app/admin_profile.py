"""
Admin Profile Endpoint
Full profile view for a team member (used by admin "Profile" button):
combines user info + attendance stats/sessions + (if sales) lead stats.
Also provides CSV / PDF export of a user's monthly attendance.

Register this router in your FastAPI app alongside admin_users.py:
    from app.routers import admin_profile
    app.include_router(admin_profile.router)
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from collections import defaultdict
import csv
import io

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-profile"])

IST = timezone(timedelta(hours=5, minutes=30))


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


def to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _user_filter(uid: ObjectId) -> dict:
    """Match user_id stored as either ObjectId OR plain string (see attendance.py)."""
    return {"$or": [{"user_id": uid}, {"user_id": str(uid)}]}


def _same_domain_or_403(current_user: dict, target: dict):
    """Admins may only view profiles of users in their own email domain."""
    admin_domain = current_user.get("email", "").split("@")[-1].lower()
    target_domain = target.get("email", "").split("@")[-1].lower()
    if admin_domain and target_domain and admin_domain != target_domain:
        raise HTTPException(status_code=403, detail="Not allowed")


def _get_target_user(user_id: str) -> dict:
    db = get_db()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")
    user = db["users"].find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user["id"] = str(user["_id"])
    return user


def _month_bounds(year: int, month: int):
    month_start_ist = datetime(year, month, 1, 0, 0, 0, tzinfo=IST)
    month_end_ist = (
        datetime(year + 1, 1, 1, tzinfo=IST) if month == 12
        else datetime(year, month + 1, 1, tzinfo=IST)
    )
    return (
        month_start_ist.astimezone(timezone.utc).replace(tzinfo=None),
        month_end_ist.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _sessions_for_month(uid: ObjectId, year: int, month: int):
    db = get_db()
    start_utc, end_utc = _month_bounds(year, month)
    return list(db["attendance_sessions"].find({
        **_user_filter(uid),
        "login_at": {"$gte": start_utc, "$lt": end_utc},
    }).sort("login_at", -1))


def _compute_stats(sessions: list):
    """Same math as attendance.py's /stats, applied to an arbitrary session list."""
    if not sessions:
        return {
            "days_present": 0, "total_minutes": 0, "avg_minutes_per_day": 0,
            "longest_streak": 0, "on_time_days": 0, "avg_checkin_time": None,
        }

    by_day = defaultdict(list)
    for s in sessions:
        login_ist = to_ist(s["login_at"])
        by_day[login_ist.strftime("%Y-%m-%d")].append(s)

    days_present = len(by_day)
    total_minutes = 0
    on_time_days = 0
    checkin_minutes_list = []
    now_ist_aware = datetime.now(timezone.utc).astimezone(IST)

    for day, day_sessions in by_day.items():
        day_total = 0
        for s in day_sessions:
            login = to_ist(s["login_at"])
            logout = to_ist(s.get("logout_at")) or now_ist_aware
            day_total += max(0, int((logout - login).total_seconds() / 60))
        total_minutes += day_total

        first = min(day_sessions, key=lambda x: x["login_at"])
        l_ist = to_ist(first["login_at"])
        cutoff = l_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if l_ist <= cutoff:
            on_time_days += 1
        checkin_minutes_list.append(l_ist.hour * 60 + l_ist.minute)

    sorted_days = sorted(by_day.keys())
    longest_streak = 1
    current_streak = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.strptime(sorted_days[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 1

    avg_day = total_minutes // days_present if days_present else 0
    avg_ci_min = sum(checkin_minutes_list) // len(checkin_minutes_list) if checkin_minutes_list else 0
    ah, am = divmod(avg_ci_min, 60)
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


def _serialize_session_row(s):
    login_ist = to_ist(s.get("login_at"))
    logout_ist = to_ist(s.get("logout_at"))
    total_min = None
    if login_ist:
        end = logout_ist or datetime.now(timezone.utc).astimezone(IST)
        total_min = max(0, int((end - login_ist).total_seconds() / 60))
    return {
        "date": login_ist.strftime("%Y-%m-%d") if login_ist else None,
        "weekday": login_ist.strftime("%a") if login_ist else None,
        "check_in": login_ist.isoformat() if login_ist else None,
        "check_out": logout_ist.isoformat() if logout_ist else None,
        "total_minutes": total_min,
        "is_active": s.get("logout_at") is None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{user_id}/profile")
async def get_user_profile(
    user_id: str,
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Full profile bundle for the admin 'Profile' view:
    - user info (extended fields)
    - attendance stats + sessions for the given month (defaults to current month)
    - lead stats (leads / closed / conversion %) if the target user's role is "sales"
    """
    _require_admin(current_user)
    target = _get_target_user(user_id)
    _same_domain_or_403(current_user, target)

    curr_ist = datetime.now(IST)
    y = year or curr_ist.year
    m = month or curr_ist.month

    uid = ObjectId(user_id)
    sessions = _sessions_for_month(uid, y, m)
    stats = _compute_stats(sessions)

    db = get_db()
    lead_stats = None
    if target.get("role") == "sales":
        leads = list(db["leads"].find({"created_by": user_id}))
        total_leads = len(leads)
        closed = sum(1 for l in leads if l.get("status") == "closed")
        conv = round((closed / total_leads) * 100) if total_leads else 0
        lead_stats = {"leads": total_leads, "closed": closed, "conversion": conv}

    return {
        "user": {
            "id": target["id"],
            "full_name": target.get("full_name"),
            "email": target.get("email"),
            "personal_email": target.get("personal_email"),
            "phone": target.get("phone"),
            "date_of_birth": target.get("date_of_birth"),
            "gender": target.get("gender"),
            "job_title": target.get("job_title"),
            "designation": target.get("designation"),
            "address": target.get("address"),
            "emergency_contact_name": target.get("emergency_contact_name"),
            "emergency_contact_phone": target.get("emergency_contact_phone"),
            "role": target.get("role"),
            "is_admin": target.get("is_admin", False),
            "avatar_url": target.get("avatar_url"),
        },
        "year": y,
        "month": m,
        "stats": stats,
        "lead_stats": lead_stats,
        # NOTE: no leaves collection exists yet in this codebase — always 0 for now.
        "leaves_taken": 0,
        "sessions": [_serialize_session_row(s) for s in sessions],
    }


@router.get("/{user_id}/export.csv")
async def export_csv(
    user_id: str,
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    target = _get_target_user(user_id)
    _same_domain_or_403(current_user, target)

    curr_ist = datetime.now(IST)
    y = year or curr_ist.year
    m = month or curr_ist.month
    uid = ObjectId(user_id)
    sessions = _sessions_for_month(uid, y, m)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Check In", "Check Out", "Total (minutes)"])
    for s in sorted(sessions, key=lambda x: x["login_at"]):
        row = _serialize_session_row(s)
        writer.writerow([
            row["date"], row["check_in"] or "",
            row["check_out"] or "Active", row["total_minutes"] or 0,
        ])
    buf.seek(0)

    filename = f"{(target.get('full_name') or 'user').replace(' ', '_')}_{y}_{m:02d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{user_id}/export.pdf")
async def export_pdf(
    user_id: str,
    year: int = Query(None),
    month: int = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Requires fpdf2 (`pip install fpdf2`)."""
    _require_admin(current_user)
    target = _get_target_user(user_id)
    _same_domain_or_403(current_user, target)

    curr_ist = datetime.now(IST)
    y = year or curr_ist.year
    m = month or curr_ist.month
    uid = ObjectId(user_id)
    sessions = _sessions_for_month(uid, y, m)

    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PDF export requires fpdf2. Run: pip install fpdf2",
        )

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Attendance - {target.get('full_name', '')} ({y}-{m:02d})", ln=True)
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(40, 8, "Date", border=1)
    pdf.cell(50, 8, "Check In", border=1)
    pdf.cell(50, 8, "Check Out", border=1)
    pdf.cell(30, 8, "Total", border=1, ln=True)
    pdf.set_font("Helvetica", "", 10)
    for s in sorted(sessions, key=lambda x: x["login_at"]):
        row = _serialize_session_row(s)
        mins = row["total_minutes"] or 0
        total_str = f"{mins // 60}h {mins % 60}m"
        pdf.cell(40, 8, row["date"] or "", border=1)
        pdf.cell(50, 8, row["check_in"] or "", border=1)
        pdf.cell(50, 8, row["check_out"] or "Active", border=1)
        pdf.cell(30, 8, total_str, border=1, ln=True)

    pdf_bytes = pdf.output(dest="S").encode("latin-1")
    filename = f"{(target.get('full_name') or 'user').replace(' ', '_')}_{y}_{m:02d}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )