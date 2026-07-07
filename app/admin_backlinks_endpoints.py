"""
Admin Backlinks Tracker — team/user filters, health score, daily report.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from typing import Optional, List

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/admin/backlinks", tags=["admin-backlinks"])

TEAM_ROLES = {"seo", "sales", "content_writer", "developer", "manager"}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admins only")


def _domain_of(email: str) -> str:
    return email.split("@")[-1].lower() if "@" in email else ""


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at", "last_checked_at", "fixed_at", "redo_requested_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


@router.get("/users")
async def list_users(current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()
    domain = _domain_of(current_user.get("email", ""))
    users = list(db["users"].find({"email": {"$regex": f"@{domain}$", "$options": "i"}}))
    return [
        {"id": str(u["_id"]), "name": u.get("full_name") or u.get("username"),
         "email": u.get("email"), "role": u.get("role")}
        for u in users
    ]


@router.get("")
async def list_all(
    date_filter: str = "today",  # today | 7d | 30d | all
    team: str = "all",
    user_id: str = "all",
    status: str = "all",
    category_id: str = "all",
    duplicate_filter: str = "all",  # all | only | hide
    search: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    _require_admin(current_user)
    db = get_db()
    col = db["backlinks"]
    domain = _domain_of(current_user.get("email", ""))
    users = list(db["users"].find({"email": {"$regex": f"@{domain}$", "$options": "i"}}))
    user_map = {str(u["_id"]): u for u in users}

    q: dict = {}
    if date_filter != "all":
        now = datetime.now(timezone.utc)
        if date_filter == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            since = now - timedelta(days=7 if date_filter == "7d" else 30)
        q["created_at"] = {"$gte": since}
    if team != "all":
        team_user_ids = [uid for uid, u in user_map.items() if u.get("role") == team]
        q["user_id"] = {"$in": team_user_ids}
    if user_id != "all":
        q["user_id"] = user_id
    if status != "all":
        q["status"] = status
    if category_id != "all":
        q["category_id"] = None if category_id == "__none__" else category_id
    if search:
        q["$or"] = [
            {"website_url": {"$regex": search, "$options": "i"}},
            {"live_post_url": {"$regex": search, "$options": "i"}},
            {"anchor_text": {"$regex": search, "$options": "i"}},
        ]

    docs = list(col.find(q).sort("created_at", -1))

    # Duplicate detection by live_post_url within this result set
    seen = {}
    for d in docs:
        url = (d.get("live_post_url") or "").strip().lower()
        if not url:
            continue
        seen.setdefault(url, []).append(d)
    dup_ids = set()
    dup_index = {}
    for url, group in seen.items():
        if len(group) > 1:
            for i, d in enumerate(sorted(group, key=lambda x: x.get("created_at", datetime.min)), start=1):
                dup_ids.add(d["_id"])
                dup_index[d["_id"]] = i

    if duplicate_filter == "only":
        docs = [d for d in docs if d["_id"] in dup_ids]
    elif duplicate_filter == "hide":
        docs = [d for d in docs if d["_id"] not in dup_ids]

    total = len(docs)
    live = sum(1 for d in docs if d.get("status") in ("live", "indexed"))
    broken = sum(1 for d in docs if d.get("status") == "broken")
    pending = sum(1 for d in docs if d.get("status") == "pending")
    duplicate = sum(1 for d in docs if d["_id"] in dup_ids)
    health_score = round((live / total) * 100) if total else 0

    items = []
    for d in docs:
        resp = _to_resp(d)
        resp["is_duplicate"] = d["_id"] in dup_ids
        resp["duplicate_index"] = dup_index.get(d["_id"])
        u = user_map.get(d.get("user_id"))
        resp["user_name"] = u.get("full_name") or u.get("username") if u else "Unknown"
        resp["user_email"] = u.get("email") if u else ""
        items.append(resp)

    return {
        "items": items,
        "stats": {"total": total, "live": live, "broken": broken, "pending": pending,
                   "duplicate": duplicate, "health_score": health_score},
        "top_contributors": sorted(
            [{"name": (user_map.get(uid, {}).get("full_name") or user_map.get(uid, {}).get("username") or "Unknown"),
              "count": sum(1 for d in docs if d.get("user_id") == uid)}
             for uid in {d.get("user_id") for d in docs}],
            key=lambda x: -x["count"],
        )[:8],
    }


@router.get("/daily-report")
async def daily_report(days: int = 7, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    db = get_db()
    domain = _domain_of(current_user.get("email", ""))
    users = {str(u["_id"]): u for u in db["users"].find({"email": {"$regex": f"@{domain}$", "$options": "i"}})}
    cats = {str(c["_id"]): c["name"] for c in db["backlink_categories"].find({})}

    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    docs = list(db["backlinks"].find({"created_at": {"$gte": since}}))

    by_day: dict = {}
    for d in docs:
        created = d.get("created_at")
        if not created:
            continue
        day_iso = created.strftime("%Y-%m-%d")
        cat_name = cats.get(d.get("category_id"), "Uncategorized")
        u = users.get(d.get("user_id"))
        user_name = (u.get("full_name") or u.get("username")) if u else "Unknown"
        by_day.setdefault(day_iso, {}).setdefault(cat_name, {"total": 0, "by_user": {}})
        by_day[day_iso][cat_name]["total"] += 1
        by_day[day_iso][cat_name]["by_user"][user_name] = by_day[day_iso][cat_name]["by_user"].get(user_name, 0) + 1

    return {"by_day": by_day, "total": len(docs)}


@router.delete("/bulk")
async def bulk_delete(ids: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    id_list = [ObjectId(i) for i in ids.split(",") if i]
    result = get_db()["backlinks"].delete_many({"_id": {"$in": id_list}})
    return {"deleted": result.deleted_count}


@router.post("/bulk-check")
async def bulk_check(ids: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    import httpx
    db = get_db()
    id_list = [ObjectId(i) for i in ids.split(",") if i]
    docs = list(db["backlinks"].find({"_id": {"$in": id_list}}))
    ok_count = broken_count = 0
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
            db["backlinks"].update_one({"_id": d["_id"]}, {"$set": {
                "status": new_status, "http_status": http_status, "last_checked_at": now,
            }})
            db["backlink_status_checks"].insert_one({
                "backlink_id": str(d["_id"]), "http_status": http_status,
                "status": new_status, "source": "admin", "checked_at": now,
            })
            if ok: ok_count += 1
            else: broken_count += 1
    return {"checked": len(docs), "live": ok_count, "broken": broken_count}