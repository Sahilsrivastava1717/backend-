from fastapi import APIRouter, Depends
from datetime import datetime
from app.dashboard_models import (
    DashboardResponse, DashboardStats, DueTodaySection,
    UpcomingSection, RecentlyCompletedSection, DashboardTask,
    TasksByStatusResponse, TaskCompletionMetrics,
    TasksByPriorityResponse, UpcomingDeadlinesResponse
)
from app.task_crud import TaskCRUD
from app.auth_utils import get_current_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


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