"""
Task Endpoints
API routes for task operations
"""
from fastapi import APIRouter, HTTPException, Query, status
from typing import Optional
from app.task_models import (
    TaskCreate, TaskUpdate, TaskResponse, TaskListResponse,
    TaskMarkDone, TaskStatusUpdate, TaskStatus
)
from app.task_crud import TaskCRUD

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(task: TaskCreate):
    """Create a new task"""
    result = TaskCRUD.create_task(task)
    return result


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    task_status: Optional[TaskStatus] = Query(None)
):
    """List all tasks with optional status filter"""
    if task_status:
        tasks = TaskCRUD.get_tasks_by_status(task_status)
    else:
        tasks = TaskCRUD.get_user_tasks(skip=skip, limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/status/overdue")
async def get_overdue_tasks():
    """Get all overdue tasks"""
    return {"tasks": TaskCRUD.get_overdue_tasks()}


@router.get("/status/due-today")
async def get_due_today():
    """Get tasks due today"""
    return {"tasks": TaskCRUD.get_due_today_tasks()}


@router.get("/status/upcoming")
async def get_upcoming(days: int = Query(30, ge=1, le=90)):
    """Get upcoming tasks"""
    return {"tasks": TaskCRUD.get_upcoming_tasks(days=days)}


@router.get("/status/completed")
async def get_completed(limit: int = Query(5, ge=1, le=50)):
    """Get recently completed tasks"""
    return {"tasks": TaskCRUD.get_completed_tasks(limit=limit)}


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get a specific task"""
    task = TaskCRUD.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, task: TaskUpdate):
    """Update a task"""
    result = TaskCRUD.update_task(task_id, task)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.patch("/{task_id}/status", response_model=TaskResponse)
async def update_status(task_id: str, status_update: TaskStatusUpdate):
    """Update task status"""
    result = TaskCRUD.update_task_status(task_id, status_update.status)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.post("/{task_id}/mark-done", response_model=TaskResponse)
async def mark_done(task_id: str, data: TaskMarkDone):
    """Mark task as done"""
    result = TaskCRUD.mark_task_done(task_id, data.completion_remarks)
    if not result:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Delete a task"""
    deleted = TaskCRUD.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")