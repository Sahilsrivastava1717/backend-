"""
Companies Endpoints
Admin only. Manage workspaces (companies) — list, create, update.
Scoped per-admin: an admin only ever sees/edits the companies they created —
one admin's companies never show up for, or can be touched by, another admin.
"""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/companies", tags=["companies"])


def _require_super_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(403, detail="Admins only")


def _col():
    return get_db()["companies"]


def _to_resp(c: dict) -> dict:
    c = {**c}
    c["id"] = str(c.pop("_id"))
    for k in ("created_at", "updated_at"):
        if isinstance(c.get(k), datetime):
            c[k] = c[k].isoformat()
    return c


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=60)
    logo_url: Optional[str] = None
    active: bool = True


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    logo_url: Optional[str] = None
    active: Optional[bool] = None


@router.get("")
async def list_companies(current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user)
    # FIX: scope to companies this admin created — previously every admin
    # saw every company in the DB regardless of who created it.
    items = list(_col().find({"created_by": current_user["id"]}).sort("name", 1))
    return {"companies": [_to_resp(c) for c in items]}


@router.post("", status_code=201)
async def create_company(data: CompanyCreate, current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user)
    slug = data.slug.strip().lower()
    # FIX: slug uniqueness scoped to this admin's own companies, not global —
    # otherwise one admin's slug choice could block another admin from ever
    # using the same slug for their own, unrelated company.
    if _col().find_one({"slug": slug, "created_by": current_user["id"]}):
        raise HTTPException(400, detail="Slug already in use")

    now = datetime.now(timezone.utc)
    doc = {
        "name": data.name.strip(),
        "slug": slug,
        "logo_url": data.logo_url.strip() if data.logo_url else None,
        "active": data.active,
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }
    result = _col().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.patch("/{company_id}")
async def update_company(company_id: str, data: CompanyUpdate, current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user)
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")

    # FIX: fetch scoped to created_by so an admin can't even discover, let
    # alone edit, a company that isn't theirs (returns 404, not 403, to
    # avoid confirming the id exists at all).
    existing = _col().find_one({"_id": oid, "created_by": current_user["id"]})
    if not existing:
        raise HTTPException(404, detail="Company not found")

    update = {}
    if data.name is not None:
        update["name"] = data.name.strip()
    if data.slug is not None:
        slug = data.slug.strip().lower()
        conflict = _col().find_one({
            "slug": slug,
            "created_by": current_user["id"],
            "_id": {"$ne": oid},
        })
        if conflict:
            raise HTTPException(400, detail="Slug already in use")
        update["slug"] = slug
    if data.logo_url is not None:
        update["logo_url"] = data.logo_url.strip() or None
    if data.active is not None:
        update["active"] = data.active

    if not update:
        raise HTTPException(400, detail="Nothing to update")

    update["updated_at"] = datetime.now(timezone.utc)
    result = _col().update_one({"_id": oid, "created_by": current_user["id"]}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Company not found")

    c = _col().find_one({"_id": oid, "created_by": current_user["id"]})
    return _to_resp(c)


@router.delete("/{company_id}")
async def delete_company(company_id: str, current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user)
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")
    # FIX: delete scoped to created_by — previously any admin could delete
    # any other admin's company by id.
    result = _col().delete_one({"_id": oid, "created_by": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Company not found")
    return {"message": "Deleted"}