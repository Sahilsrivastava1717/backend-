"""
Reports Endpoints — Leads per user & per offer code (admin only)
"""
from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

CLOSED_STATUSES = {"closed_won", "closed"}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/overview")
async def get_reports_overview(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()

    # ---- Non-admin users (sales reps) ----
    sales_users = list(db["users"].find({"is_admin": {"$ne": True}}, {"name": 1}))

    # ---- Leads (only fields we need) ----
    leads = list(db["leads"].find(
        {}, {"created_by": 1, "status": 1, "offer_code": 1}
    ))

    # ---- Leads per user ----
    by_user = []
    for u in sales_users:
        uid = str(u["_id"])
        first_name = (u.get("name") or "").split(" ")[0] or "Unknown"
        user_leads = [l for l in leads if str(l.get("created_by")) == uid]
        closed = sum(1 for l in user_leads if l.get("status") in CLOSED_STATUSES)
        by_user.append({"name": first_name, "leads": len(user_leads), "closed": closed})

    # ---- Leads per offer code ----
    codes = sorted({l.get("offer_code") for l in leads if l.get("offer_code")})
    by_code = []
    for code in codes:
        code_leads = [l for l in leads if l.get("offer_code") == code]
        closed = sum(1 for l in code_leads if l.get("status") in CLOSED_STATUSES)
        by_code.append({"code": code, "leads": len(code_leads), "closed": closed})

    return {"by_user": by_user, "by_code": by_code}