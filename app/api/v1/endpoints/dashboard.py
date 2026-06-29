"""
Dashboard Endpoints
API routes for dashboard analytics and overview
"""
from fastapi import APIRouter, Query
from datetime import datetime
from typing import Optional
from app.dashboard_models import (
    DashboardResponse, DashboardStats, DueTodaySection,
    UpcomingSection, RecentlyCompletedSection, DashboardTask,
    TasksByStatusResponse, TaskCompletionMetrics,
    TasksByPriorityResponse, UpcomingDeadlinesResponse
)
from app.task_crud import TaskCRUD

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
async def get_dashboard(username: Optional[str] = Query("User")):
    """Get full dashboard data"""
    hour = datetime.now().hour
    if hour < 12:
        greeting = f"Good morning, {username}!"
    elif hour < 17:
        greeting = f"Good afternoon, {username}!"
    else:
        greeting = f"Good evening, {username}!"

    stats_data = TaskCRUD.get_dashboard_stats()
    stats = DashboardStats(**stats_data)

    due_today_tasks = [task_to_dashboard(t) for t in TaskCRUD.get_due_today_tasks()]
    upcoming_tasks = [task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks()]
    completed_tasks = [task_to_dashboard(t) for t in TaskCRUD.get_completed_tasks()]

    return DashboardResponse(
        greeting=greeting,
        stats=stats,
        due_today=DueTodaySection(tasks=due_today_tasks, count=len(due_today_tasks)),
        upcoming=UpcomingSection(tasks=upcoming_tasks, count=len(upcoming_tasks)),
        recently_completed=RecentlyCompletedSection(tasks=completed_tasks, count=len(completed_tasks)),
    )


@router.get("/stats", response_model=DashboardStats)
async def get_stats():
    """Get dashboard stats only"""
    return DashboardStats(**TaskCRUD.get_dashboard_stats())


@router.get("/tasks-by-status", response_model=TasksByStatusResponse)
async def get_tasks_by_status():
    """Get tasks grouped by status"""
    from app.task_models import TaskStatus
    result = {}
    for s in TaskStatus:
        result[s.value] = TaskCRUD.get_tasks_by_status(s)
    return TasksByStatusResponse(tasks_by_status=result)


@router.get("/completion-metrics", response_model=TaskCompletionMetrics)
async def get_completion_metrics():
    """Get completion rate and metrics"""
    stats = TaskCRUD.get_dashboard_stats()
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
async def get_priority_breakdown():
    """Get task count by priority"""
    from app.mongodb import get_tasks_collection
    collection = get_tasks_collection()
    priorities = ["low", "medium", "high", "urgent"]
    breakdown = {}
    for p in priorities:
        breakdown[p] = collection.count_documents({"priority": p})
    return TasksByPriorityResponse(priority_breakdown=breakdown)


@router.get("/upcoming-deadlines", response_model=UpcomingDeadlinesResponse)
async def get_upcoming_deadlines():
    """Get upcoming deadlines grouped by timeframe"""
    return UpcomingDeadlinesResponse(
        next_7_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(days=7)],
        next_14_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(days=14)],
        next_30_days=[task_to_dashboard(t) for t in TaskCRUD.get_upcoming_tasks(days=30)],
    )


@router.get("/summary")
async def get_summary():
    """Quick summary dict"""
    stats = TaskCRUD.get_dashboard_stats()
    due_today = len(TaskCRUD.get_due_today_tasks())
    return {**stats, "due_today": due_today, "timestamp": datetime.utcnow().isoformat()}