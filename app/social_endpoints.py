"""
Social Media Hub Endpoints.
Trend Radar / Competitor "scraping" uses Groq only (no live web-search
configured) — trends are AI-generated plausible ideas from niche+keywords,
not scraped from the real web. Swap in a search tool later if available.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId
import os, json

from app.auth_utils import get_current_user
from app.mongodb import get_db

router = APIRouter(prefix="/api/v1/social", tags=["social"])

PLATFORMS = ["linkedin", "facebook", "instagram", "twitter"]
POST_STATUS = {"draft", "review", "scheduled", "posted", "failed"}


def _groq():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(500, detail="GROQ_API_KEY not configured")
    from groq import Groq
    return Groq(api_key=api_key)


def _to_resp(d: dict) -> dict:
    d = {**d}
    d["id"] = str(d.pop("_id"))
    for k in ("created_at", "updated_at", "scheduled_at", "posted_at", "discovered_at", "last_scraped_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _posts_col():       return get_db()["social_posts"]
def _competitors_col(): return get_db()["social_competitors"]
def _trends_col():      return get_db()["social_trends"]
def _settings_col():    return get_db()["social_settings"]


# ── Posts ─────────────────────────────────────────────────────────────────────

class PostIn(BaseModel):
    title: Optional[str] = None
    content: str
    platforms: List[str]
    hashtags: List[str] = []
    media_urls: List[str] = []
    first_comment: Optional[str] = None
    scheduled_at: Optional[str] = None
    status: str = "draft"  # draft | review | scheduled


class PostUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    platforms: Optional[List[str]] = None
    hashtags: Optional[List[str]] = None
    media_urls: Optional[List[str]] = None
    first_comment: Optional[str] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = None


@router.get("/posts")
async def list_posts(current_user: dict = Depends(get_current_user)):
    docs = list(_posts_col().find({}).sort("created_at", -1).limit(500))
    return [_to_resp(d) for d in docs]


@router.post("/posts", status_code=201)
async def create_post(data: PostIn, current_user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    doc = {
        **data.dict(),
        "posted_at": None,
        "created_by": current_user["id"],
        "created_at": now, "updated_at": now,
    }
    result = _posts_col().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.put("/posts/{post_id}")
async def update_post(post_id: str, data: PostUpdate, current_user: dict = Depends(get_current_user)):
    update = {k: v for k, v in data.dict(exclude_unset=True).items()}
    update["updated_at"] = datetime.now(timezone.utc)
    if update.get("status") == "posted":
        update["posted_at"] = datetime.now(timezone.utc)
    result = _posts_col().update_one({"_id": ObjectId(post_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Post not found")
    doc = _posts_col().find_one({"_id": ObjectId(post_id)})
    return _to_resp(doc)


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, current_user: dict = Depends(get_current_user)):
    result = _posts_col().delete_one({"_id": ObjectId(post_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Post not found")
    return {"message": "Deleted"}


# ── AI compose helpers (Hook / Rewrite / Shorten / Hashtags) ──────────────────

class ComposeAIRequest(BaseModel):
    content: str
    action: str  # hook | rewrite | shorten | hashtags
    platform: str = "linkedin"


@router.post("/ai/compose")
async def ai_compose(data: ComposeAIRequest, current_user: dict = Depends(get_current_user)):
    client = _groq()
    prompts = {
        "hook": f'Rewrite the opening line of this {data.platform} post to be a scroll-stopping hook. Return only the full rewritten post, nothing else.\n\n"{data.content}"',
        "rewrite": f'Rewrite this {data.platform} post to be clearer and more engaging, same core message. Return only the rewritten post text.\n\n"{data.content}"',
        "shorten": f'Shorten this {data.platform} post to about half its length while keeping the key message. Return only the shortened post text.\n\n"{data.content}"',
        "hashtags": f'Suggest 5 relevant hashtags for this {data.platform} post. Return ONLY a JSON array of strings like ["#tag1","#tag2"], nothing else.\n\n"{data.content}"',
    }
    prompt = prompts.get(data.action)
    if not prompt:
        raise HTTPException(400, detail="Invalid action")
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        if data.action == "hashtags":
            raw_clean = raw.replace("```json", "").replace("```", "").strip()
            try:
                tags = json.loads(raw_clean)
            except Exception:
                tags = [t.strip() for t in raw_clean.replace("[", "").replace("]", "").replace('"', "").split(",") if t.strip()]
            return {"hashtags": tags}
        return {"content": raw}
    except Exception as e:
        raise HTTPException(500, detail=f"Groq error: {e}")


# ── Competitors ────────────────────────────────────────────────────────────────

class CompetitorIn(BaseModel):
    platform: str
    handle: str
    display_name: Optional[str] = None
    url: str
    notes: Optional[str] = None


@router.get("/competitors")
async def list_competitors(current_user: dict = Depends(get_current_user)):
    docs = list(_competitors_col().find({}).sort("created_at", -1))
    return [_to_resp(d) for d in docs]


@router.post("/competitors", status_code=201)
async def create_competitor(data: CompetitorIn, current_user: dict = Depends(get_current_user)):
    doc = {**data.dict(), "last_scraped_at": None, "created_by": current_user["id"], "created_at": datetime.now(timezone.utc)}
    result = _competitors_col().insert_one(doc)
    doc["_id"] = result.inserted_id
    return _to_resp(doc)


@router.delete("/competitors/{comp_id}")
async def delete_competitor(comp_id: str, current_user: dict = Depends(get_current_user)):
    result = _competitors_col().delete_one({"_id": ObjectId(comp_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, detail="Not found")
    return {"message": "Deleted"}


# ── Trend Radar (Groq-generated, no live web search) ───────────────────────────

class TrendsRefreshRequest(BaseModel):
    niche: str
    keywords: List[str]


@router.post("/trends/refresh")
async def refresh_trends(data: TrendsRefreshRequest, current_user: dict = Depends(get_current_user)):
    client = _groq()
    system = (
        "You are a social media trend analyst. Given a niche and keywords, invent 5 plausible, "
        "currently-relevant content trends/angles a marketer could post about. You do NOT have live "
        "web access — do not claim specific real articles, stats, or sources; keep claims general and framed as ideas, not facts.\n\n"
        "Return ONLY valid JSON: an array of 5 objects, each with keys: "
        '"topic" (short headline), "summary" (1-2 sentences), "angle" (a punchy quote-style hook), '
        '"hashtags" (array of 3-5 strings starting with #), "score" (0-100 relevance guess).'
    )
    user_msg = f"Niche: {data.niche}\nKeywords: {', '.join(data.keywords)}"
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=1200,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        )
        raw = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)
    except Exception as e:
        raise HTTPException(500, detail=f"Groq error: {e}")

    now = datetime.now(timezone.utc)
    inserted = 0
    for item in items:
        doc = {
            "topic": item.get("topic", "")[:200],
            "summary": item.get("summary", ""),
            "angle": item.get("angle"),
            "hashtags": item.get("hashtags", []),
            "source_url": None, "source_title": None,
            "score": int(item.get("score", 50)),
            "status": "new",
            "niche": data.niche,
            "discovered_at": now,
        }
        _trends_col().insert_one(doc)
        inserted += 1
    return {"inserted": inserted}


@router.get("/trends")
async def list_trends(current_user: dict = Depends(get_current_user)):
    docs = list(_trends_col().find({}).sort("discovered_at", -1).limit(60))
    return [_to_resp(d) for d in docs]


@router.patch("/trends/{trend_id}")
async def update_trend(trend_id: str, status: str, current_user: dict = Depends(get_current_user)):
    result = _trends_col().update_one({"_id": ObjectId(trend_id)}, {"$set": {"status": status}})
    if result.matched_count == 0:
        raise HTTPException(404, detail="Not found")
    return {"message": "Updated"}


# ── AI Weekly Planner ────────────────────────────────────────────────────────

class WeeklyPlanRequest(BaseModel):
    goal: str
    keywords: List[str]
    tone: Optional[str] = "friendly + expert"
    cta_link: Optional[str] = None
    brand_context: Optional[str] = None
    week_start: str  # YYYY-MM-DD (Monday)
    posts_per_day: int = 1
    platforms: List[str] = ["linkedin"]


@router.post("/weekly-plan")
async def generate_weekly_plan(data: WeeklyPlanRequest, current_user: dict = Depends(get_current_user)):
    client = _groq()
    competitor_count = _competitors_col().count_documents({})
    system = (
        "You are a social media strategist. Propose 3 DISTINCT 7-day content plans for the week given "
        f"the goal, keywords, tone, and brand context. Competitor tracking context: {competitor_count} competitors tracked "
        "(you don't have their live posts, so don't reference specific competitor content).\n\n"
        "Return ONLY valid JSON: an array of 3 plan objects, each with keys "
        '"plan_name" (short label like "Educational-first"), "summary" (1 sentence), '
        '"days" (array of 7 objects, one per day Mon-Sun, each with "day" (Mon..Sun), '
        '"title", "content" (full post text ready to publish, include the CTA link if given), "hashtags" (array)).'
    )
    user_msg = (
        f"Goal: {data.goal}\nKeywords: {', '.join(data.keywords)}\nTone: {data.tone}\n"
        f"CTA link: {data.cta_link or 'none'}\nBrand context: {data.brand_context or 'none'}\n"
        f"Posts per day: {data.posts_per_day}\nPlatforms: {', '.join(data.platforms)}"
    )
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile", max_tokens=3000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        )
        raw = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        plans = json.loads(raw)
        return {"plans": plans}
    except Exception as e:
        raise HTTPException(500, detail=f"Groq error: {e}")


class ApplyPlanRequest(BaseModel):
    week_start: str
    days: List[dict]  # [{day, title, content, hashtags}]
    platforms: List[str] = ["linkedin"]


@router.post("/weekly-plan/apply")
async def apply_weekly_plan(data: ApplyPlanRequest, current_user: dict = Depends(get_current_user)):
    from datetime import timedelta
    monday = datetime.strptime(data.week_start, "%Y-%m-%d")
    day_offset = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    now = datetime.now(timezone.utc)
    created = 0
    for d in data.days:
        offset = day_offset.get(d.get("day", "Mon"), 0)
        scheduled = (monday + timedelta(days=offset)).replace(hour=10, minute=0, second=0, microsecond=0)
        doc = {
            "title": d.get("title"), "content": d.get("content", ""),
            "platforms": data.platforms, "hashtags": d.get("hashtags", []),
            "media_urls": [], "first_comment": None,
            "scheduled_at": scheduled.isoformat(), "status": "scheduled",
            "posted_at": None, "created_by": current_user["id"],
            "created_at": now, "updated_at": now,
        }
        _posts_col().insert_one(doc)
        created += 1
    return {"created": created}


# ── Settings (Ayrshare key) ───────────────────────────────────────────────────

class SettingsIn(BaseModel):
    ayrshare_key: str


@router.get("/settings")
async def get_settings(current_user: dict = Depends(get_current_user)):
    doc = _settings_col().find_one({"user_id": current_user["id"]})
    return {"ayrshare_key_set": bool(doc and doc.get("ayrshare_key"))}


@router.post("/settings")
async def save_settings(data: SettingsIn, current_user: dict = Depends(get_current_user)):
    _settings_col().update_one(
        {"user_id": current_user["id"]},
        {"$set": {"user_id": current_user["id"], "ayrshare_key": data.ayrshare_key, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"message": "Saved"}