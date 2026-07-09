"""
Employees & Consultants Endpoints
Admin-only. Creates a login account (like admin_users.py) + an employee
record, with optional initial documents stored as base64 in Mongo.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from bson import ObjectId

from app.auth_utils import get_current_user, hash_password, get_users_collection
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/employees", tags=["employees"])

EMPLOYMENT_TYPES = {"fte", "contractor", "intern", "part_time"}
EMPLOYEE_STATUSES = {"onboarding", "active", "on_leave", "offboarding", "terminated"}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _emp_col():  return get_db()["employees"]
def _doc_col():  return get_db()["employee_documents"]
def _act_col():  return get_db()["employee_activity"]


def _log_activity(employee_id: str, actor_id: str, type_: str, meta: dict = None):
    _act_col().insert_one({
        "employee_id": employee_id, "actor_id": actor_id, "type": type_,
        "meta": meta or {}, "created_at": datetime.now(timezone.utc),
    })


class DocumentIn(BaseModel):
    doc_type: str = "offer_letter"
    title: str
    file_data: Optional[str] = None
    file_name: Optional[str] = None
    url: Optional[str] = None


class EmployeeCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    temp_password: str = Field(..., min_length=8, max_length=128)
    job_title: Optional[str] = None
    employee_code: Optional[str] = None
    employment_type: str = "fte"
    status: str = "onboarding"
    department: Optional[str] = None
    reporting_manager_id: Optional[str] = None
    client_name: Optional[str] = None
    visa_status: Optional[str] = None
    visa_expiry: Optional[str] = None
    start_date: Optional[str] = None
    documents: List[DocumentIn] = []


class EmployeeUpdate(BaseModel):
    employee_code: Optional[str] = None
    employment_type: Optional[str] = None
    status: Optional[str] = None
    department: Optional[str] = None
    reporting_manager_id: Optional[str] = None
    client_name: Optional[str] = None
    visa_status: Optional[str] = None
    visa_expiry: Optional[str] = None
    start_date: Optional[str] = None


@router.get("")
async def list_employees(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()

    emps = list(_emp_col().find({}).sort("created_at", -1))
    user_ids = [e["user_id"] for e in emps if e.get("user_id")]
    users = {str(u["_id"]): u for u in db["users"].find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}})}

    doc_counts = {}
    for d in _doc_col().find({"employee_id": {"$in": [str(e["_id"]) for e in emps]}}):
        c = doc_counts.setdefault(d["employee_id"], {"total": 0, "expiring": 0, "expired": 0})
        c["total"] += 1
        if d.get("status") == "expiring_soon": c["expiring"] += 1
        if d.get("status") == "expired": c["expired"] += 1

    items = []
    for e in emps:
        resp = _to_resp(e)
        u = users.get(e.get("user_id"))
        resp["profile"] = {"name": u.get("full_name"), "email": u.get("email")} if u else None
        resp["doc_stats"] = doc_counts.get(str(e["_id"]), {"total": 0, "expiring": 0, "expired": 0})
        items.append(resp)

    counts = {
        "total": len(emps),
        "active": sum(1 for e in emps if e.get("status") == "active"),
        "onboarding": sum(1 for e in emps if e.get("status") == "onboarding"),
        "doc_alerts": sum(1 for e in emps if doc_counts.get(str(e["_id"]), {}).get("expiring", 0) or doc_counts.get(str(e["_id"]), {}).get("expired", 0)),
    }
    return {"items": items, "counts": counts}


@router.post("", status_code=201)
async def create_employee(data: EmployeeCreate, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if data.employment_type not in EMPLOYMENT_TYPES:
        raise HTTPException(400, detail="Invalid employment type")
    if data.status not in EMPLOYEE_STATUSES:
        raise HTTPException(400, detail="Invalid status")

    users_col = get_users_collection()
    admin_domain = _domain_of(current_user.get("email", ""))
    new_domain = _domain_of(data.email)
    if admin_domain and new_domain and admin_domain != new_domain:
        raise HTTPException(400, detail=f"New employee email must belong to @{admin_domain}")
    if users_col.find_one({"email": data.email}):
        raise HTTPException(400, detail="Email already registered")

    base_username = data.email.split("@")[0].lower().replace(".", "_")
    username = base_username
    suffix = 1
    while users_col.find_one({"username": username}):
        username = f"{base_username}{suffix}"
        suffix += 1

    now = datetime.now(timezone.utc)
    user_doc = {
        "username": username, "email": data.email, "full_name": data.full_name,
        "password_hash": hash_password(data.temp_password), "temp_password": data.temp_password,
        "role": None, "is_active": True, "is_admin": False,
        "created_at": now, "offer_code": None,
        "avatar_url": None, "job_title": data.job_title, "designation": None,
    }
    user_result = users_col.insert_one(user_doc)
    user_id = str(user_result.inserted_id)

    emp_doc = {
        "user_id": user_id, "employee_code": data.employee_code or None,
        "employment_type": data.employment_type, "status": data.status,
        "department": data.department, "reporting_manager_id": data.reporting_manager_id,
        "client_name": data.client_name, "visa_status": data.visa_status,
        "visa_expiry": data.visa_expiry, "start_date": data.start_date,
        "created_by": current_user["id"], "created_at": now, "updated_at": now,
    }
    emp_result = _emp_col().insert_one(emp_doc)
    employee_id = str(emp_result.inserted_id)

    for doc in data.documents:
        _doc_col().insert_one({
            "employee_id": employee_id, "doc_type": doc.doc_type, "title": doc.title,
            "file_data": doc.file_data, "file_name": doc.file_name, "url": doc.url,
            "status": "valid", "uploaded_by": current_user["id"], "created_at": now,
        })

    _log_activity(employee_id, current_user["id"], "created")

    emp_doc["_id"] = emp_result.inserted_id
    resp = _to_resp(emp_doc)
    resp["profile"] = {"name": data.full_name, "email": data.email}
    return resp


@router.get("/managers/list")
async def list_managers(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    domain = _domain_of(current_user.get("email", ""))
    users = get_users_collection().find({"email": {"$regex": f"@{domain}$", "$options": "i"}})
    return [{"id": str(u["_id"]), "name": u.get("full_name") or u.get("username")} for u in users]


@router.get("/{employee_id}")
async def get_employee(employee_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()
    try:
        oid = ObjectId(employee_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")
    e = _emp_col().find_one({"_id": oid})
    if not e:
        raise HTTPException(404, detail="Employee not found")

    u = db["users"].find_one({"_id": ObjectId(e["user_id"])}) if e.get("user_id") else None
    docs = list(_doc_col().find({"employee_id": employee_id}).sort("created_at", -1))
    activity = list(_act_col().find({"employee_id": employee_id}).sort("created_at", -1).limit(50))

    resp = _to_resp(e)
    resp["profile"] = {
        "name": u.get("full_name"), "email": u.get("email"), "job_title": u.get("job_title"),
    } if u else None
    resp["documents"] = [_to_resp(d) for d in docs]
    resp["activity"] = [_to_resp(a) for a in activity]
    return resp


@router.patch("/{employee_id}")
async def update_employee(employee_id: str, data: EmployeeUpdate, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    update = {k: v for k, v in data.dict(exclude_unset=True).items()}
    update["updated_at"] = datetime.now(timezone.utc)
    result = _emp_col().update_one({"_id": ObjectId(employee_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Employee not found")
    _log_activity(employee_id, current_user["id"], "updated", update)
    return {"message": "Updated"}


@router.post("/{employee_id}/mark-onboarded")
async def mark_onboarded(employee_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    result = _emp_col().update_one({"_id": ObjectId(employee_id)}, {"$set": {"status": "active", "updated_at": datetime.now(timezone.utc)}})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Employee not found")
    _log_activity(employee_id, current_user["id"], "marked_onboarded")
    return {"message": "Marked as onboarded"}


@router.delete("/{employee_id}")
async def delete_employee(employee_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    result = _emp_col().delete_one({"_id": ObjectId(employee_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Employee not found")
    _doc_col().delete_many({"employee_id": employee_id})
    _act_col().delete_many({"employee_id": employee_id})
    return {"message": "Deleted"}


@router.post("/{employee_id}/documents", status_code=201)
async def add_document(employee_id: str, data: DocumentIn, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    if not _emp_col().find_one({"_id": ObjectId(employee_id)}):
        raise HTTPException(404, detail="Employee not found")
    doc = {
        "employee_id": employee_id, "doc_type": data.doc_type, "title": data.title,
        "file_data": data.file_data, "file_name": data.file_name, "url": data.url,
        "status": "valid", "uploaded_by": current_user["id"], "created_at": datetime.now(timezone.utc),
    }
    result = _doc_col().insert_one(doc)
    doc["_id"] = result.inserted_id
    _log_activity(employee_id, current_user["id"], "document_added", {"title": data.title})
    return _to_resp(doc)


@router.delete("/{employee_id}/documents/{doc_id}")
async def delete_document(employee_id: str, doc_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    result = _doc_col().delete_one({"_id": ObjectId(doc_id), "employee_id": employee_id})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Document not found")
    return {"message": "Deleted"}