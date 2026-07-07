"""
Content Studio Endpoints
Full CRUD for content documents
"""
from fastapi import APIRouter, HTTPException, status, Depends, Query
from datetime import datetime
from typing import Optional
from bson import ObjectId

from app.auth_utils import get_current_user
from app.mongodb import get_db
from app.content_models import (
    ContentCreate, ContentUpdate, ContentResponse,
    ContentListResponse, ContentStatus
)

router = APIRouter(prefix="/api/v1/content", tags=["content"])

def _log_activity(doc_id: str, user_id: str, type_: str, meta: dict = None):
    get_db()["content_activity"].insert_one({
        "document_id": doc_id, "user_id": user_id, "type": type_,
        "meta": meta or {}, "created_at": datetime.utcnow(),
    })

def get_content_collection():
    return get_db()["content_documents"]


def serialize(doc: dict) -> dict:
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    return doc


def build_starter_html(title: str, category: str, brief: str = None, platform: str = None) -> tuple:
    """Build starter HTML content and return (html, word_count, char_count)"""
    from datetime import datetime
    created = datetime.utcnow().strftime("%b %d, %Y")

    intro_map = {
        "blog_post":    f"Created on {created}",
        "social_post":  f"Platform: {platform or 'Social'}",
        "website_copy": "Website copy draft",
        "other":        "Custom content draft",
    }
    intro = intro_map.get(category, created)

    parts = [f"<h1>{title}</h1>", f"<p>{intro}</p>"]
    if brief:
        parts.append(f"<blockquote><p>{brief}</p></blockquote>")
    parts.append("<p><br></p>")

    html = "".join(parts)
    text = f"{title} {intro} {brief or ''}".strip()
    words = len(text.split())
    chars = len(text)
    return html, words, chars


# ── List documents ─────────────────────────────────────────────────────────────
@router.get("", response_model=ContentListResponse)
async def list_documents(
    scope: str = Query("mine"),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("updated"),
    current_user: dict = Depends(get_current_user),
):
    col = get_content_collection()
    query = {}

    if scope == "mine":
        query["owner_id"] = current_user["id"]
    elif scope == "review":
        query["assigned_reviewer_id"] = current_user["id"]
    elif scope == "shared":
        query["share_enabled"] = True
        query["owner_id"] = {"$ne": current_user["id"]}

    if category and category != "all":
        query["category"] = category
    if status and status != "all":
        query["status"] = status
    if search:
        query["title"] = {"$regex": search, "$options": "i"}

    sort_map = {
        "updated": ("updated_at", -1),
        "created": ("created_at", -1),
        "title":   ("title", 1),
        "words":   ("word_count", -1),
    }
    sort_field, sort_dir = sort_map.get(sort_by, ("updated_at", -1))

    docs = list(col.find(query).sort(sort_field, sort_dir))
    docs = [serialize(d) for d in docs]
    return {"documents": docs, "total": len(docs)}


# ── Create document ────────────────────────────────────────────────────────────
@router.post("", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    data: ContentCreate,
    current_user: dict = Depends(get_current_user),
):
    col = get_content_collection()
    now = datetime.utcnow()

    html, words, chars = build_starter_html(
        data.title, data.category,
        data.brief, data.platform
    )

    doc = {
        "title": data.title.strip(),
        "category": data.category,
        "status": "draft",
        "platform": data.platform if data.category == "social_post" else None,
        "custom_category": data.custom_category.strip() if data.category == "other" and data.custom_category else None,
        "brief": data.brief,
        "content_html": data.content_html or html,
        "content_json": data.content_json,
        "word_count": words,
        "char_count": chars,
        "share_enabled": False,
        "owner_id": current_user["id"],
        "owner_name": current_user.get("full_name") or current_user.get("username"),
        "assigned_reviewer_id": None,
        "created_at": now,
        "updated_at": now,
    }

    result = col.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    _log_activity(doc["id"], current_user["id"], "created")
    return doc


# ── Get single document ────────────────────────────────────────────────────────
@router.get("/{doc_id}", response_model=ContentResponse)
async def get_document(doc_id: str, current_user: dict = Depends(get_current_user)):
    col = get_content_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return serialize(doc)


# ── Update document ────────────────────────────────────────────────────────────
@router.put("/{doc_id}", response_model=ContentResponse)
async def update_document(
    doc_id: str,
    data: ContentUpdate,
    current_user: dict = Depends(get_current_user),
):
    col = get_content_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.utcnow()

    col.update_one({"_id": ObjectId(doc_id)}, {"$set": update})
    updated = col.find_one({"_id": ObjectId(doc_id)})

    if "status" in update:
        _log_activity(doc_id, current_user["id"], "status_changed", {"status": update["status"]})
    else:
        _log_activity(doc_id, current_user["id"], "edited")

    return serialize(updated)


# ── Delete document ────────────────────────────────────────────────────────────
@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: str, current_user: dict = Depends(get_current_user)):
    col = get_content_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    col.delete_one({"_id": ObjectId(doc_id)})


# ── Toggle share ───────────────────────────────────────────────────────────────
@router.patch("/{doc_id}/share", response_model=ContentResponse)
async def toggle_share(doc_id: str, current_user: dict = Depends(get_current_user)):
    col = get_content_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    new_val = not doc.get("share_enabled", False)
    col.update_one({"_id": ObjectId(doc_id)}, {"$set": {"share_enabled": new_val, "updated_at": datetime.utcnow()}})
    updated = col.find_one({"_id": ObjectId(doc_id)})
    _log_activity(doc_id, current_user["id"], "shared" if new_val else "unshared")
    return serialize(updated)


# ── Update status only ─────────────────────────────────────────────────────────
@router.patch("/{doc_id}/status", response_model=ContentResponse)
async def update_status(
    doc_id: str,
    new_status: ContentStatus,
    current_user: dict = Depends(get_current_user),
):
    col = get_content_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    col.update_one({"_id": ObjectId(doc_id)}, {"$set": {"status": new_status, "updated_at": datetime.utcnow()}})
    updated = col.find_one({"_id": ObjectId(doc_id)})
    _log_activity(doc_id, current_user["id"], "status_changed", {"status": new_status})
    return serialize(updated)