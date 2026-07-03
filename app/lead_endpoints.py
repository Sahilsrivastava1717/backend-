"""
Lead Endpoints (CRM)
Create, list, update, delete leads — domain-scoped like tasks/chat.
"""
from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime
from bson import ObjectId
from app.lead_models import LeadCreate, LeadUpdate, LeadResponse, BulkDeleteRequest
from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])


def _leads_collection():
    return get_db()["leads"]


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _domain_user_ids(current_user: dict) -> list[str]:
    from app.mongodb import get_users_collection
    domain = _domain_of(current_user.get("email", ""))
    if not domain:
        return [current_user["id"]]
    escaped = domain.replace(".", "\\.")
    users = get_users_collection().find({"email": {"$regex": f"@{escaped}$", "$options": "i"}})
    return [str(u["_id"]) for u in users]


def _name_map(user_ids: list[str]) -> dict:
    from app.mongodb import get_users_collection
    users = get_users_collection().find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}})
    return {
        str(u["_id"]): (u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0])
        for u in users
    }


def _to_response(doc: dict, name_map: dict) -> LeadResponse:
    return LeadResponse(
        id=str(doc["_id"]),
        name=doc.get("name", ""),
        email=doc.get("email"),
        phone=doc.get("phone"),
        company=doc.get("company"),
        industry=doc.get("industry"),
        linkedin=doc.get("linkedin"),
        offer_code=doc.get("offer_code"),
        notes=doc.get("notes"),
        status=doc.get("status", "new"),
        email_status=doc.get("email_status", "not_sent"),
        call_status=doc.get("call_status", "not_called"),
        onboarded=doc.get("onboarded", False),
        follow_up_date=doc.get("follow_up_date"),
        created_by=doc.get("created_by", ""),
        created_by_name=name_map.get(doc.get("created_by"), "Unknown"),
        created_at=doc.get("created_at", datetime.utcnow()),
        updated_at=doc.get("updated_at", datetime.utcnow()),
        last_activity_at=doc.get("last_activity_at", doc.get("updated_at", datetime.utcnow())),
    )


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(data: LeadCreate, current_user: dict = Depends(get_current_user)):
    now = datetime.utcnow()
    doc = {
        **data.dict(),
        "status": "new",
        "email_status": "not_sent",
        "call_status": "not_called",
        "onboarded": False,
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
    }
    result = _leads_collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    name_map = _name_map([current_user["id"]])
    return _to_response(doc, name_map)


@router.get("", response_model=list[LeadResponse])
async def list_leads(current_user: dict = Depends(get_current_user)):
    user_ids = _domain_user_ids(current_user)
    docs = list(_leads_collection().find({"created_by": {"$in": user_ids}}).sort("updated_at", -1))
    name_map = _name_map(user_ids)
    return [_to_response(d, name_map) for d in docs]


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str, current_user: dict = Depends(get_current_user)):
    doc = _leads_collection().find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    user_ids = _domain_user_ids(current_user)
    if doc.get("created_by") not in user_ids:
        raise HTTPException(status_code=403, detail="Not allowed")
    return _to_response(doc, _name_map(user_ids))


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(lead_id: str, data: LeadUpdate, current_user: dict = Depends(get_current_user)):
    col = _leads_collection()
    doc = col.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    user_ids = _domain_user_ids(current_user)
    if doc.get("created_by") not in user_ids:
        raise HTTPException(status_code=403, detail="Not allowed")

    update_data = data.dict(exclude_unset=True)
    now = datetime.utcnow()
    update_data["updated_at"] = now
    # Any manual edit counts as activity; status/email/call status changes especially so
    if any(k in update_data for k in ("status", "email_status", "call_status", "notes")):
        update_data["last_activity_at"] = now

    col.update_one({"_id": ObjectId(lead_id)}, {"$set": update_data})
    updated = col.find_one({"_id": ObjectId(lead_id)})
    return _to_response(updated, _name_map(user_ids))


@router.delete("/{lead_id}")
async def delete_lead(lead_id: str, current_user: dict = Depends(get_current_user)):
    col = _leads_collection()
    doc = col.find_one({"_id": ObjectId(lead_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    user_ids = _domain_user_ids(current_user)
    if not current_user.get("is_admin", False) and doc.get("created_by") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not allowed")
    col.delete_one({"_id": ObjectId(lead_id)})
    return {"message": "Lead deleted"}


@router.post("/bulk-delete")
async def bulk_delete_leads(data: BulkDeleteRequest, current_user: dict = Depends(get_current_user)):
    col = _leads_collection()
    object_ids = [ObjectId(i) for i in data.ids]
    query = {"_id": {"$in": object_ids}}
    if not current_user.get("is_admin", False):
        query["created_by"] = current_user["id"]
    result = col.delete_many(query)
    return {"deleted": result.deleted_count}