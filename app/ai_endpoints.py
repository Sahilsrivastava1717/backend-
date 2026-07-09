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
                f'to be clearer, more actionable, and professional. Keep it concise (50 characters max). '
                f'No bullet points or markdown. Return only the refined description text, nothing else.\n\n'
                f'Task title: "{data.title}"\n'
                f'Current description: "{data.description}"'
            )
        else:
            prompt = (
                f'You are a task description writer. Write a clear, concise, and actionable description '
                f'1 sentence for the following task. No bullet points or markdown. '
                f'Return only the description text, nothing else.\n\n'
                f'Task title: "{data.title}"'
            )

        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=100,
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

# ══════════════════════════════════════════════════════════════════════════════
# AI Day Planner
# ══════════════════════════════════════════════════════════════════════════════

class PlanRequest(BaseModel):
    user_id: str
    date: str  # "YYYY-MM-DD"


class PlannedTask(BaseModel):
    title: str
    description: str
    rationale: str
    category: str
    priority: str  # high | medium | low
    estimated_minutes: int


class DayPlanResponse(BaseModel):
    user_id: str
    name: str
    date: str
    focus_theme: str
    summary: str
    insights: List[str]
    tasks: List[PlannedTask]
    ai_used: bool


def _require_admin_ai(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.post("/day-plan", response_model=DayPlanResponse)
async def generate_day_plan(data: PlanRequest, current_user: dict = Depends(get_current_user)):
    """Analyze a member's recent tasks/completion rate and suggest a focused
    plan for the given date. Admin-only (mirrors the reference app)."""
    _require_admin_ai(current_user)
    from app.mongodb import get_db
    from bson import ObjectId

    db = get_db()
    try:
        member = db["users"].find_one({"_id": ObjectId(data.user_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    name = member.get("full_name") or member.get("username") or "Member"
    role = member.get("role") or "sales"

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    fourteen_days_ago = now_utc - timedelta(days=14)

    all_tasks = list(db["tasks"].find({"assigned_to": str(member["_id"])}))
    recent_tasks = [
        t for t in all_tasks
        if t.get("created_at") and (t["created_at"] if not t["created_at"].tzinfo else t["created_at"].replace(tzinfo=None)) >= fourteen_days_ago
    ]
    completed_recent = [t for t in recent_tasks if t.get("status") == "done"]
    active_tasks = [t for t in all_tasks if t.get("status") not in ("done", "cancelled")]
    overdue_tasks = [
        t for t in active_tasks
        if t.get("due_date") and (t["due_date"] if not t["due_date"].tzinfo else t["due_date"].replace(tzinfo=None)) < now_utc
    ]

    completion_rate = round((len(completed_recent) / len(recent_tasks)) * 100) if recent_tasks else 0

    # ── Heuristic insights ───────────────────────────────────────────────────
    insights = []
    if len(recent_tasks) == 0:
        insights.append(f"Zero completion rate over last 14 days suggests a need for manageable, high-success tasks.")
    elif completion_rate == 0:
        insights.append(f"Zero completion rate over last 14 days suggests a need for manageable, high-success tasks.")
    if len(active_tasks) == 0:
        insights.append("Lack of open tasks indicates a fresh start or transition period.")
    if overdue_tasks:
        insights.append(f"{len(overdue_tasks)} overdue task{'s' if len(overdue_tasks) != 1 else ''} — prioritize clearing backlog before new work.")
    if not insights:
        insights.append("Focusing on environment and documentation first prevents mid-sprint blockers.")
    insights = insights[:3]

    role_templates = {
        "developer": [
            ("Local Development Environment Setup", "Configure local development environment, including IDE extensions, Docker containers, and environment variables.", "Ensures a functional workspace to prevent technical friction during coding tasks.", "Environment", "high", 90),
            ("Verify Core System Boilerplate", "Execute the 'Hello World' equivalent of the current project, verifying database connectivity and API health checks.", "Verifies that the foundational architecture is operational before complex coding begins.", "Development", "high", 60),
            ("Technical Spec and Requirements Review", "Read the latest project requirements, architectural diagrams, and API specifications.", "Aligns technical implementation with stakeholder expectations and design patterns.", "Documentation Review", "medium", 45),
            ("Scaffold Feature Module Architecture", "Scaffold the first feature module (models and routes) based on the prioritized backlog.", "Starts the development cycle for the next major feature delivery.", "Development", "medium", 120),
            ("Write Unit Tests for Core Utilities", "Add unit test coverage for shared utility functions and helpers.", "Reduces regression risk as new features are built on top of shared code.", "Testing", "low", 60),
        ],
        "seo": [
            ("Keyword Gap Analysis", "Review top competitor pages for target keywords not yet covered.", "Surfaces quick-win content opportunities with existing search demand.", "Research", "high", 60),
            ("Fix Broken Backlinks", "Check and resolve flagged broken/redirected backlinks from the tracker.", "Protects existing link equity and domain authority.", "Backlinks", "high", 45),
            ("On-page Audit — Priority Pages", "Audit meta tags, headers, and internal linking on top-traffic pages.", "Improves ranking signals on pages already receiving organic traffic.", "Audit", "medium", 90),
            ("Submit New Guest Post Pitches", "Draft and send 3 outreach pitches for guest posting opportunities.", "Builds pipeline for future high-authority backlinks.", "Outreach", "medium", 60),
            ("Content Brief for Next Blog Post", "Prepare a keyword-optimized content brief for the content team.", "Keeps the content pipeline fed with SEO-driven topics.", "Content", "low", 45),
        ],
        "content_writer": [
            ("Draft Blog Post from Content Calendar", "Write the next scheduled blog post based on the approved brief.", "Keeps publishing cadence on track for organic growth goals.", "Writing", "high", 120),
            ("Revise Content Per Editor Feedback", "Apply outstanding review comments on drafts pending approval.", "Unblocks content stuck in the review stage.", "Editing", "high", 45),
            ("Social Post Repurposing", "Turn the latest published article into 3 social media posts.", "Extends content reach without new research overhead.", "Social", "medium", 45),
            ("SEO Pass on Draft", "Add meta description, alt text, and internal links to a draft.", "Improves discoverability of new content before publishing.", "SEO", "medium", 30),
            ("Research Next Topic Cluster", "Identify 3-5 topic ideas aligned with current keyword strategy.", "Feeds the content calendar for the following weeks.", "Research", "low", 60),
        ],
        "sales": [
            ("Follow Up on Warm Leads", "Call or email leads marked 'contacted' with no activity in 3+ days.", "Prevents warm leads from going cold due to lack of follow-up.", "Outreach", "high", 60),
            ("Update CRM Pipeline Stages", "Review and correct stale lead statuses in the pipeline.", "Keeps reporting and forecasting accurate for the team.", "Admin", "medium", 30),
            ("Prospect New Leads", "Research and add 10 new qualified leads matching ICP.", "Keeps the top of funnel filled for future conversions.", "Prospecting", "medium", 90),
            ("Send Proposal to Hot Lead", "Prepare and send a tailored proposal to the highest-intent lead.", "Moves a near-close deal toward conversion.", "Closing", "high", 60),
            ("Review Lost Deals This Month", "Note reasons for lost deals to refine future pitching.", "Improves win rate through pattern recognition.", "Analysis", "low", 30),
        ],
    }
    templates = role_templates.get(role, role_templates["sales"])

    if overdue_tasks:
        tasks_out = [PlannedTask(
            title=f"Clear overdue: {t.get('title', 'Untitled task')}",
            description=t.get("description") or "Revisit and complete this overdue item.",
            rationale="Clearing overdue work first prevents backlog compounding.",
            category=t.get("category") or "Follow-up", priority="high", estimated_minutes=45,
        ) for t in overdue_tasks[:2]]
        remaining_slots = 5 - len(tasks_out)
        tasks_out += [PlannedTask(title=t[0], description=t[1], rationale=t[2], category=t[3], priority=t[4], estimated_minutes=t[5]) for t in templates[:remaining_slots]]
    else:
        tasks_out = [PlannedTask(title=t[0], description=t[1], rationale=t[2], category=t[3], priority=t[4], estimated_minutes=t[5]) for t in templates]

    focus_theme = "Development Foundation and Environment Setup" if role == "developer" else (
        "Link Building and Technical SEO" if role == "seo" else
        "Content Pipeline Momentum" if role == "content_writer" else
        "Pipeline Follow-up and Prospecting"
    )
    summary_fallback = (
        f"Since there are no active or overdue tasks, today is dedicated to establishing a stable "
        f"foundation and initial scaffolding for {name.split(' ')[0]}'s next phase of work, building "
        f"momentum after a period of inactivity."
        if not active_tasks else
        f"{name.split(' ')[0]} has {len(active_tasks)} active task{'s' if len(active_tasks) != 1 else ''} and "
        f"{len(overdue_tasks)} overdue. Today prioritizes clearing blockers before moving to new work."
    )

    ai_used = False
    focus_final, summary_final = focus_theme, summary_fallback

    api_key = os.getenv("GROQ_API_KEY")
    if api_key:
        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            system = (
                "You are a manager writing a one-paragraph daily focus summary for a team member's AI-suggested "
                "day plan. Tone: clear, motivating, specific. Return ONLY valid JSON (no markdown) with keys: "
                '{"focus_theme":"<3-6 word theme>","summary":"<2-3 sentence paragraph>"}'
            )
            user_msg = (
                f"Member: {name}, role: {role}\n"
                f"Active tasks: {len(active_tasks)}, overdue: {len(overdue_tasks)}\n"
                f"Completion rate last 14 days: {completion_rate}%\n"
                f"Planned task titles: {[t.title for t in tasks_out]}"
            )
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile", max_tokens=250,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            )
            import json
            raw = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)
            focus_final = parsed.get("focus_theme", focus_theme)
            summary_final = parsed.get("summary", summary_fallback)
            ai_used = True
        except Exception as e:
            print(f"Day plan AI error: {e}")

    return DayPlanResponse(
        user_id=str(member["_id"]), name=name, date=data.date,
        focus_theme=focus_final, summary=summary_final,
        insights=insights, tasks=tasks_out, ai_used=ai_used,
    )


class AssignTaskRequest(BaseModel):
    user_id: str
    date: str
    task: PlannedTask


@router.post("/day-plan/assign")
async def assign_day_plan_task(data: AssignTaskRequest, current_user: dict = Depends(get_current_user)):
    _require_admin_ai(current_user)
    from app.mongodb import get_db

    db = get_db()
    due_date = datetime.strptime(f"{data.date} 18:00:00", "%Y-%m-%d %H:%M:%S")
    doc = {
        "title": data.task.title,
        "description": f"{data.task.description}\n\n💡 {data.task.rationale}\n⏱ ~{data.task.estimated_minutes} min",
        "assigned_to": data.user_id,
        "assigned_by": current_user["id"],
        "priority": data.task.priority,
        "status": "pending",
        "category": data.task.category,
        "due_date": due_date,
        "created_at": datetime.utcnow(),
    }
    result = db["tasks"].insert_one(doc)
    db["notifications"].insert_one({
        "user_id": data.user_id, "type": "task_assigned",
        "title": "New task from your day plan", "message": data.task.title,
        "link": "/myTasks", "data": {}, "read": False, "created_at": datetime.utcnow(),
    })
    return {"task_id": str(result.inserted_id), "message": "Task assigned"}


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