"""
Admin Content Oversight Endpoint
Stats + filterable document list + recent activity feed, admin-only,
domain-scoped like other admin views.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta
from typing import Optional
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/content", tags=["admin-content"])

CATEGORY_META = {
    "blog_post":    {"label": "Blog post"},
    "social_post":  {"label": "Social post"},
    "website_copy": {"label": "Website copy"},
    "other":        {"label": "Other"},
}
STATUS_META = {
    "draft":     {"label": "Draft"},
    "in_review": {"label": "In review"},
    "approved":  {"label": "Approved"},
    "published": {"label": "Published"},
    "archived":  {"label": "Archived"},
}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _serialize_doc(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


@router.get("/overview")
async def content_overview(
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    owner_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    db = get_db()
    docs_col = db["content_documents"]
    users_col = db["users"]

    domain = _domain_of(current_user.get("email", ""))
    domain_users = list(users_col.find({"email": {"$regex": f"@{domain}$", "$options": "i"}})) if domain else []
    owner_ids = [str(u["_id"]) for u in domain_users]
    profiles = {
        str(u["_id"]): {"id": str(u["_id"]), "name": u.get("full_name") or u.get("username"), "email": u.get("email")}
        for u in domain_users
    }

    base_q = {"owner_id": {"$in": owner_ids}} if owner_ids else {}
    all_docs = list(docs_col.find(base_q).sort("updated_at", -1))

    stats = {
        "total": len(all_docs),
        "drafts": sum(1 for d in all_docs if d.get("status") == "draft"),
        "review": sum(1 for d in all_docs if d.get("status") == "in_review"),
        "published": sum(1 for d in all_docs if d.get("status") == "published"),
    }

    filtered = all_docs
    if category and category != "all":
        filtered = [d for d in filtered if d.get("category") == category]
    if status and status != "all":
        filtered = [d for d in filtered if d.get("status") == status]
    if owner_id and owner_id != "all":
        filtered = [d for d in filtered if d.get("owner_id") == owner_id]
    if search:
        s = search.lower()
        filtered = [d for d in filtered if s in (d.get("title") or "").lower()]

    writers = list({d.get("owner_id") for d in all_docs if d.get("owner_id") in profiles})
    writers_list = [profiles[w] for w in writers]

    # Recent activity — best-effort; collection may not exist yet / be empty.
    doc_ids = {str(d["_id"]) for d in all_docs}
    activity_raw = list(db["content_activity"].find({}).sort("created_at", -1).limit(100))
    activity = []
    for a in activity_raw:
        if a.get("document_id") not in doc_ids:
            continue
        doc_title = next((d.get("title") for d in all_docs if str(d["_id"]) == a["document_id"]), "(deleted doc)")
        who = profiles.get(a.get("user_id"), {}).get("name", "Someone") if a.get("user_id") else "Someone"
        activity.append({
            "id": str(a["_id"]), "who": who, "type": a.get("type"), "meta": a.get("meta"),
            "doc_title": doc_title,
            "created_at": a["created_at"].isoformat() if isinstance(a.get("created_at"), datetime) else a.get("created_at"),
        })
        if len(activity) >= 50:
            break

    return {
        "stats": stats,
        "documents": [
            {**_serialize_doc(d), "owner_name": profiles.get(d.get("owner_id"), {}).get("name", "—")}
            for d in filtered
        ],
        "writers": writers_list,
        "activity": activity,
        "categories": [{"value": k, "label": v["label"]} for k, v in CATEGORY_META.items()],
        "statuses": [{"value": k, "label": v["label"]} for k, v in STATUS_META.items()],
    }


@router.get("/writing-time")
async def writing_time(range_days: int = 30, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()
    users_col = db["users"]

    domain = _domain_of(current_user.get("email", ""))
    domain_users = list(users_col.find({"email": {"$regex": f"@{domain}$", "$options": "i"}})) if domain else []
    owner_ids = [str(u["_id"]) for u in domain_users]
    profiles = {
        str(u["_id"]): {"id": str(u["_id"]), "name": u.get("full_name") or u.get("username")}
        for u in domain_users
    }

    since = (datetime.utcnow() - timedelta(days=range_days)).strftime("%Y-%m-%d")
    logs = list(db["content_time_logs"].find({"log_date": {"$gte": since}, "user_id": {"$in": owner_ids}}))
    docs = {str(d["_id"]): d for d in db["content_documents"].find({"owner_id": {"$in": owner_ids}})}

    total_sec = sum(l.get("seconds", 0) for l in logs)
    writers_touched = len({l["user_id"] for l in logs})
    docs_touched = len({l["document_id"] for l in logs})
    days_touched = len({l["log_date"] for l in logs}) or 1
    avg_per_day = round(total_sec / days_touched)

    by_user = {}
    for l in logs:
        u = by_user.setdefault(l["user_id"], {"seconds": 0, "docs": set(), "days": set()})
        u["seconds"] += l.get("seconds", 0)
        u["docs"].add(l["document_id"])
        u["days"].add(l["log_date"])
    by_user_list = sorted([
        {"user": profiles.get(uid), "seconds": v["seconds"], "docs": len(v["docs"]), "days": len(v["days"])}
        for uid, v in by_user.items() if uid in profiles
    ], key=lambda r: -r["seconds"])

    by_doc = {}
    for l in logs:
        d = by_doc.setdefault(l["document_id"], {"seconds": 0, "users": set()})
        d["seconds"] += l.get("seconds", 0)
        d["users"].add(l["user_id"])
    by_doc_list = sorted([
        {"id": did, "doc": {"title": docs[did].get("title"), "category": docs[did].get("category"), "status": docs[did].get("status")},
         "seconds": v["seconds"], "users": len(v["users"])}
        for did, v in by_doc.items() if did in docs
    ], key=lambda r: -r["seconds"])[:25]

    by_category = {}
    for l in logs:
        d = docs.get(l["document_id"])
        if not d: continue
        cat = d.get("category", "other")
        c = by_category.setdefault(cat, {"seconds": 0, "docs": set(), "users": set()})
        c["seconds"] += l.get("seconds", 0)
        c["docs"].add(l["document_id"])
        c["users"].add(l["user_id"])
    cat_total = sum(v["seconds"] for v in by_category.values()) or 1
    by_category_list = sorted([
        {"category": cat, "seconds": v["seconds"], "docs": len(v["docs"]), "users": len(v["users"]),
         "avg_per_doc": round(v["seconds"] / len(v["docs"])) if v["docs"] else 0,
         "pct": round(v["seconds"] / cat_total * 100, 1)}
        for cat, v in by_category.items()
    ], key=lambda r: -r["seconds"])

    by_status = {}
    for l in logs:
        d = docs.get(l["document_id"])
        if not d: continue
        st = d.get("status", "draft")
        by_status[st] = by_status.get(st, 0) + l.get("seconds", 0)
    st_total = sum(by_status.values()) or 1
    by_status_list = sorted([
        {"status": st, "seconds": sec, "pct": round(sec / st_total * 100, 1)}
        for st, sec in by_status.items()
    ], key=lambda r: -r["seconds"])

    by_day = {}
    for l in logs:
        by_day[l["log_date"]] = by_day.get(l["log_date"], 0) + l.get("seconds", 0)
    by_day_list = sorted(by_day.items())

    return {
        "totals": {"total_seconds": total_sec, "writers": writers_touched, "docs_touched": docs_touched, "avg_per_day": avg_per_day},
        "by_user": by_user_list,
        "by_doc": by_doc_list,
        "by_category": by_category_list,
        "by_status": by_status_list,
        "by_day": by_day_list,
    }