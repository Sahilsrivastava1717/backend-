
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ConversationCreate(BaseModel):
    type: str  # "dm" or "group"
    name: Optional[str] = None
    member_ids: List[str] = []


class MessageCreate(BaseModel):
    content: Optional[str] = None
    reply_to_id: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_name: Optional[str] = None
    attachment_type: Optional[str] = None


class MessageUpdate(BaseModel):
    content: str


class ReactionToggle(BaseModel):
    emoji: str
