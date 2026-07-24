"""
Meetings Endpoints — creates REAL Google Meet links via the Google
Calendar API (conferenceData), not fake strings.

Setup required (one-time, per environment):
1. Go to console.cloud.google.com -> create/select a project.
2. Enable the "Google Calendar API".
3. Create an OAuth 2.0 Client ID (type: Web application).
   Authorized redirect URI: {API_BASE}/api/v1/meetings/google/callback
4. Set env vars:
     GOOGLE_CLIENT_ID=...
     GOOGLE_CLIENT_SECRET=...
     GOOGLE_REDIRECT_URI=https://your-api-domain/api/v1/meetings/google/callback
     FRONTEND_URL=https://your-frontend-domain   (used to redirect back after connect)
5. pip install google-api-python-client google-auth google-auth-oauthlib

Flow:
- Admin clicks "Connect Google" in the frontend -> browser navigates to
  GET /api/v1/meetings/google/connect?token=<jwt> (token passed as a query
  param since this is a plain browser redirect, not a fetch() call, so it
  can't carry an Authorization header).
- User is redirected to Google's consent screen, then back to
  /api/v1/meetings/google/callback -> we store the refresh token on that
  admin's user doc (google_oauth field) and redirect to
  {FRONTEND_URL}/meetings?google=connected.
- POST /api/v1/meetings now uses that admin's stored credentials to insert a
  real Calendar event with conferenceData, and saves the REAL hangoutLink
  Google returns as meet_link. This is what makes the "Join" button open an
  actual "Ready to join?" screen instead of a "Check your meeting code" error.

If no admin has connected Google yet, meeting creation still works but
meet_link is null and meet_link_is_real is false, so the frontend shows a
"Connect Google" banner and disables the Join button instead of linking to
a broken/fake meeting URL.

meet_link_error captures WHY a real link couldn't be created (no admin
connected, refresh token invalid/revoked, Calendar API insert failed, etc.)
so the frontend can show something more useful than a generic message when
the banner says "connected" but creation still fails.

Mount in main.py:
    from app.routers import meetings
    app.include_router(meetings.router)
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import os, uuid

# google-auth-oauthlib refuses to run over plain http by default, and also
# errors out if Google returns a slightly different scope string back than
# what we requested (very common — e.g. it appends "openid"). Both of these
# are expected/harmless for local dev over http://localhost, so relax them
# BEFORE importing/using Flow, or token exchange silently fails and every
# connect attempt bounces back as ?google=error with no useful detail.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from app.auth_utils import get_current_user, get_users_collection, decode_token, get_user_by_id
from app.mongodb import get_db
from app.config import settings
from bson import ObjectId

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])

IST = timezone(timedelta(hours=5, minutes=30))

GOOGLE_CLIENT_ID = settings.google_client_id
GOOGLE_CLIENT_SECRET = settings.google_client_secret
GOOGLE_REDIRECT_URI = settings.google_redirect_uri
FRONTEND_URL = settings.frontend_url or "http://localhost:3000"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# google-auth-oauthlib auto-generates a PKCE code_verifier per Flow instance.
# /connect and /callback are separate requests (separate Flow objects), so
# we have to persist the verifier ourselves, keyed by `state`, or token
# exchange fails with "invalid_grant: Missing code verifier". In-memory dict
# is fine here since this is a single dev process; for multi-worker/prod
# deployments swap this for a short-lived Mongo/Redis entry instead.
_PENDING_PKCE_VERIFIERS: dict[str, str] = {}


def _require_admin(current_user: dict):
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


def _meetings_col():
    return get_db()["meetings"]


def _user_from_query_token(token: str) -> dict:
    """Auth for browser-redirect endpoints that can't send an Authorization header."""
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _to_resp(m: dict) -> dict:
    return {
        "id": str(m["_id"]),
        "title": m.get("title", ""),
        "description": m.get("description", ""),
        "date": m.get("date"),
        "time": m.get("time"),
        "duration_minutes": m.get("duration_minutes", 30),
        "meet_link": m.get("meet_link"),
        "meet_link_is_real": m.get("meet_link_is_real", False),
        "meet_link_error": m.get("meet_link_error"),
        "attendees": m.get("attendees", []),
        "external_emails": m.get("external_emails", []),
        "agenda": m.get("agenda"),
        "notes": m.get("notes", ""),
        "created_by": m.get("created_by"),
        "created_by_name": m.get("created_by_name"),
        "created_at": m.get("created_at").isoformat() if m.get("created_at") else None,
        "starts_at": m.get("starts_at").isoformat() if m.get("starts_at") else None,
        "ends_at": m.get("ends_at").isoformat() if m.get("ends_at") else None,
    }


# ── Google OAuth: connect / callback ─────────────────────────────────────────

@router.get("/google/connect")
async def google_connect(token: str = Query(...)):
    """
    Admin's browser is redirected here (not a fetch call) so auth comes from
    a `token` query param instead of an Authorization header.
    """
    current_user = _user_from_query_token(token)
    _require_admin(current_user)
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
        raise HTTPException(500, detail="Google OAuth is not configured on the server (missing env vars)")

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=GOOGLE_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI

    state = f"{current_user['id']}:{uuid.uuid4().hex}"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    # Persist the PKCE verifier the Flow generated so /callback can use it.
    if getattr(flow, "code_verifier", None):
        _PENDING_PKCE_VERIFIERS[state] = flow.code_verifier
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_callback(code: str, state: str):
    """Google redirects here after consent. Store refresh token on the admin's user doc."""
    from google_auth_oauthlib.flow import Flow

    admin_id = state.split(":")[0]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes=GOOGLE_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI

    verifier = _PENDING_PKCE_VERIFIERS.pop(state, None)
    if verifier:
        flow.code_verifier = verifier

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        import traceback
        print(f"Google token exchange failed: {e}")
        traceback.print_exc()
        return RedirectResponse(f"{FRONTEND_URL}/meetings?google=error")

    creds = flow.credentials
    if not creds.refresh_token:
        # Google only returns a refresh_token on first consent (prompt=consent
        # should force this, but guard anyway) — without it we can't create
        # events later, so surface that clearly instead of silently "connecting".
        return RedirectResponse(f"{FRONTEND_URL}/meetings?google=error&reason=no_refresh_token")

    users_col = get_users_collection()
    try:
        oid = ObjectId(admin_id)
    except Exception:
        return RedirectResponse(f"{FRONTEND_URL}/meetings?google=error")

    users_col.update_one(
        {"_id": oid},
        {"$set": {
            "google_oauth": {
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
                "connected_at": datetime.utcnow(),
            }
        }},
    )
    return RedirectResponse(f"{FRONTEND_URL}/meetings?google=connected")


@router.get("/google/status")
async def google_status(current_user: dict = Depends(get_current_user)):
    """
    Frontend checks this to show 'Connect Google' vs 'Connected'. Actually
    verifies the stored refresh token still works (Google can invalidate it
    server-side — e.g. testing-mode consent expiring after 7 days, or the
    user revoking access) rather than just checking a field exists, so the
    banner doesn't lie about being connected when creation would fail.
    """
    users_col = get_users_collection()
    u = users_col.find_one({"_id": ObjectId(current_user["id"])})
    g = (u or {}).get("google_oauth")
    if not g or not g.get("refresh_token"):
        return {"connected": False}

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds = Credentials(
        None,
        refresh_token=g["refresh_token"],
        token_uri=g["token_uri"],
        client_id=g["client_id"],
        client_secret=g["client_secret"],
        scopes=g["scopes"],
    )
    try:
        creds.refresh(GoogleAuthRequest())
        return {"connected": True}
    except Exception as e:
        # Token is stored but Google has invalidated it — clear it so the
        # banner correctly flips back to "Connect Google" instead of lying.
        print(f"Stored Google token for {u.get('email')} is invalid: {e}")
        users_col.update_one({"_id": u["_id"]}, {"$unset": {"google_oauth": ""}})
        return {"connected": False, "reason": "token_expired_or_revoked"}


@router.post("/google/disconnect")
async def google_disconnect(current_user: dict = Depends(get_current_user)):
    """Manually clear this admin's stored Google credentials."""
    _require_admin(current_user)
    users_col = get_users_collection()
    users_col.update_one({"_id": ObjectId(current_user["id"])}, {"$unset": {"google_oauth": ""}})
    return {"message": "Disconnected"}


def _get_calendar_service_for_admin():
    """
    Find any admin with a connected Google account and build a Calendar API
    client. Returns (service, error) — error is None on success, or a short
    string describing why no working service could be built.
    """
    users_col = get_users_collection()
    admin_with_google = users_col.find_one({"is_admin": True, "google_oauth.refresh_token": {"$exists": True}})
    if not admin_with_google:
        return None, "no_admin_connected"

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from googleapiclient.discovery import build

    g = admin_with_google["google_oauth"]
    creds = Credentials(
        None,
        refresh_token=g["refresh_token"],
        token_uri=g["token_uri"],
        client_id=g["client_id"],
        client_secret=g["client_secret"],
        scopes=g["scopes"],
    )
    try:
        # Force a refresh now (rather than lazily on first API call) so a
        # revoked/expired refresh token surfaces as a clear error here,
        # instead of failing deep inside events().insert() with a vaguer trace.
        creds.refresh(GoogleAuthRequest())
    except Exception as e:
        print(f"Google credential refresh failed for admin {admin_with_google.get('email')}: {e}")
        return None, f"refresh_failed: {e}"

    try:
        return build("calendar", "v3", credentials=creds), None
    except Exception as e:
        print(f"Failed to build Calendar service: {e}")
        return None, f"build_failed: {e}"


# ── Models ────────────────────────────────────────────────────────────────────

class CreateMeetingRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""
    date: str          # "YYYY-MM-DD"
    time: str          # "HH:MM" (24h)
    duration_minutes: int = 30
    attendee_ids: List[str] = []
    external_emails: List[str] = []


class FindTimeRequest(BaseModel):
    attendee_ids: List[str] = []
    duration_minutes: int = 30
    days_ahead: int = 5


class AgendaRequest(BaseModel):
    title: str
    description: Optional[str] = ""
    attendee_names: List[str] = []


class NotesUpdate(BaseModel):
    notes: str


# ── Attendee directory ───────────────────────────────────────────────────────

@router.get("/team/attendees")
async def list_attendees(current_user: dict = Depends(get_current_user)):
    col = get_users_collection()
    admin_email = current_user.get("email", "")
    domain = admin_email.split("@")[-1] if "@" in admin_email else None
    query = {}
    if domain:
        query["email"] = {"$regex": f"@{domain}$", "$options": "i"}
    out = []
    for u in col.find(query).sort("full_name", 1):
        out.append({
            "id": str(u["_id"]),
            "name": u.get("full_name") or u.get("username") or u.get("email"),
            "email": u.get("email", ""),
        })
    return out


# ── Find a time (stub) ───────────────────────────────────────────────────────

@router.post("/find-time")
async def find_time(data: FindTimeRequest, current_user: dict = Depends(get_current_user)):
    now = datetime.now(IST)
    slots = []
    day = now
    checked = 0
    while len(slots) < 5 and checked < data.days_ahead + 3:
        day = day + timedelta(days=1)
        checked += 1
        if day.weekday() >= 5:
            continue
        for hour in (10, 14, 16):
            slot_start = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            slots.append({
                "date": slot_start.strftime("%Y-%m-%d"),
                "time": slot_start.strftime("%H:%M"),
                "label": slot_start.strftime("%a, %b %d · %I:%M %p"),
            })
            if len(slots) >= 5:
                break
    return {"slots": slots}


# ── AI Agenda generation (Groq) ──────────────────────────────────────────────

@router.post("/generate-agenda")
async def generate_agenda(data: AgendaRequest, current_user: dict = Depends(get_current_user)):
    if not data.title.strip():
        raise HTTPException(400, detail="Title is required to generate an agenda")

    api_key = os.getenv("GROQ_API_KEY")
    fallback = (
        f"# {data.title}\n\n1. Quick status round\n2. Blockers & risks\n"
        f"3. Priorities for next period\n4. Open discussion\n"
    )
    if not api_key:
        return {"agenda": fallback, "ai_used": False}

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        attendees_str = ", ".join(data.attendee_names) if data.attendee_names else "the team"
        prompt = (
            f"Write a short, practical meeting agenda in markdown (numbered list, no preamble) "
            f"for a meeting titled \"{data.title}\" with attendees: {attendees_str}.\n"
            f"Context/description: {data.description or 'none provided'}\n"
            f"Keep it to 4-6 concise agenda items."
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"agenda": resp.choices[0].message.content.strip(), "ai_used": True}
    except Exception as e:
        print(f"Agenda AI error: {e}")
        return {"agenda": fallback, "ai_used": False}


# ── Create meeting ───────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_meeting(data: CreateMeetingRequest, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)

    users_col = get_users_collection()
    attendees = []
    attendee_emails = []
    for aid in data.attendee_ids:
        try:
            u = users_col.find_one({"_id": ObjectId(aid)})
        except Exception:
            u = None
        if u:
            attendees.append({
                "id": str(u["_id"]),
                "name": u.get("full_name") or u.get("username") or u.get("email"),
                "email": u.get("email", ""),
            })
            if u.get("email"):
                attendee_emails.append(u["email"])

    try:
        starts_at = datetime.strptime(f"{data.date} {data.time}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    except ValueError:
        raise HTTPException(400, detail="Invalid date/time format")
    ends_at = starts_at + timedelta(minutes=data.duration_minutes)

    meet_link = None
    meet_link_is_real = False
    google_event_id = None
    meet_link_error = None

    service, service_error = _get_calendar_service_for_admin()
    if service_error:
        meet_link_error = service_error
    if service:
        try:
            event_body = {
                "summary": data.title.strip(),
                "description": (data.description or "").strip(),
                "start": {"dateTime": starts_at.isoformat(), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": ends_at.isoformat(), "timeZone": "Asia/Kolkata"},
                "attendees": [{"email": e} for e in (attendee_emails + data.external_emails)],
                "conferenceData": {
                    "createRequest": {
                        "requestId": uuid.uuid4().hex,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            }
            created = service.events().insert(
                calendarId="primary",
                body=event_body,
                conferenceDataVersion=1,
                sendUpdates="all",
            ).execute()
            meet_link = created.get("hangoutLink")
            google_event_id = created.get("id")
            meet_link_is_real = bool(meet_link)
            if not meet_link:
                meet_link_error = "event_created_but_no_hangout_link"
        except Exception as e:
            # Surfacing this in logs is critical: a silent failure here is exactly
            # what produces the "Check your meeting code" experience — the meeting
            # gets saved but with no usable link, and nobody knows why.
            print(f"Google Calendar event creation failed: {e}")
            meet_link_error = f"event_insert_failed: {e}"

    doc = {
        "title": data.title.strip(),
        "description": (data.description or "").strip(),
        "date": data.date,
        "time": data.time,
        "duration_minutes": data.duration_minutes,
        "meet_link": meet_link,
        "meet_link_is_real": meet_link_is_real,
        "meet_link_error": meet_link_error,
        "google_event_id": google_event_id,
        "attendees": attendees,
        "external_emails": [e.strip() for e in data.external_emails if e.strip()],
        "agenda": None,
        "notes": "",
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name") or current_user.get("username"),
        "created_at": datetime.utcnow(),
        "starts_at": starts_at.astimezone(timezone.utc).replace(tzinfo=None),
        "ends_at": ends_at.astimezone(timezone.utc).replace(tzinfo=None),
    }
    result = _meetings_col().insert_one(doc)
    doc["_id"] = result.inserted_id

    return {"meeting": _to_resp(doc), "message": "Meeting created"}


# ── List meetings ─────────────────────────────────────────────────────────────

@router.get("")
async def list_meetings(kind: str = "upcoming", current_user: dict = Depends(get_current_user)):
    if kind not in ("upcoming", "history"):
        raise HTTPException(400, detail="kind must be 'upcoming' or 'history'")

    col = _meetings_col()
    now_utc = datetime.utcnow()

    if current_user.get("is_admin", False):
        base_query = {}
    else:
        base_query = {"attendees.id": current_user["id"]}

    if kind == "upcoming":
        query = {**base_query, "starts_at": {"$gte": now_utc}}
        cursor = col.find(query).sort("starts_at", 1)
    else:
        query = {**base_query, "starts_at": {"$lt": now_utc}}
        cursor = col.find(query).sort("starts_at", -1)

    return {"meetings": [_to_resp(m) for m in cursor]}


# ── Notes / delete ───────────────────────────────────────────────────────────

@router.patch("/{meeting_id}/notes")
async def update_notes(meeting_id: str, data: NotesUpdate, current_user: dict = Depends(get_current_user)):
    try:
        oid = ObjectId(meeting_id)
    except Exception:
        raise HTTPException(400, detail="Invalid meeting id")
    result = _meetings_col().update_one({"_id": oid}, {"$set": {"notes": data.notes}})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Meeting not found")
    return {"message": "Notes updated"}


@router.delete("/{meeting_id}")
async def delete_meeting(meeting_id: str, current_user: dict = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        oid = ObjectId(meeting_id)
    except Exception:
        raise HTTPException(400, detail="Invalid meeting id")

    meeting = _meetings_col().find_one({"_id": oid})
    if not meeting:
        raise HTTPException(404, detail="Meeting not found")

    if meeting.get("google_event_id"):
        service, _ = _get_calendar_service_for_admin()
        if service:
            try:
                service.events().delete(calendarId="primary", eventId=meeting["google_event_id"], sendUpdates="all").execute()
            except Exception as e:
                print(f"Failed to delete Google Calendar event: {e}")

    _meetings_col().delete_one({"_id": oid})
    return {"message": "Meeting deleted"}