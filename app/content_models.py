from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ContentCategory(str, Enum):
    blog_post    = "blog_post"
    social_post  = "social_post"
    website_copy = "website_copy"
    other        = "other"


class ContentStatus(str, Enum):
    draft     = "draft"
    in_review = "in_review"
    approved  = "approved"
    published = "published"
    archived  = "archived"


class ContentCreate(BaseModel):
    title: str
    category: ContentCategory
    platform: Optional[str] = None
    custom_category: Optional[str] = None
    brief: Optional[str] = None
    content_html: Optional[str] = ""
    content_json: Optional[dict] = None


class ContentUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[ContentStatus] = None
    content_html: Optional[str] = None
    content_json: Optional[dict] = None
    word_count: Optional[int] = None
    char_count: Optional[int] = None
    platform: Optional[str] = None
    custom_category: Optional[str] = None
    share_enabled: Optional[bool] = None
    assigned_reviewer_id: Optional[str] = None


class ContentResponse(BaseModel):
    id: str
    title: str
    category: ContentCategory
    status: ContentStatus
    platform: Optional[str] = None
    custom_category: Optional[str] = None
    brief: Optional[str] = None
    content_html: Optional[str] = None
    content_json: Optional[dict] = None
    word_count: int = 0
    char_count: int = 0
    share_enabled: bool = False
    owner_id: str
    owner_name: Optional[str] = None
    assigned_reviewer_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ContentListResponse(BaseModel):
    documents: List[ContentResponse]
    total: int