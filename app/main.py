from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from app.config import settings
from app.mongodb import connect_to_mongo, close_mongo_connection
from app.auth_endpoints import router as auth_router
from app.task_endpoints import router as task_router
from app.dashboard_endpoints import router as dashboard_router
from app.note_endpoints import router as note_router
from app.document_endpoints import router as document_router
from app.xp_endpoints import router as xp_router
from app.content_endpoints import router as content_router
from app.chat_endpoints import router as chat_router
from app.attendance_endpoints import router as attendance_router
from app.ai_endpoints import router as ai_router
from app.lead_endpoints import router as lead_router
from app.admin_attendance_endpoints import router as admin_attendance_router
from app.users_endpoints import router as users_router
from app.admin_profile import router as admin_profile_router
from app.backlink_endpoints import router as backlink_router
from app.admin_content_endpoints import router as admin_content_router
from app.admin_backlinks_endpoints import router as admin_backlinks_router
from app.social_endpoints import router as social_router
from app.admin_overview_endpoints import router as admin_overview_router
from app.leaves_endpoints import router as leaves_router
from app.team_settings_endpoints import router as team_settings_router
from app.employee_endpoints import router as employee_router
from app.meetings_endpoints import router as meetings_router
from app.activities_endpoints import router as activities_router 
from app.reports_endpoints import router as reports_router 
from app.company_report_endpoints import router as company_report_router
from app.Presence_endpoints import router as presence_router
from app.standup_feed_endpoints import router as standup_feed_router


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting EZTRACKLY Backend...")
    connect_to_mongo()
    yield
    close_mongo_connection()


app = FastAPI(
    title="EZTRACKLY API",
    description="Task Management and Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(task_router)
app.include_router(dashboard_router)
app.include_router(note_router)
app.include_router(document_router)
app.include_router(xp_router)
app.include_router(content_router)
app.include_router(chat_router)
app.include_router(attendance_router)
app.include_router(ai_router)
app.include_router(lead_router)
app.include_router(admin_attendance_router)
app.include_router(users_router)
app.include_router(admin_profile_router)
app.include_router(backlink_router)
app.include_router(admin_content_router)
app.include_router(admin_backlinks_router)
app.include_router(social_router)
app.include_router(admin_overview_router)
app.include_router(leaves_router)
app.include_router(team_settings_router)
app.include_router(employee_router)
app.include_router(meetings_router)
app.include_router(activities_router)
app.include_router(reports_router)
app.include_router(company_report_router)
app.include_router(presence_router)
app.include_router(standup_feed_router)

@app.get("/")
async def root():
    return {"message": "Welcome to EZTRACKLY API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}