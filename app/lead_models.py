from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    interested = "interested"
    negotiation = "negotiation"
    closed_won = "closed_won"
    closed_lost = "closed_lost"


class EmailStatus(str, Enum):
    not_sent = "not_sent"
    sent = "sent"
    opened = "opened"
    replied = "replied"


class CallStatus(str, Enum):
    not_called = "not_called"
    called = "called"
    connected = "connected"
    no_response = "no_response"


class LeadCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    linkedin: Optional[str] = None
    offer_code: Optional[str] = None
    notes: Optional[str] = None
    follow_up_date: Optional[datetime] = None


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    linkedin: Optional[str] = None
    offer_code: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[LeadStatus] = None
    email_status: Optional[EmailStatus] = None
    call_status: Optional[CallStatus] = None
    onboarded: Optional[bool] = None
    follow_up_date: Optional[datetime] = None


class LeadResponse(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    linkedin: Optional[str] = None
    offer_code: Optional[str] = None
    notes: Optional[str] = None
    status: LeadStatus = LeadStatus.new
    email_status: EmailStatus = EmailStatus.not_sent
    call_status: CallStatus = CallStatus.not_called
    onboarded: bool = False
    follow_up_date: Optional[datetime] = None
    created_by: str
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class BulkDeleteRequest(BaseModel):
    ids: list[str]