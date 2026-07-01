"""
Document Endpoints
Upload files or save links — shared across the team (domain-scoped)
"""
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from typing import Optional
from datetime import datetime
from bson import ObjectId
import os, shutil, uuid

from app.auth_utils import get_current_user, decode_token, get_user_by_id
from app.mongodb import get_db
from app.document_models import DocumentResponse, DocumentListResponse

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_docs_collection():
    return get_db()["documents"]


def serialize(doc: dict) -> dict:
    doc["id"] = str(doc["_id"])
    doc.pop("_id", None)
    return doc


def extract_domain(email: str) -> str:
    """Extract domain from email — e.g. 'sahil@ezsignly.com' → 'ezsignly.com'"""
    return email.split("@")[-1].lower() if "@" in email else ""


@router.get("", response_model=DocumentListResponse)
async def list_documents(current_user: dict = Depends(get_current_user)):
    """Return only documents uploaded by members of the same email domain."""
    domain = extract_domain(current_user.get("email", ""))
    if not domain:
        return {"documents": [], "total": 0}

    col = get_docs_collection()
    docs = list(col.find({"domain": domain}).sort("created_at", -1))
    docs = [serialize(d) for d in docs]
    return {"documents": docs, "total": len(docs)}


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename)[-1]
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    col = get_docs_collection()
    now = datetime.utcnow()
    doc = {
        "title": title,
        "description": description,
        "file_url": f"/api/v1/documents/file/{unique_name}",
        "file_name": file.filename,
        "file_type": ext.lstrip(".").upper() if ext else file.content_type,
        "link_url": None,
        "uploaded_by": current_user["id"],
        "uploader_name": current_user.get("full_name") or current_user.get("username"),
        # Scope this document to the uploader's email domain so only
        # same-domain members can see it.
        "domain": extract_domain(current_user.get("email", "")),
        "created_at": now,
    }
    result = col.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.post("/link", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def save_link(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    link_url: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    col = get_docs_collection()
    now = datetime.utcnow()
    doc = {
        "title": title,
        "description": description,
        "file_url": None,
        "file_name": None,
        "file_type": None,
        "link_url": link_url,
        "uploaded_by": current_user["id"],
        "uploader_name": current_user.get("full_name") or current_user.get("username"),
        # Same domain scoping as file uploads.
        "domain": extract_domain(current_user.get("email", "")),
        "created_at": now,
    }
    result = col.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


# ── File download — accepts token as query param ──────────────────────────────
@router.get("/file/{filename}")
async def get_file(filename: str, token: Optional[str] = Query(None)):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        filename=filename,
        media_type="application/octet-stream"
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: str, current_user: dict = Depends(get_current_user)):
    col = get_docs_collection()
    try:
        doc = col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Only the uploader can delete their document
    if doc["uploaded_by"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this document")

    # Verify the requesting user is from the same domain (extra safety check)
    if doc.get("domain") and doc["domain"] != extract_domain(current_user.get("email", "")):
        raise HTTPException(status_code=403, detail="Not authorized")

    if doc.get("file_url"):
        fname = doc["file_url"].split("/")[-1]
        fpath = os.path.join(UPLOAD_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)

    col.delete_one({"_id": ObjectId(doc_id)})