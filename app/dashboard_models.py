from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class DashboardStats(BaseModel):
    total_assigned: int = 0
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    backlog_overdue: int = 0


class DashboardTask(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    due_date: Optional[datetime] = None
    category: Optional[str] = None
    completed_at: Optional[datetime] = None
    completion_remarks: Optional[str] = None


class DueTodaySection(BaseModel):
    tasks: List[DashboardTask]
    count: int


class UpcomingSection(BaseModel):
    tasks: List[DashboardTask]
    count: int


class RecentlyCompletedSection(BaseModel):
    tasks: List[DashboardTask]
    count: int


class DashboardResponse(BaseModel):
    greeting: str
    stats: DashboardStats
    due_today: DueTodaySection
    upcoming: UpcomingSection
    recently_completed: RecentlyCompletedSection


class TasksByStatusResponse(BaseModel):
    tasks_by_status: Dict[str, Any]


class TaskCompletionMetrics(BaseModel):
    total_tasks: int
    completed_tasks: int
    completion_rate: float
    pending_tasks: int
    in_progress_tasks: int
    overdue_tasks: int


class TasksByPriorityResponse(BaseModel):
    priority_breakdown: Dict[str, int]


class UpcomingDeadlinesResponse(BaseModel):
    next_7_days: List[DashboardTask]
    next_14_days: List[DashboardTask]
    next_30_days: List[DashboardTask]