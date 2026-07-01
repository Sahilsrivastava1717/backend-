
"""
Chat Endpoints — Full messaging backend
Conversations (DM + Group) + Messages + Reactions + Read receipts
"""
from fastapi import APIRouter, HTTPException, Depends, status
from datetime import datetime
from bson import ObjectId
from typing import List

from app.auth_utils import get_current_user
from app.mongodb import get_db
from app.chat_models import ConversationCreate, MessageCreate, MessageUpdate, ReactionToggle

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ── Helpers ────────────────────────────────────────────────────────────────────
def oid(s):
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {s}")


def serialize_user(u):
    return {
        "id": str(u["_id"]),
        "name": u.get("full_name") or u.get("username") or u.get("email", "").split("@")[0],
        "username": u.get("username", ""),
        "email": u.get("email", ""),
        "role": u.get("role", "Member"),
        "avatar_url": None,
        "last_seen": u.get("last_seen"),
    }


def serialize_msg(m, users_by_id):
    return {
        "id": str(m["_id"]),
        "conversation_id": str(m["conversation_id"]),
        "sender_id": str(m["sender_id"]),
        "sender": users_by_id.get(str(m["sender_id"])),
        "content": m.get("content"),
        "reply_to_id": str(m["reply_to_id"]) if m.get("reply_to_id") else None,
        "attachment_url": m.get("attachment_url"),
        "attachment_name": m.get("attachment_name"),
        "attachment_type": m.get("attachment_type"),
        "is_system": m.get("is_system", False),
        "is_pinned": m.get("is_pinned", False),
        "edited_at": m.get("edited_at").isoformat() if m.get("edited_at") else None,
        "deleted_at": m.get("deleted_at").isoformat() if m.get("deleted_at") else None,
        "reactions": m.get("reactions", []),
        "reads": m.get("reads", []),
        "mentions": m.get("mentions", []),
        "created_at": m["created_at"].isoformat(),
    }


def serialize_conv(conv, current_user_id, users_by_id):
    members = [users_by_id.get(str(uid)) for uid in conv.get("member_ids", []) if str(uid) != current_user_id]
    members = [m for m in members if m]

    other_user = None
    if conv["type"] == "dm":
        other_id = next((str(uid) for uid in conv.get("member_ids", []) if str(uid) != current_user_id), None)
        other_user = users_by_id.get(other_id) if other_id else None

    last_read = next(
        (r["read_at"] for r in conv.get("last_read", []) if str(r.get("user_id")) == current_user_id),
        None
    )
    unread = conv.get("unread_counts", {}).get(current_user_id, 0)

    return {
        "id": str(conv["_id"]),
        "type": conv["type"],
        "name": conv.get("name"),
        "emoji": conv.get("emoji", "💬" if conv["type"] == "group" else None),
        "avatar_url": None,
        "last_message_at": conv.get("last_message_at").isoformat() if conv.get("last_message_at") else None,
        "last_message_preview": conv.get("last_message_preview"),
        "muted": False,
        "last_read_at": last_read.isoformat() if last_read else None,
        "unread": unread,
        "otherUser": other_user,
        "members": members,
    }


# ── GET /teammates ─────────────────────────────────────────────────────────────
@router.get("/teammates")
async def get_teammates(current_user: dict = Depends(get_current_user)):
    db = get_db()

    # Dynamically match the current user's email domain so users only see
    # teammates who registered with the same domain (e.g. gmail.com users see
    # only gmail.com users, ezsignly.com users see only ezsignly.com users).
    email = current_user.get("email", "")
    domain = email.split("@")[-1] if "@" in email else None

    if not domain:
        return []

    # Escape dots so they're treated as literal characters in the regex
    escaped_domain = domain.replace(".", "\\.")

    users = list(db["users"].find({
        "email": {"$regex": f"@{escaped_domain}$", "$options": "i"},
        "_id": {"$ne": ObjectId(current_user["id"])}
    }))
    return [serialize_user(u) for u in users]

# ── GET /conversations ─────────────────────────────────────────────────────────
@router.get("/conversations")
async def get_conversations(current_user: dict = Depends(get_current_user)):
    db = get_db()
    uid = ObjectId(current_user["id"])

    convs = list(db["conversations"].find(
        {"member_ids": uid},
        sort=[("last_message_at", -1)]
    ))

    # Collect all user ids
    all_user_ids = set()
    for c in convs:
        for mid in c.get("member_ids", []):
            all_user_ids.add(mid)

    users = list(db["users"].find({"_id": {"$in": list(all_user_ids)}}))
    users_by_id = {str(u["_id"]): serialize_user(u) for u in users}

    return [serialize_conv(c, current_user["id"], users_by_id) for c in convs]


# ── POST /conversations ────────────────────────────────────────────────────────
@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreate,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    member_ids = [uid] + [oid(m) for m in data.member_ids]

    # For DM — find existing conversation
    if data.type == "dm" and len(member_ids) == 2:
        existing = db["conversations"].find_one({
            "type": "dm",
            "member_ids": {"$all": member_ids, "$size": 2}
        })
        if existing:
            # Build response
            all_uids = existing.get("member_ids", [])
            users = list(db["users"].find({"_id": {"$in": all_uids}}))
            users_by_id = {str(u["_id"]): serialize_user(u) for u in users}
            return serialize_conv(existing, current_user["id"], users_by_id)

    now = datetime.utcnow()
    conv = {
        "type": data.type,
        "name": data.name if data.type == "group" else None,
        "emoji": "💬" if data.type == "group" else None,
        "member_ids": member_ids,
        "last_message_at": None,
        "last_message_preview": None,
        "last_read": [{"user_id": uid, "read_at": now}],
        "unread_counts": {},
        "created_at": now,
        "created_by": uid,
    }
    result = db["conversations"].insert_one(conv)
    conv["_id"] = result.inserted_id

    users = list(db["users"].find({"_id": {"$in": member_ids}}))
    users_by_id = {str(u["_id"]): serialize_user(u) for u in users}
    return serialize_conv(conv, current_user["id"], users_by_id)


# ── GET /conversations/{id}/messages ──────────────────────────────────────────
@router.get("/conversations/{conv_id}/messages")
async def get_messages(
    conv_id: str,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    conv = db["conversations"].find_one({"_id": oid(conv_id), "member_ids": uid})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = list(db["messages"].find(
        {"conversation_id": oid(conv_id)},
        sort=[("created_at", 1)]
    ))

    # Get all sender ids
    sender_ids = list({m["sender_id"] for m in msgs})
    users = list(db["users"].find({"_id": {"$in": sender_ids}}))
    users_by_id = {str(u["_id"]): serialize_user(u) for u in users}

    # Mark as read
    now = datetime.utcnow()
    db["conversations"].update_one(
        {"_id": oid(conv_id), "last_read.user_id": uid},
        {"$set": {"last_read.$.read_at": now, f"unread_counts.{current_user['id']}": 0}}
    )

    return [serialize_msg(m, users_by_id) for m in msgs]


# ── POST /conversations/{id}/messages ─────────────────────────────────────────
@router.post("/conversations/{conv_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    conv_id: str,
    data: MessageCreate,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    conv = db["conversations"].find_one({"_id": oid(conv_id), "member_ids": uid})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not data.content and not data.attachment_url:
        raise HTTPException(status_code=400, detail="Message must have content or attachment")

    now = datetime.utcnow()
    msg = {
        "conversation_id": oid(conv_id),
        "sender_id": uid,
        "content": data.content,
        "reply_to_id": oid(data.reply_to_id) if data.reply_to_id else None,
        "attachment_url": data.attachment_url,
        "attachment_name": data.attachment_name,
        "attachment_type": data.attachment_type,
        "is_system": False,
        "is_pinned": False,
        "reactions": [],
        "reads": [],
        "mentions": [],
        "edited_at": None,
        "deleted_at": None,
        "created_at": now,
    }
    result = db["messages"].insert_one(msg)
    msg["_id"] = result.inserted_id

    # Update conversation last message + unread counts for others
    preview = (data.content or "📎 Attachment")[:80]
    unread_inc = {}
    for mid in conv.get("member_ids", []):
        if mid != uid:
            key = f"unread_counts.{str(mid)}"
            unread_inc[key] = 1  # Will be incremented below

    update = {
        "$set": {
            "last_message_at": now,
            "last_message_preview": preview,
        },
        "$inc": {f"unread_counts.{str(mid)}": 1 for mid in conv.get("member_ids", []) if mid != uid}
    }
    db["conversations"].update_one({"_id": oid(conv_id)}, update)

    # Get sender info
    sender = db["users"].find_one({"_id": uid})
    users_by_id = {str(uid): serialize_user(sender)} if sender else {}

    return serialize_msg(msg, users_by_id)


# ── PUT /messages/{id} — Edit ──────────────────────────────────────────────────
@router.put("/messages/{msg_id}")
async def edit_message(
    msg_id: str,
    data: MessageUpdate,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    msg = db["messages"].find_one({"_id": oid(msg_id), "sender_id": uid})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found or not yours")

    now = datetime.utcnow()
    db["messages"].update_one(
        {"_id": oid(msg_id)},
        {"$set": {"content": data.content, "edited_at": now}}
    )
    msg["content"] = data.content
    msg["edited_at"] = now

    sender = db["users"].find_one({"_id": uid})
    users_by_id = {str(uid): serialize_user(sender)} if sender else {}
    return serialize_msg(msg, users_by_id)


# ── DELETE /messages/{id} — Soft delete ───────────────────────────────────────
@router.delete("/messages/{msg_id}")
async def delete_message(
    msg_id: str,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = ObjectId(current_user["id"])
    msg = db["messages"].find_one({"_id": oid(msg_id), "sender_id": uid})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found or not yours")

    now = datetime.utcnow()
    db["messages"].update_one(
        {"_id": oid(msg_id)},
        {"$set": {"deleted_at": now, "content": None}}
    )
    return {"ok": True}


# ── POST /messages/{id}/react ─────────────────────────────────────────────────
@router.post("/messages/{msg_id}/react")
async def toggle_reaction(
    msg_id: str,
    data: ReactionToggle,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = str(current_user["id"])
    msg = db["messages"].find_one({"_id": oid(msg_id)})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    reactions = msg.get("reactions", [])
    existing = next((r for r in reactions if r["user_id"] == uid and r["emoji"] == data.emoji), None)

    if existing:
        db["messages"].update_one(
            {"_id": oid(msg_id)},
            {"$pull": {"reactions": {"user_id": uid, "emoji": data.emoji}}}
        )
    else:
        db["messages"].update_one(
            {"_id": oid(msg_id)},
            {"$push": {"reactions": {"user_id": uid, "emoji": data.emoji, "created_at": datetime.utcnow().isoformat()}}}
        )
    return {"ok": True}


# ── POST /messages/{id}/pin ───────────────────────────────────────────────────
@router.post("/messages/{msg_id}/pin")
async def toggle_pin(
    msg_id: str,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    msg = db["messages"].find_one({"_id": oid(msg_id)})
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    db["messages"].update_one(
        {"_id": oid(msg_id)},
        {"$set": {"is_pinned": not msg.get("is_pinned", False)}}
    )
    return {"ok": True, "is_pinned": not msg.get("is_pinned", False)}


# ── POST /messages/{id}/read ──────────────────────────────────────────────────
@router.post("/messages/{msg_id}/read")
async def mark_read(
    msg_id: str,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    uid = str(current_user["id"])
    db["messages"].update_one(
        {"_id": oid(msg_id), "reads.user_id": {"$ne": uid}},
        {"$push": {"reads": {"user_id": uid, "read_at": datetime.utcnow().isoformat()}}}
    )
    return {"ok": True}
