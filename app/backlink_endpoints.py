"""
Backlinks Endpoints — SEO/Admin only, domain-scoped like leads/tasks.

Access control:
- Only users who are admins OR have role == "seo" may use these endpoints.
- Within that, every query is scoped to the caller's own email domain
  (org) — an admin or SEO user from one org never sees, edits, or
  deletes another org's backlinks, even though they're both admins.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId
import csv, io, httpx

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/backlinks", tags=["backlinks"])

STATUS_META = {
    "pending":  {"label": "Pending"},
    "live":     {"label": "Live"},
    "indexed":  {"label": "Indexed"},
    "broken":   {"label": "Broken"},
    "rejected": {"label": "Rejected"},
}
VALID_STATUS = set(STATUS_META.keys())


def _col():        return get_db()["backlinks"]
def _cat_col():     return get_db()["backlink_categories"]
def _checks_col():  return get_db()["backlink_status_checks"]


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _domain_user_ids(current_user: dict) -> List[str]:
    """
    All user ids belonging to the caller's own email domain (org).
    Used to scope both admins and SEO users to their own org — an
    admin no longer sees every org's data, just their own.
    """
    from app.mongodb import get_users_collection
    domain = _domain_of(current_user.get("email", ""))
    if not domain:
        return [current_user["id"]]
    users = get_users_collection().find({"email": {"$regex": f"@{domain}$", "$options": "i"}})
    return [str(u["_id"]) for u in users]


def _require_seo_or_admin(current_user: dict):
    role = current_user.get("role")
    if not (current_user.get("is_admin") or role == "seo"):
        raise HTTPException(status_code=403, detail="SEO team only")


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at", "last_checked_at", "fixed_at", "redo_requested_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _get_org_backlink(backlink_id: str, allowed_user_ids: List[str]) -> dict:
    """
    Fetch a backlink doc, but only if it belongs to someone in the
    caller's own org (allowed_user_ids). Raises 404 for both "doesn't
    exist" and "belongs to another org" — deliberately indistinguishable
    so org boundaries aren't leak-able.
    """
    try:
        oid = ObjectId(backlink_id)
    except Exception:
        raise HTTPException(400, detail="Invalid id")

    d = _col().find_one({"_id": oid})
    if not d or d.get("user_id") not in allowed_user_ids:
        raise HTTPException(404, detail="Backlink not found")
    return d


class BacklinkIn(BaseModel):
    date: Optional[str] = None
    website_url: str
    live_post_url: Optional[str] = None
    target_url: Optional[str] = None
    anchor_text: Optional[str] = None
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    status: str = "pending"
    do_follow: bool = True
    shared_with_team: bool = False
    remarks: Optional[str] = None
    category_id: Optional[str] = None
    assigned_to: Optional[str] = None
    da: Optional[int] = None
    dr: Optional[int] = None


class BacklinkUpdate(BaseModel):
    date: Optional[str] = None
    website_url: Optional[str] = None
    live_post_url: Optional[str] = None
    target_url: Optional[str] = None
    anchor_text: Optional[str] = None
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    status: Optional[str] = None
    do_follow: Optional[bool] = None
    shared_with_team: Optional[bool] = None
    remarks: Optional[str] = None
    category_id: Optional[str] = None
    assigned_to: Optional[str] = None
    da: Optional[int] = None
    dr: Optional[int] = None
    redo_requested: Optional[bool] = None
    redo_note: Optional[str] = None


class CategoryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class BulkRow(BaseModel):
    date: Optional[str] = None
    website_url: str
    category_id: Optional[str] = None
    login_email: Optional[str] = None
    login_password: Optional[str] = None
    anchor_text: Optional[str] = None
    live_post_url: Optional[str] = None


class BulkIn(BaseModel):
    rows: List[BulkRow]


class RedoIn(BaseModel):
    note: str = Field(..., min_length=5, max_length=500)
    assigned_to: str


# ── list + stats ──────────────────────────────────────────────────────────────

@router.get("")
async def list_backlinks(
    page: int = 0, page_size: int = 25,
    status: Optional[str] = None, category_id: Optional[str] = None,
    search: Optional[str] = None, mine_only: bool = False,
    website_filter: Optional[str] = None, live_filter: Optional[str] = None,
    sort_key: str = "date", sort_dir: str = "desc",
    current_user: dict = Depends(get_current_user),
):
    _require_seo_or_admin(current_user)
    col = _col()
    user_ids = _domain_user_ids(current_user)

    # Every caller — admin or SEO — is scoped to their own org's users.
    q: dict = {"user_id": {"$in": user_ids}}
    if status and status != "all":
        q["status"] = status
    if category_id and category_id != "all":
        q["category_id"] = category_id
    if mine_only:
        q["assigned_to"] = current_user["id"]
    if website_filter:
        q["website_url"] = {"$regex": website_filter, "$options": "i"}
    if live_filter:
        q["live_post_url"] = {"$regex": live_filter, "$options": "i"}
    if search:
        q["$or"] = [
            {"website_url": {"$regex": search, "$options": "i"}},
            {"live_post_url": {"$regex": search, "$options": "i"}},
            {"anchor_text": {"$regex": search, "$options": "i"}},
        ]

    total = col.count_documents(q)
    direction = 1 if sort_dir == "asc" else -1
    docs = list(col.find(q).sort(sort_key, direction).skip(page * page_size).limit(page_size))

    base_q = {"user_id": {"$in": user_ids}}
    counts = {"all": col.count_documents(base_q)}
    for s in VALID_STATUS:
        counts[s] = col.count_documents({**base_q, "status": s})

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    counts["today"] = col.count_documents({**base_q, "created_at": {"$gte": today_start}})
    counts["week"] = col.count_documents({**base_q, "created_at": {"$gte": week_ago}})

    broken = list(col.find({**base_q, "status": "broken"}).sort("last_checked_at", -1).limit(50))

    return {
        "items": [_to_resp(d) for d in docs],
        "total": total,
        "counts": counts,
        "broken": [_to_resp(d) for d in broken],
    }


@router.get("/categories")
async def list_categories(current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    cats = list(_cat_col().find({}).sort("name", 1))
    return [_to_resp(c) for c in cats]


@router.post("/categories", status_code=201)
async def create_category(data: CategoryIn, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    if _cat_col().find_one({"name": {"$regex": f"^{data.name.strip()}$", "$options": "i"}}):
        raise HTTPException(400, detail="Category already exists")
    doc = {"name": data.name.strip(), "created_by": current_user["id"], "is_default": False}
    result = _cat_col().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.get("/teammates")
async def list_teammates(current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    from app.mongodb import get_users_collection
    domain = _domain_of(current_user.get("email", ""))
    users = get_users_collection().find({"email": {"$regex": f"@{domain}$", "$options": "i"}})
    return [
        {"id": str(u["_id"]), "name": u.get("full_name") or u.get("username"), "email": u.get("email"),
         "avatar_url": u.get("avatar_url")}
        for u in users
    ]


@router.post("", status_code=201)
async def create_backlink(data: BacklinkIn, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    if data.status not in VALID_STATUS:
        raise HTTPException(400, detail="Invalid status")
    now = datetime.now(timezone.utc)
    doc = {
        **data.dict(),
        "user_id": current_user["id"],
        "created_at": now, "updated_at": now,
        "last_checked_at": None, "http_status": None,
        "redo_requested": False, "redo_requested_at": None, "redo_note": None, "fixed_at": None,
    }
    if doc.get("live_post_url"):
        # Duplicate check scoped to the caller's own org only.
        user_ids = _domain_user_ids(current_user)
        dup = _col().find_one({
            "live_post_url": {"$regex": f"^{doc['live_post_url']}$", "$options": "i"},
            "user_id": {"$in": user_ids},
        })
        if dup:
            raise HTTPException(400, detail="Duplicate live URL — already logged for your team.")
    result = _col().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.patch("/{backlink_id}")
async def update_backlink(backlink_id: str, data: BacklinkUpdate, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    _get_org_backlink(backlink_id, user_ids)  # 404s if not this org's

    update = {k: v for k, v in data.dict(exclude_unset=True).items()}
    if "status" in update and update["status"] not in VALID_STATUS:
        raise HTTPException(400, detail="Invalid status")
    update["updated_at"] = datetime.now(timezone.utc)
    result = _col().update_one({"_id": ObjectId(backlink_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Backlink not found")
    return {"message": "Updated"}


@router.post("/{backlink_id}/mark-fixed")
async def mark_fixed(backlink_id: str, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    _get_org_backlink(backlink_id, user_ids)  # 404s if not this org's

    oid = ObjectId(backlink_id)
    _col().update_one({"_id": oid}, {"$set": {
        "status": "live", "fixed_at": datetime.now(timezone.utc), "redo_requested": False,
    }})
    return {"message": "Marked as fixed"}


@router.post("/{backlink_id}/request-redo")
async def request_redo(backlink_id: str, data: RedoIn, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    _get_org_backlink(backlink_id, user_ids)  # 404s if not this org's

    # assigned_to must also be someone in the same org
    if data.assigned_to not in user_ids:
        raise HTTPException(400, detail="Can only assign to a teammate in your org")

    oid = ObjectId(backlink_id)
    result = _col().update_one({"_id": oid}, {"$set": {
        "redo_requested": True, "redo_requested_at": datetime.now(timezone.utc),
        "redo_note": data.note.strip(), "assigned_to": data.assigned_to,
    }})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Backlink not found")
    return {"message": "Re-do requested"}


@router.get("/redo-queue")
async def redo_queue(current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    q = {"redo_requested": True, "user_id": {"$in": user_ids}}
    docs = list(_col().find(q).sort("redo_requested_at", -1))
    return [_to_resp(d) for d in docs]


@router.delete("/{backlink_id}")
async def delete_backlink(backlink_id: str, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    _get_org_backlink(backlink_id, user_ids)  # 404s if not this org's

    oid = ObjectId(backlink_id)
    result = _col().delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Backlink not found")
    return {"message": "Deleted"}


@router.post("/bulk", status_code=201)
async def bulk_add(data: BulkIn, current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    now = datetime.now(timezone.utc)
    created = 0
    for row in data.rows:
        if not row.website_url or not row.website_url.strip():
            continue
        doc = {
            **row.dict(), "status": "pending", "do_follow": True, "shared_with_team": False,
            "remarks": None, "assigned_to": None, "target_url": None, "da": None, "dr": None,
            "user_id": current_user["id"], "created_at": now, "updated_at": now,
            "last_checked_at": None, "http_status": None,
            "redo_requested": False, "redo_requested_at": None, "redo_note": None, "fixed_at": None,
        }
        _col().insert_one(doc)
        created += 1
    return {"created": created}


@router.post("/check")
async def check_backlinks(
    from_date: Optional[str] = None, to_date: Optional[str] = None,
    category_ids: Optional[str] = None, status: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Checks HTTP status of live_post_url (or website_url) for matching backlinks."""
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    q: dict = {"user_id": {"$in": user_ids}}
    if from_date: q.setdefault("date", {})["$gte"] = from_date
    if to_date: q.setdefault("date", {})["$lte"] = to_date
    if category_ids:
        q["category_id"] = {"$in": category_ids.split(",")}
    if status and status != "all":
        q["status"] = status

    docs = list(_col().find(q))
    updated = 0
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        for d in docs:
            target = d.get("live_post_url") or d.get("website_url")
            if not target:
                continue
            try:
                r = await client.get(target)
                ok = r.status_code < 400
                http_status = r.status_code
            except Exception:
                ok = False
                http_status = None
            new_status = "indexed" if (ok and d.get("status") == "indexed") else ("live" if ok else "broken")
            now = datetime.now(timezone.utc)
            _col().update_one({"_id": d["_id"]}, {"$set": {
                "status": new_status, "http_status": http_status, "last_checked_at": now,
            }})
            _checks_col().insert_one({
                "backlink_id": str(d["_id"]), "http_status": http_status,
                "status": new_status, "source": "manual", "checked_at": now,
            })
            updated += 1
    return {"checked": len(docs), "updated": updated}


@router.post("/sync-sheet")
async def sync_sheet(current_user: dict = Depends(get_current_user)):
    """Stub — wire this up to your Google Sheets integration (e.g. gspread + service account)."""
    _require_seo_or_admin(current_user)
    return {"message": "Synced to Google Sheet"}


@router.get("/export.csv")
async def export_csv(current_user: dict = Depends(get_current_user)):
    _require_seo_or_admin(current_user)
    user_ids = _domain_user_ids(current_user)
    q = {"user_id": {"$in": user_ids}}
    docs = list(_col().find(q).sort("date", -1))
    cats = {str(c["_id"]): c["name"] for c in _cat_col().find({})}

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Website URL", "Live Post URL", "Target URL", "Anchor", "Category",
                "Status", "DA", "DR", "Do-Follow", "Email", "Password", "Remarks", "Last Checked", "HTTP"])
    for d in docs:
        w.writerow([
            d.get("date", ""), d.get("website_url", ""), d.get("live_post_url", ""),
            d.get("target_url", ""), d.get("anchor_text", ""), cats.get(d.get("category_id"), ""),
            d.get("status", ""), d.get("da", ""), d.get("dr", ""), "Yes" if d.get("do_follow") else "No",
            d.get("login_email", ""), d.get("login_password", ""), d.get("remarks", ""),
            d.get("last_checked_at", ""), d.get("http_status", ""),
        ])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=backlinks.csv"})