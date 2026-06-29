from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum


class XPEventType(str, Enum):
    task_completed    = "task_completed"
    standup_submitted = "standup_submitted"
    eod_submitted     = "eod_submitted"
    checkin_ontime    = "checkin_ontime"
    weekly_completion = "weekly_completion"
    overdue_penalty   = "overdue_penalty"
    missed_standup    = "missed_standup"
    missed_eod        = "missed_eod"
    late_checkin      = "late_checkin"


class XPEventCreate(BaseModel):
    event_type: XPEventType
    points: int
    reason: Optional[str] = None
    target_user_id: Optional[str] = None  # admin can award to others


class XPEventResponse(BaseModel):
    id: str
    user_id: str
    event_type: str
    points: int
    reason: Optional[str] = None
    created_at: datetime


class BreakdownEntry(BaseModel):
    points: int
    count: int


class LeaderboardEntry(BaseModel):
    id: str
    username: str
    full_name: Optional[str] = None
    period_xp: int
    total_xp: int
    breakdown: Dict[str, BreakdownEntry] = {}
    recents: List[XPEventResponse] = []


class LeaderboardResponse(BaseModel):
    entries: List[LeaderboardEntry]
    period_label: str


class MyXPResponse(BaseModel):
    period_xp: int
    total_xp: int
    level: int
    level_title: str
    progress: int
    to_next: int
    recent_events: List[XPEventResponse]
    breakdown: Dict[str, BreakdownEntry] = {}


class TeamFairnessRow(BaseModel):
    team: str
    members: int
    due: int
    done_on_time: int
    rate: int
    avg_xp: int


class TeamFairnessResponse(BaseModel):
    rows: List[TeamFairnessRow]
    period_label: str