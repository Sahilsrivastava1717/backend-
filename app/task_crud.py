"""
Task CRUD Operations
Database operations for tasks
"""
from datetime import datetime, timedelta
from typing import List, Optional
from bson import ObjectId
from app.mongodb import get_tasks_collection
from app.task_models import TaskCreate, TaskUpdate, TaskStatus
import logging

logger = logging.getLogger(__name__)


class TaskCRUD:
    """Task CRUD operations"""

    @staticmethod
    def create_task(task_data: TaskCreate, user_id: str = "self") -> dict:
        """Create a new task"""
        collection = get_tasks_collection()
        task_dict = task_data.model_dump()
        task_dict["assigned_to"] = user_id
        task_dict["assigned_by"] = user_id
        task_dict["created_at"] = datetime.utcnow()
        task_dict["completed_at"] = None
        task_dict["completion_remarks"] = None
        result = collection.insert_one(task_dict)
        task_dict["id"] = str(result.inserted_id)
        task_dict.pop("_id", None)
        return task_dict

    @staticmethod
    def get_task(task_id: str) -> Optional[dict]:
        """Get a task by ID"""
        collection = get_tasks_collection()
        try:
            task = collection.find_one({"_id": ObjectId(task_id)})
            if task:
                task["id"] = str(task["_id"])
                task.pop("_id", None)
            return task
        except Exception:
            return None

    @staticmethod
    def get_user_tasks(user_id: str = "self", skip: int = 0, limit: int = 50) -> List[dict]:
        """Get all tasks for a user"""
        collection = get_tasks_collection()
        tasks = list(collection.find({"assigned_to": user_id}).skip(skip).limit(limit).sort("created_at", -1))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def get_tasks_by_status(status: TaskStatus, user_id: str = "self") -> List[dict]:
        """Get tasks filtered by status"""
        collection = get_tasks_collection()
        tasks = list(collection.find({"assigned_to": user_id, "status": status}).sort("created_at", -1))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def get_overdue_tasks(user_id: str = "self") -> List[dict]:
        """Get overdue tasks"""
        collection = get_tasks_collection()
        now = datetime.utcnow()
        tasks = list(collection.find({
            "assigned_to": user_id,
            "due_date": {"$lt": now},
            "status": {"$nin": ["done", "cancelled"]}
        }).sort("due_date", 1))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def get_due_today_tasks(user_id: str = "self") -> List[dict]:
        """Get tasks due today"""
        collection = get_tasks_collection()
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        tasks = list(collection.find({
            "assigned_to": user_id,
            "due_date": {"$gte": today, "$lt": tomorrow},
            "status": {"$nin": ["done", "cancelled"]}
        }).sort("due_date", 1))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def get_upcoming_tasks(user_id: str = "self", days: int = 30) -> List[dict]:
        """Get upcoming tasks"""
        collection = get_tasks_collection()
        now = datetime.utcnow()
        future = now + timedelta(days=days)
        tasks = list(collection.find({
            "assigned_to": user_id,
            "due_date": {"$gte": now, "$lte": future},
            "status": {"$nin": ["done", "cancelled"]}
        }).sort("due_date", 1).limit(6))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def get_completed_tasks(user_id: str = "self", limit: int = 5) -> List[dict]:
        """Get recently completed tasks"""
        collection = get_tasks_collection()
        tasks = list(collection.find({
            "assigned_to": user_id,
            "status": "done"
        }).sort("completed_at", -1).limit(limit))
        for task in tasks:
            task["id"] = str(task["_id"])
            task.pop("_id", None)
        return tasks

    @staticmethod
    def update_task(task_id: str, task_data: TaskUpdate) -> Optional[dict]:
        """Update a task"""
        collection = get_tasks_collection()
        update_data = {k: v for k, v in task_data.model_dump().items() if v is not None}
        if not update_data:
            return TaskCRUD.get_task(task_id)
        try:
            collection.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
            return TaskCRUD.get_task(task_id)
        except Exception:
            return None

    @staticmethod
    def mark_task_done(task_id: str, remarks: str = None) -> Optional[dict]:
        """Mark a task as done"""
        collection = get_tasks_collection()
        try:
            collection.update_one(
                {"_id": ObjectId(task_id)},
                {"$set": {
                    "status": "done",
                    "completed_at": datetime.utcnow(),
                    "completion_remarks": remarks
                }}
            )
            return TaskCRUD.get_task(task_id)
        except Exception:
            return None

    @staticmethod
    def update_task_status(task_id: str, status: TaskStatus) -> Optional[dict]:
        """Update task status only"""
        collection = get_tasks_collection()
        try:
            update = {"status": status}
            if status == "done":
                update["completed_at"] = datetime.utcnow()
            collection.update_one({"_id": ObjectId(task_id)}, {"$set": update})
            return TaskCRUD.get_task(task_id)
        except Exception:
            return None

    @staticmethod
    def delete_task(task_id: str) -> bool:
        """Delete a task"""
        collection = get_tasks_collection()
        try:
            result = collection.delete_one({"_id": ObjectId(task_id)})
            return result.deleted_count > 0
        except Exception:
            return False

    @staticmethod
    def get_dashboard_stats(user_id: str = "self") -> dict:
        """Get dashboard statistics"""
        collection = get_tasks_collection()
        now = datetime.utcnow()
        total = collection.count_documents({"assigned_to": user_id})
        pending = collection.count_documents({"assigned_to": user_id, "status": "pending"})
        in_progress = collection.count_documents({"assigned_to": user_id, "status": "in_progress"})
        completed = collection.count_documents({"assigned_to": user_id, "status": "done"})
        overdue = collection.count_documents({
            "assigned_to": user_id,
            "due_date": {"$lt": now},
            "status": {"$nin": ["done", "cancelled"]}
        })
        return {
            "total_assigned": total,
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "backlog_overdue": overdue,
        }