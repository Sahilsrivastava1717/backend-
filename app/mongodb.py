"""
MongoDB Connection and Database Initialization
Handles connection pooling and collection setup
"""
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from app.config import settings
import logging

logger = logging.getLogger(__name__)

db = None
client = None


def connect_to_mongo():
    global client, db
    try:
        client = MongoClient(
            settings.mongodb_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
        client.admin.command('ping')
        db = client[settings.database_name]
        logger.info(f"Connected to MongoDB: {settings.database_name}")
        init_collections()
        return db
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise


def close_mongo_connection():
    global client
    if client:
        client.close()
        logger.info("Disconnected from MongoDB")


def init_collections():
    if db is None:
        return
    if 'tasks' not in db.list_collection_names():
        db.create_collection('tasks')
    tasks_collection = db['tasks']
    tasks_collection.create_index('assigned_to')
    tasks_collection.create_index('status')
    tasks_collection.create_index('priority')
    tasks_collection.create_index('due_date')
    tasks_collection.create_index('created_at')
    tasks_collection.create_index('completed_at')
    tasks_collection.create_index([('assigned_to', 1), ('status', 1)])
    logger.info("Tasks collection initialized with indexes")
    if 'users' not in db.list_collection_names():
        db.create_collection('users')
    users_collection = db['users']
    users_collection.create_index('email', unique=True)
    users_collection.create_index('username', unique=True)
    logger.info("Users collection initialized with indexes")


def get_db():
    if db is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongo() first.")
    return db


def get_tasks_collection():
    return get_db()['tasks']


def get_users_collection():
    return get_db()['users']