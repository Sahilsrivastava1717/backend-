
from pydantic import BaseModel
from typing import Optional


class CheckInRequest(BaseModel):
    photo_url: Optional[str] = None
    note: Optional[str] = None
    work_from: Optional[str] = "office"  # "office" | "wfh"


class CheckOutRequest(BaseModel):
    photo_url: Optional[str] = None
    note: Optional[str] = None
