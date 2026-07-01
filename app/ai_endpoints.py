"""
AI Endpoints
Groq-powered helpers (description refinement, weekly summary, etc.)
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timedelta, timezone

# Load .env so API keys are available when running with uvicorn
from dotenv import load_dotenv
load_dotenv()

from app.auth_utils import get_current_user

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

IST = timezone(timedelta(hours=5, minutes=30))


# ── Refine ────────────────────────────────────────────────────────────────────

class RefineRequest(BaseModel):
    title: str
    description: Optional[str] = None


class RefineResponse(BaseModel):
    refined: str


@router.post("/refine", response_model=RefineResponse)
async def refine_description(
    data: RefineRequest,
    current_user: dict = Depends(get_current_user),
):
    """Use Groq to refine or generate a task description."""
    if not data.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY not found. Add it to your .env file as: GROQ_API_KEY=gsk_..."
        )

    try:
        from groq import Groq
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="groq package not installed. Run: pip install groq"
        )

    try:
        client = Groq(api_key=api_key)

        if data.description and data.description.strip():
            prompt = (
                f'You are a task description editor. Improve and refine the following task description '
                f'to be clearer, more actionable, and professional. Keep it concise (2-4 sentences max). '
                f'No bullet points or markdown. Return only the refined description text, nothing else.\n\n'
                f'Task title: "{data.title}"\n'
                f'Current description: "{data.description}"'
            )
        else:
            prompt = (
                f'You are a task description writer. Write a clear, concise, and actionable description '
                f'(2-4 sentences) for the following task. No bullet points or markdown. '
                f'Return only the description text, nothing else.\n\n'
                f'Task title: "{data.title}"'
            )

        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        refined = message.choices[0].message.content.strip()
        return {"refined": refined}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq API error: {str(e)}")


# ── Weekly Summary ────────────────────────────────────────────────────────────

class WeeklySummaryRequest(BaseModel):
    week_start: Optional[str] = None   # "YYYY-MM-DD" (Monday). Defaults to current week.


class WeeklySummaryResponse(BaseModel):
    user_id: str
    name: str
    week_start: str
    week_end: str
    stats: dict
    headline: str
    highlights: List[str]
    concerns: List[str]
    focus: str
    score: int
    ai_used: bool


def _start_of_week_ist() -> datetime:
    """Return the Monday 00:00 IST of the current week."""
    now_ist = datetime.now(IST)
    monday = now_ist - timedelta(days=(now_ist.weekday()))
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _ymd(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _fallback_headline(name: str, stats: dict) -> str:
    first = name.split(" ")[0]
    return (
        f"This week {first} completed {stats['tasks_done']}/{stats['tasks_assigned']} tasks, "
        f"marked {stats['activities']} activities, and was present "
        f"{stats['days_present']}/{stats['working_days']} days."
    )


@router.post("/weekly-summary", response_model=WeeklySummaryResponse)
async def generate_weekly_summary(
    data: WeeklySummaryRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate an AI-powered weekly performance summary for the current user."""
    from app.mongodb import get_db
    from bson import ObjectId

    db = get_db()
    uid = ObjectId(current_user["id"])
    name = current_user.get("full_name") or current_user.get("username") or "User"

    # ── Week boundaries ──────────────────────────────────────────────────────
    if data.week_start:
        week_start_ist = datetime.strptime(data.week_start, "%Y-%m-%d").replace(tzinfo=IST)
    else:
        week_start_ist = _start_of_week_ist()

    week_end_ist = week_start_ist + timedelta(days=6, hours=23, minutes=59, seconds=59)
    week_start_utc = week_start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    week_end_utc   = (week_start_ist + timedelta(days=7)).astimezone(timezone.utc).replace(tzinfo=None)

    # ── Pull attendance sessions ─────────────────────────────────────────────
    sessions = list(db["attendance_sessions"].find({
        "user_id": uid,
        "login_at": {"$gte": week_start_utc, "$lt": week_end_utc},
    }).sort("login_at", 1))

    # Group by IST day and aggregate
    from collections import defaultdict
    by_day: dict = defaultdict(list)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    for s in sessions:
        login = s["login_at"]
        if hasattr(login, "tzinfo") and login.tzinfo is None:
            login_aware = login.replace(tzinfo=timezone.utc)
        else:
            login_aware = login if login.tzinfo else login.replace(tzinfo=timezone.utc)
        login_ist = login_aware.astimezone(IST)
        by_day[login_ist.strftime("%Y-%m-%d")].append(s)

    days_present = len(by_day)
    total_minutes = 0
    on_time_days = 0

    for day, day_sessions in by_day.items():
        for s in day_sessions:
            login_raw = s["login_at"]
            logout_raw = s.get("logout_at") or now_utc
            login_dt  = login_raw  if login_raw.tzinfo  else login_raw.replace(tzinfo=timezone.utc)
            logout_dt = logout_raw if (hasattr(logout_raw,"tzinfo") and logout_raw.tzinfo) else logout_raw.replace(tzinfo=timezone.utc)
            total_minutes += max(0, int((logout_dt - login_dt).total_seconds() / 60))

        # On-time = first check-in ≤ 9:15 AM IST
        first = min(day_sessions, key=lambda x: x["login_at"])
        l = first["login_at"]
        l = l.replace(tzinfo=timezone.utc) if not l.tzinfo else l
        l_ist = l.astimezone(IST)
        cutoff = l_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if l_ist <= cutoff:
            on_time_days += 1

    # Working days Mon–Fri this week
    working_days = sum(
        1 for i in range(5)
        if (week_start_ist + timedelta(days=i)).date() <= week_end_ist.date()
    )

    total_hours = round(total_minutes / 60, 1)

    # ── Pull tasks ───────────────────────────────────────────────────────────
    all_tasks = list(db["tasks"].find({"assigned_to": str(uid)}))

    tasks_done = sum(
        1 for t in all_tasks
        if t.get("status") == "done"
        and t.get("completed_at")
        and week_start_utc <= (t["completed_at"] if not t["completed_at"].tzinfo else t["completed_at"].replace(tzinfo=None)) < week_end_utc
    )
    tasks_assigned = sum(
        1 for t in all_tasks
        if t.get("created_at")
        and week_start_utc <= (t["created_at"] if not t["created_at"].tzinfo else t["created_at"].replace(tzinfo=None)) < week_end_utc
    )
    tasks_overdue = sum(
        1 for t in all_tasks
        if t.get("status") not in ("done", "cancelled")
        and t.get("due_date")
        and (t["due_date"] if not t["due_date"].tzinfo else t["due_date"].replace(tzinfo=None)) < now_utc
    )

    stats = {
        "days_present":    days_present,
        "working_days":    working_days,
        "on_time_days":    on_time_days,
        "total_hours":     total_hours,
        "activities":      0,           # extend later
        "tasks_done":      tasks_done,
        "tasks_assigned":  tasks_assigned,
        "tasks_overdue":   tasks_overdue,
    }

    # ── Heuristic score ──────────────────────────────────────────────────────
    att_score  = (days_present / working_days * 40) if working_days else 0
    punc_score = (on_time_days / days_present * 20) if days_present else 0
    task_score = min(40, tasks_done * 8) if tasks_assigned else 0
    score = max(0, min(100, int(att_score + punc_score + task_score)))

    fallback = WeeklySummaryResponse(
        user_id   = str(uid),
        name      = name,
        week_start = _ymd(week_start_ist),
        week_end   = _ymd(week_end_ist),
        stats     = stats,
        headline  = _fallback_headline(name, stats),
        highlights = [],
        concerns  = ([f"{tasks_overdue} task{'s' if tasks_overdue>1 else ''} overdue"] if tasks_overdue else []),
        focus     = "Keep momentum going next week.",
        score     = score,
        ai_used   = False,
    )

    # ── Groq AI refinement ───────────────────────────────────────────────────
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return fallback

    try:
        from groq import Groq
        client = Groq(api_key=api_key)

        system = (
            "You are a manager writing a brief weekly performance summary for a team member. "
            "Tone: factual, encouraging, specific. Use the member's first name.\n\n"
            "Return ONLY valid JSON (no markdown) with these exact keys:\n"
            '{"headline":"<ONE sentence with numbers, ≤32 words>","highlights":["<win>","<win>"],"concerns":["<risk>"],"focus":"<action verb first, ≤16 words>","score":<0-100>}\n\n'
            "Rules:\n"
            "- headline: mirror this style: 'This week Sahil closed 3 deals, was on time 4/5 days, completed 12/15 tasks.'\n"
            "- highlights: 2-4 short wins (each ≤12 words). Empty array if nothing to praise.\n"
            "- concerns: 0-3 short risks (each ≤12 words). Empty array if performance is solid.\n"
            "- focus: ONE actionable suggestion for next week (action verb first).\n"
            "- score: 0-100 reflecting overall weekly performance."
        )

        user_msg = (
            f"Member: {name}\n"
            f"Week: {_ymd(week_start_ist)} to {_ymd(week_end_ist)}\n"
            f"Heuristic score: {score}\n"
            f"Stats: {stats}"
        )

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )

        import json
        raw = resp.choices[0].message.content.strip()
        # Strip any accidental markdown fences
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)

        return WeeklySummaryResponse(
            **{**fallback.dict(),
               "headline":   parsed.get("headline",  fallback.headline),
               "highlights": parsed.get("highlights", [])[:4],
               "concerns":   parsed.get("concerns",  [])[:3],
               "focus":      parsed.get("focus",     fallback.focus),
               "score":      max(0, min(100, int(parsed.get("score", score)))),
               "ai_used":    True,
            }
        )

    except Exception as e:
        # AI failed — return heuristic fallback gracefully
        print(f"Weekly summary AI error: {e}")
        return fallback