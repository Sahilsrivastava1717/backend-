from fastapi import APIRouter, HTTPException, Query, status, Depends
from typing import Optional
from app.task_models import (
    TaskCreate, TaskUpdate, TaskResponse, TaskListResponse,
    TaskMarkDone, TaskStatusUpdate, TaskStatus
)
from app.task_crud import TaskCRUD
from app.auth_utils import get_current_user

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(task: TaskCreate, current_user: dict = Depends(get_current_user)):
    """
    Create a task.
    - assigned_by is always forced to the authenticated user.
    - assigned_to uses the teammate real id sent by frontend, or falls back to self.
    """
    from app.mongodb import get_tasks_collection
    from datetime import datetime

    collection = get_tasks_collection()
    task_dict = task.model_dump()

    # Always stamp who created/assigned it
    task_dict["assigned_by"] = current_user["id"]

    # "self" or missing → assign to creator; otherwise keep the real teammate id
    raw = task_dict.get("assigned_to")
    if not raw or raw == "self":
        task_dict["assigned_to"] = current_user["id"]

    task_dict["created_at"] = datetime.utcnow()
    task_dict["completed_at"] = None
    task_dict["completion_remarks"] = None

    result = collection.insert_one(task_dict)
    task_dict["id"] = str(result.inserted_id)
    task_dict.pop("_id", None)
    return task_dict


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    task_status: Optional[TaskStatus] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Tasks assigned TO the current user (their own inbox)."""
    user_id = current_user["id"]
    if task_status:
        tasks = TaskCRUD.get_tasks_by_status(task_status, user_id=user_id)
    else:
        tasks = TaskCRUD.get_user_tasks(user_id=user_id, skip=skip, limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/assigned-by-me", response_model=TaskListResponse)
async def list_tasks_assigned_by_me(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: dict = Depends(get_current_user)
):
    """Tasks the current user delegated to someone else."""
    tasks = TaskCRUD.get_tasks_assigned_by_me(
        user_id=current_user["id"], skip=skip, limit=limit
    )
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/status/overdue")
async def get_overdue_tasks(current_user: dict = Depends(get_current_user)):
    return {"tasks": TaskCRUD.get_overdue_tasks(user_id=current_user["id"])}


@router.get("/status/due-today")
async def get_due_today(current_user: dict = Depends(get_current_user)):
    return {"tasks": TaskCRUD.get_due_today_tasks(user_id=current_user["id"])}


@router.get("/status/upcoming")
async def get_upcoming(
    days: int = Query(30, ge=1, le=90),
    current_user: dict = Depends(get_current_user)
):
    return {"tasks": TaskCRUD.get_upcoming_tasks(user_id=current_user["id"], days=days)}


@router.get("/status/completed")
async def get_completed(
    limit: int = Query(5, ge=1, le=50),
    current_user: dict = Depends(get_current_user)
):
    return {"tasks": TaskCRUD.get_completed_tasks(user_id=current_user["id"], limit=limit)}


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    task = TaskCRUD.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("assigned_to") != current_user["id"] and task.get("assigned_by") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, task: TaskUpdate, current_user: dict = Depends(get_current_user)):
    existing = TaskCRUD.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if existing.get("assigned_by") != current_user["id"] and existing.get("assigned_to") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return TaskCRUD.update_task(task_id, task)


@router.patch("/{task_id}/status", response_model=TaskResponse)
async def update_status(task_id: str, status_update: TaskStatusUpdate, current_user: dict = Depends(get_current_user)):
    existing = TaskCRUD.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if existing.get("assigned_to") != current_user["id"] and existing.get("assigned_by") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return TaskCRUD.update_task_status(task_id, status_update.status)


@router.post("/{task_id}/mark-done", response_model=TaskResponse)
async def mark_done(task_id: str, data: TaskMarkDone, current_user: dict = Depends(get_current_user)):
    existing = TaskCRUD.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    if existing.get("assigned_to") != current_user["id"] and existing.get("assigned_by") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = TaskCRUD.mark_task_done(task_id, data.completion_remarks)

    from datetime import datetime
    from app.mongodb import get_db
    from app.xp_utils import xp_for_task

    priority = existing.get("priority", "medium")
    due_date = existing.get("due_date")
    on_time = due_date is None or datetime.utcnow() <= due_date
    points = xp_for_task(priority, on_time)

    get_db()["xp_events"].insert_one({
        "user_id": current_user["id"],
        "event_type": "task_completed",
        "points": points,
        "reason": f"Task: {existing.get('title', 'Unknown')}",
        "created_at": datetime.utcnow(),
    })

    return result


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    existing = TaskCRUD.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")
    # Only the assigner can delete
    if existing.get("assigned_by") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    TaskCRUD.delete_task(task_id)