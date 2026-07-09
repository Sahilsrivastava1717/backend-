"""
Configuration management for EZTRACKLY Backend
Handles environment variables and application settings
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings from environment variables"""
    
    # MongoDB Configuration
    mongodb_url: str = "mongodb://localhost:27017"
    database_name: str = "eztrackly_db"
    
    # JWT Configuration
    secret_key: str = "your-secret-key-change-this-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    
    # API Configuration
    api_version: str = "v1"
    debug: bool = True
    
    # CORS Configuration
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:3001",
    ]
    
    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    groq_api_key: str = ""

    # Google OAuth (Calendar API / Meet link creation)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    frontend_url: str = "http://localhost:3000"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Create settings instance
settings = Settings()