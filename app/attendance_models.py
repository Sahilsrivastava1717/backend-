from pydantic import BaseModel
from typing import Optional, List


class CheckInRequest(BaseModel):
    photo_url: Optional[str] = None
    note: Optional[str] = None
    work_from: Optional[str] = "office"  # "office" | "wfh"


class CheckOutRequest(BaseModel):
    photo_url: Optional[str] = None
    note: Optional[str] = None


class StandupRequest(BaseModel):
    priorities: List[str] = []

class EODRequest(BaseModel):
    completed: list[str] = []
    blockers: str = ""
    mood: str = "good"
    completed_task_ids: list[str] = []