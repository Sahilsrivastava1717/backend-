"""
Dashboard Models - Pydantic schemas for dashboard endpoints
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class DashboardStats(BaseModel):
    """Dashboard statistics"""
    total_tasks: int
    pending_tasks: int
    in_progress_tasks: int
    completed_tasks: int
    overdue_tasks: int
    today_due_count: int


class DashboardTask(BaseModel):
    """Task in dashboard response"""
    id: str = Field(alias="_id")
    title: str
    priority: str
    status: str
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DueTodaySection(BaseModel):
    """Due today section"""
    count: int
    tasks: List[DashboardTask]


class UpcomingSection(BaseModel):
    """Upcoming tasks section"""
    count: int
    tasks: List[DashboardTask]


class RecentlyCompletedSection(BaseModel):
    """Recently completed tasks section"""
    count: int
    tasks: List[DashboardTask]


class DashboardResponse(BaseModel):
    """Complete dashboard response"""
    greeting: str
    username: str
    online_status: bool = True
    stats: DashboardStats
    due_today: DueTodaySection
    upcoming: UpcomingSection
    recently_completed: RecentlyCompletedSection
    generated_at: datetime


class TasksByStatusResponse(BaseModel):
    """Tasks grouped by status"""
    pending: List[DashboardTask]
    in_progress: List[DashboardTask]
    done: List[DashboardTask]
    cancelled: List[DashboardTask]


class TaskCompletionMetrics(BaseModel):
    """Task completion metrics"""
    total_tasks: int
    completed_tasks: int
    completion_rate: float  # percentage
    average_completion_time: Optional[float] = None  # in days


class TasksByPriorityResponse(BaseModel):
    """Tasks grouped by priority"""
    low: int
    medium: int
    high: int
    urgent: int


class UpcomingDeadlinesResponse(BaseModel):
    """Upcoming deadlines"""
    next_7_days: List[DashboardTask]
    next_14_days: List[DashboardTask]
    next_30_days: List[DashboardTask]