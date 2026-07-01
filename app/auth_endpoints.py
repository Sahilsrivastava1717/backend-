"""
Auth Endpoints
Register, Login, Logout, Refresh, Profile
"""
from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime
from app.auth_models import (
    UserRegister, UserLogin, UserResponse, TokenResponse,
    RefreshTokenRequest, ChangePasswordRequest, UpdateProfileRequest
)
from app.auth_utils import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    decode_token, get_user_by_email, get_user_by_username,
    get_user_by_id, get_current_user
)
from app.mongodb import get_users_collection
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _to_user_response(user: dict) -> UserResponse:
    """Build a UserResponse from a raw Mongo user document, defaulting
    any extended profile fields that aren't set yet."""
    return UserResponse(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        full_name=user.get("full_name"),
        created_at=user["created_at"],
        is_active=user.get("is_active", True),
        personal_email=user.get("personal_email"),
        phone=user.get("phone"),
        date_of_birth=user.get("date_of_birth"),
        gender=user.get("gender"),
        job_title=user.get("job_title"),
        designation=user.get("designation"),
        company=user.get("company"),
        address=user.get("address"),
        bio=user.get("bio"),
        avatar_url=user.get("avatar_url"),
        emergency_contact_name=user.get("emergency_contact_name"),
        emergency_contact_phone=user.get("emergency_contact_phone"),
        emergency_contact_relation=user.get("emergency_contact_relation"),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserRegister):
    """Register a new user"""
    collection = get_users_collection()

    # Check if email already exists
    if get_user_by_email(data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Check if username already exists
    if get_user_by_username(data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )

    # Create user document
    user_doc = {
        "username": data.username,
        "email": data.email,
        "full_name": data.full_name,
        "password_hash": hash_password(data.password),
        "is_active": True,
        "created_at": datetime.utcnow(),
        # Extended profile fields default to None until the user fills them in
        "personal_email": None,
        "phone": None,
        "date_of_birth": None,
        "gender": None,
        "job_title": None,
        "designation": None,
        "company": None,
        "address": None,
        "bio": None,
        "avatar_url": None,
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
        "emergency_contact_relation": None,
    }

    result = collection.insert_one(user_doc)
    user_id = str(result.inserted_id)

    # Generate tokens
    token_data = {"sub": user_id, "email": data.email}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    user_response = UserResponse(
        id=user_id,
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        created_at=user_doc["created_at"],
        is_active=True,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=user_response,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin):
    """Login with email and password"""
    user = get_user_by_email(data.email)

    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive"
        )

    token_data = {"sub": user["id"], "email": user["email"]}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=_to_user_response(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(data: RefreshTokenRequest):
    """Refresh access token using refresh token"""
    payload = decode_token(data.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    user_id = payload.get("sub")
    user = get_user_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    token_data = {"sub": user["id"], "email": user["email"]}
    access_token = create_access_token(token_data)
    new_refresh_token = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=_to_user_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current logged in user profile"""
    return _to_user_response(current_user)


@router.put("/me", response_model=UserResponse)
async def update_profile(
    data: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update current user profile"""
    collection = get_users_collection()
    update_data = {}

    if data.full_name is not None:
        update_data["full_name"] = data.full_name

    if data.username is not None:
        existing = get_user_by_username(data.username)
        if existing and existing["id"] != current_user["id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
        update_data["username"] = data.username

    # ── Extended profile fields ──
    # Use exclude_unset so fields the client didn't send aren't overwritten
    # with None, but fields explicitly sent (even empty string) do get saved.
    extended_fields = data.dict(
        exclude_unset=True,
        include={
            "personal_email", "phone", "date_of_birth", "gender",
            "job_title", "designation", "address", "bio",
            "emergency_contact_name", "emergency_contact_phone",
            "emergency_contact_relation",
        },
    )
    update_data.update(extended_fields)

    if update_data:
        from bson import ObjectId
        collection.update_one(
            {"_id": ObjectId(current_user["id"])},
            {"$set": update_data}
        )

    updated_user = get_user_by_id(current_user["id"])
    return _to_user_response(updated_user)


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    """Change user password"""
    if not verify_password(data.current_password, current_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    from bson import ObjectId
    collection = get_users_collection()
    collection.update_one(
        {"_id": ObjectId(current_user["id"])},
        {"$set": {"password_hash": hash_password(data.new_password)}}
    )

    return {"message": "Password changed successfully"}


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout (client should discard tokens)"""
    return {"message": "Logged out successfully"}