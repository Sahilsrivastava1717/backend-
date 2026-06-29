"""
Task Models - Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    """Task status enumeration"""
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class TaskPriority(str, Enum):
    """Task priority enumeration"""
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskBase(BaseModel):
    """Base task model with common fields"""
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=2000)
    status: TaskStatus = TaskStatus.pending
    priority: TaskPriority = TaskPriority.medium
    due_date: Optional[datetime] = None
    assigned_to: str = "self"
    assigned_by: str = "self"
    category: Optional[str] = None
    completion_remarks: Optional[str] = None


class TaskCreate(TaskBase):
    """Model for creating a new task"""
    pass


class TaskUpdate(BaseModel):
    """Model for updating a task"""
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None
    category: Optional[str] = None
    completion_remarks: Optional[str] = None


class TaskMarkDone(BaseModel):
    """Model for marking a task as done"""
    completion_remarks: Optional[str] = None


class TaskResponse(TaskBase):
    """Task response model"""
    id: str = Field(alias="_id")
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """Response model for task list"""
    total: int
    items: List[TaskResponse]


class TaskStatusUpdate(BaseModel):
    """Model for updating task status"""
    status: TaskStatus