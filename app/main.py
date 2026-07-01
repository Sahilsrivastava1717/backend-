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



@app.get("/")
async def root():
    return {"message": "Welcome to EZTRACKLY API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}