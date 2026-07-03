from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timedelta, timezone
from app.dashboard_models import (
    DashboardResponse, DashboardStats, DueTodaySection,
    UpcomingSection, RecentlyCompletedSection, DashboardTask,
    TasksByStatusResponse, TaskCompletionMetrics,
    TasksByPriorityResponse, UpcomingDeadlinesResponse
)
from app.task_crud import TaskCRUD
from app.auth_utils import get_current_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

IST = timezone(timedelta(hours=5, minutes=30))


def task_to_dashboard(task: dict) -> DashboardTask:
    return DashboardTask(
        id=task.get("id", ""),
        title=task.get("title", ""),
        status=task.get("status", "pending"),
        priority=task.get("priority", "medium"),
        due_date=task.get("due_date"),
        category=task.get("category"),
        completed_at=task.get("completed_at"),
        completion_remarks=task.get("completion_remarks"),
    )


@router.get("", response_model=DashboardResponse)
async def get_dashboard(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    username = current_user.get("full_name") or current_user.get("username", "User")

    hour = datetime.now().hour
    if hour < 12:
        greeting = f"Good morning, {username}!"
    elif hour < 17:
        greeting = f"Good afternoon, {username}!"
    else:
        greeting = f"Good evening, {username}!"

    stats = DashboardStats(**TaskCRUD.get_dashboard_stats(user_id=user_id))
    due_today = [task_to_dashboard(t) for t in TaskCRUD.get_due_today_tasks(user_id=user_id)]
    upcoming = [task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(user_id=user_id)]
    completed = [task_to_dashboard(t) for t in TaskCRUD.get_completed_tasks(user_id=user_id)]

    return DashboardResponse(
        greeting=greeting,
        stats=stats,
        due_today=DueTodaySection(tasks=due_today, count=len(due_today)),
        upcoming=UpcomingSection(tasks=upcoming, count=len(upcoming)),
        recently_completed=RecentlyCompletedSection(tasks=completed, count=len(completed)),
    )


@router.get("/stats", response_model=DashboardStats)
async def get_stats(current_user: dict = Depends(get_current_user)):
    return DashboardStats(**TaskCRUD.get_dashboard_stats(user_id=current_user["id"]))


@router.get("/tasks-by-status", response_model=TasksByStatusResponse)
async def get_tasks_by_status(current_user: dict = Depends(get_current_user)):
    from app.task_models import TaskStatus
    result = {}
    for s in TaskStatus:
        result[s.value] = TaskCRUD.get_tasks_by_status(s, user_id=current_user["id"])
    return TasksByStatusResponse(tasks_by_status=result)


@router.get("/completion-metrics", response_model=TaskCompletionMetrics)
async def get_completion_metrics(current_user: dict = Depends(get_current_user)):
    stats = TaskCRUD.get_dashboard_stats(user_id=current_user["id"])
    total = stats["total_assigned"]
    completed = stats["completed"]
    rate = round((completed / total * 100), 1) if total > 0 else 0.0
    return TaskCompletionMetrics(
        total_tasks=total,
        completed_tasks=completed,
        completion_rate=rate,
        pending_tasks=stats["pending"],
        in_progress_tasks=stats["in_progress"],
        overdue_tasks=stats["backlog_overdue"],
    )


@router.get("/priority-breakdown", response_model=TasksByPriorityResponse)
async def get_priority_breakdown(current_user: dict = Depends(get_current_user)):
    from app.mongodb import get_tasks_collection
    collection = get_tasks_collection()
    user_id = current_user["id"]
    priorities = ["low", "medium", "high", "urgent"]
    breakdown = {p: collection.count_documents({"priority": p, "assigned_to": user_id}) for p in priorities}
    return TasksByPriorityResponse(priority_breakdown=breakdown)


@router.get("/upcoming-deadlines", response_model=UpcomingDeadlinesResponse)
async def get_upcoming_deadlines(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    return UpcomingDeadlinesResponse(
        next_7_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(user_id=user_id, days=7)],
        next_14_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(user_id=user_id, days=14)],
        next_30_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(user_id=user_id, days=30)],
    )


@router.get("/summary")
async def get_summary(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    stats = TaskCRUD.get_dashboard_stats(user_id=user_id)
    due_today = len(TaskCRUD.get_due_today_tasks(user_id=user_id))
    return {**stats, "due_today": due_today, "timestamp": datetime.utcnow().isoformat()}


# ── Admin overview ────────────────────────────────────────────────────────────
def _domain_users(current_user: dict):
    from app.mongodb import get_users_collection
    email = current_user.get("email", "")
    domain = email.split("@")[-1] if "@" in email else None
    if not domain:
        return []
    escaped = domain.replace(".", "\\.")
    return list(get_users_collection().find({"email": {"$regex": f"@{escaped}$", "$options": "i"}}))


@router.get("/admin-overview")
async def admin_overview(current_user: dict = Depends(get_current_user)):
    """Org-wide dashboard for admins: team task stats, live attendance, activity feed."""
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.mongodb import get_db
    from bson import ObjectId

    db = get_db()
    users = _domain_users(current_user)
    user_ids = [str(u["_id"]) for u in users]
    user_map = {
        str(u["_id"]): (u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0])
        for u in users
    }

    tasks_col = db["tasks"]
    all_tasks = list(tasks_col.find({"assigned_to": {"$in": user_ids}}))
    now = datetime.utcnow()

    def to_task(t):
        return {
            "id": str(t["_id"]),
            "title": t.get("title", ""),
            "status": t.get("status", "pending"),
            "priority": t.get("priority", "medium"),
            "due_date": t.get("due_date"),
            "completed_at": t.get("completed_at"),
            "assigned_to": t.get("assigned_to"),
            "assigned_to_name": user_map.get(t.get("assigned_to"), "Unknown"),
        }

    total = len(all_tasks)
    pending = sum(1 for t in all_tasks if t.get("status") == "pending")
    in_progress = sum(1 for t in all_tasks if t.get("status") == "in_progress")
    completed = sum(1 for t in all_tasks if t.get("status") == "done")
    backlog = sum(
        1 for t in all_tasks
        if t.get("status") not in ("done", "cancelled") and t.get("due_date") and t["due_date"] < now
    )

    def is_today(dt):
        if not dt:
            return False
        return dt.year == now.year and dt.month == now.month and dt.day == now.day

    due_today = [to_task(t) for t in all_tasks if t.get("status") not in ("done", "cancelled") and is_today(t.get("due_date"))]
    upcoming = sorted(
        [t for t in all_tasks if t.get("status") not in ("done", "cancelled") and t.get("due_date") and t["due_date"] > now and not is_today(t.get("due_date"))],
        key=lambda t: t["due_date"]
    )[:6]
    upcoming = [to_task(t) for t in upcoming]
    recent_done = sorted(
        [t for t in all_tasks if t.get("status") == "done" and t.get("completed_at")],
        key=lambda t: t["completed_at"], reverse=True
    )[:5]
    recent_done = [to_task(t) for t in recent_done]

    # Live team attendance — today's IST window
    curr_ist = datetime.now(IST)
    day_start_utc = curr_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc = curr_ist.replace(hour=23, minute=59, second=59, microsecond=999999).astimezone(timezone.utc).replace(tzinfo=None)

    sessions = list(db["attendance_sessions"].find({
        "user_id": {"$in": [ObjectId(uid) for uid in user_ids]},
        "login_at": {"$gte": day_start_utc, "$lte": day_end_utc},
    }))
    sessions_by_user = {}
    for s in sessions:
        uid = str(s["user_id"])
        # keep earliest login / latest logout per user for the day
        entry = sessions_by_user.setdefault(uid, {"checkin": None, "checkout": None, "active": False})
        login_ist = (s["login_at"].replace(tzinfo=timezone.utc)).astimezone(IST)
        if entry["checkin"] is None or login_ist < entry["checkin"]:
            entry["checkin"] = login_ist
        if s.get("logout_at") is None:
            entry["active"] = True
        else:
            logout_ist = (s["logout_at"].replace(tzinfo=timezone.utc)).astimezone(IST)
            if entry["checkout"] is None or logout_ist > entry["checkout"]:
                entry["checkout"] = logout_ist

    team = []
    for u in users:
        uid = str(u["_id"])
        entry = sessions_by_user.get(uid)
        last_seen = u.get("last_seen")
        team.append({
            "id": uid,
            "name": user_map[uid],
            "email": u.get("email", ""),
            "is_admin": u.get("is_admin", False),
            "last_seen": last_seen,
            "checked_in": entry is not None,
            "on_shift": bool(entry and entry["active"]),
            "checkin_time": entry["checkin"].isoformat() if entry and entry["checkin"] else None,
            "checkout_time": entry["checkout"].isoformat() if entry and entry["checkout"] else None,
        })
    team = [t for t in team if not t["is_admin"]]

    return {
        "greeting": _greeting(current_user),
        "stats": {
            "total_assigned": total, "pending": pending, "in_progress": in_progress,
            "completed": completed, "backlog_overdue": backlog,
        },
        "due_today": {"tasks": due_today, "count": len(due_today)},
        "upcoming": {"tasks": upcoming, "count": len(upcoming)},
        "recently_completed": {"tasks": recent_done, "count": len(recent_done)},
        "team": team,
        "team_size": len(team),
        "on_shift_count": sum(1 for t in team if t["on_shift"]),
    }


def _greeting(current_user: dict) -> str:
    username = current_user.get("full_name") or current_user.get("username", "Admin")
    hour = datetime.now().hour
    if hour < 12:
        return f"Good morning, {username}!"
    if hour < 17:
        return f"Good afternoon, {username}!"
    return f"Good evening, {username}!"