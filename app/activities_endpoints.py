"""
Lead Endpoints (CRM)
Create, list, update, delete leads — domain-scoped like tasks/chat.
Now also logs an activity entry on every meaningful lead action, and
exposes a paginated team activity feed (excludes admin-authored activity,
matching the "team actions" framing).
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from datetime import datetime
from bson import ObjectId
from app.lead_models import LeadCreate, LeadUpdate, LeadResponse, BulkDeleteRequest
from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

ACTIVITY_TYPES = {"call", "email", "message", "meeting"}


def _leads_collection():
    return get_db()["leads"]


def _activity_collection():
    return get_db()["lead_activities"]


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


def _admin_ids(user_ids: list[str]) -> set:
    from app.mongodb import get_users_collection
    admins = get_users_collection().find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}, "is_admin": True})
    return {str(a["_id"]) for a in admins}


def _name_map(user_ids: list[str]) -> dict:
    from app.mongodb import get_users_collection
    users = get_users_collection().find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}})
    return {
        str(u["_id"]): (u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0])
        for u in users
    }


def _log_activity(lead_id: str, lead_name: str, user_id: str, type_: str, notes: str = None):
    _activity_collection().insert_one({
        "lead_id": lead_id, "lead_name": lead_name, "user_id": user_id,
        "type": type_ if type_ in ACTIVITY_TYPES else "message",
        "notes": notes, "created_at": datetime.utcnow(),
    })


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
    _log_activity(str(doc["_id"]), doc.get("name", ""), current_user["id"], "message", "Lead created")
    name_map = _name_map([current_user["id"]])
    return _to_response(doc, name_map)


@router.get("", response_model=list[LeadResponse])
async def list_leads(current_user: dict = Depends(get_current_user)):
    user_ids = _domain_user_ids(current_user)
    docs = list(_leads_collection().find({"created_by": {"$in": user_ids}}).sort("updated_at", -1))
    name_map = _name_map(user_ids)
    return [_to_response(d, name_map) for d in docs]


@router.get("/activities/feed")
async def activities_feed(
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Full history of team actions across leads. Excludes admin-authored
    activity, matching the reference app's 'team actions' framing."""
    user_ids = _domain_user_ids(current_user)
    admins = _admin_ids(user_ids)
    non_admin_ids = [uid for uid in user_ids if uid not in admins]

    query = {"user_id": {"$in": non_admin_ids}} if non_admin_ids else {"user_id": {"$in": []}}
    col = _activity_collection()
    total = col.count_documents(query)
    skip = page * page_size
    docs = list(col.find(query).sort("created_at", -1).skip(skip).limit(page_size))

    names = _name_map(user_ids)
    items = [{
        "id": str(d["_id"]),
        "type": d.get("type", "message"),
        "notes": d.get("notes"),
        "lead_name": d.get("lead_name", "—"),
        "user_name": names.get(d.get("user_id"), "Someone"),
        "created_at": d.get("created_at", datetime.utcnow()).isoformat(),
    } for d in docs]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


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

    lead_name = updated.get("name", "")
    if "call_status" in update_data:
        _log_activity(lead_id, lead_name, current_user["id"], "call", f"Call status → {update_data['call_status']}")
    if "email_status" in update_data:
        _log_activity(lead_id, lead_name, current_user["id"], "email", f"Email status → {update_data['email_status']}")
    if "status" in update_data:
        _log_activity(lead_id, lead_name, current_user["id"], "message", f"Status → {update_data['status']}")
    if "notes" in update_data and update_data["notes"]:
        _log_activity(lead_id, lead_name, current_user["id"], "message", update_data["notes"])

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