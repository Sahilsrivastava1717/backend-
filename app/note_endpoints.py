# app/notes.py
from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from app.note_models import NoteCreate, NoteUpdate, NoteResponse, NoteListResponse
from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def get_notes_collection():
    return get_db()["notes"]


def serialize(note: dict) -> dict:
    note["id"] = str(note["_id"])
    note.pop("_id", None)
    return note


def _oid(note_id: str) -> ObjectId:
    try:
        return ObjectId(note_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Note not found")


@router.get("", response_model=NoteListResponse)
async def list_notes(current_user: dict = Depends(get_current_user)):
    col = get_notes_collection()
    notes = list(
        col.find({"user_id": current_user["id"]})
        .sort([("pinned", -1), ("updated_at", -1)])
    )
    notes = [serialize(n) for n in notes]
    return {"notes": notes, "total": len(notes)}


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(data: NoteCreate, current_user: dict = Depends(get_current_user)):
    col = get_notes_collection()
    now = datetime.utcnow()
    doc = {
        "user_id": current_user["id"],
        "title": data.title,
        "content": data.content,
        "color": data.color,
        "pinned": data.pinned,
        "created_at": now,
        "updated_at": now,
    }
    result = col.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note(note_id: str, data: NoteUpdate, current_user: dict = Depends(get_current_user)):
    col = get_notes_collection()
    oid = _oid(note_id)

    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.utcnow()

    result = col.find_one_and_update(
        {"_id": oid, "user_id": current_user["id"]},
        {"$set": update},
    )
    if not result:
        raise HTTPException(status_code=404, detail="Note not found")

    updated = col.find_one({"_id": oid, "user_id": current_user["id"]})
    return serialize(updated)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, current_user: dict = Depends(get_current_user)):
    col = get_notes_collection()
    oid = _oid(note_id)
    result = col.delete_one({"_id": oid, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Note not found")