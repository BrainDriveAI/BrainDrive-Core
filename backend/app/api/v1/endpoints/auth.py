from datetime import datetime, timedelta as datetime_timedelta, timezone
from typing import Optional
from uuid import uuid4, UUID
from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.config import settings
from app.core.database import get_db, db_factory
from app.core.user_updater import run_user_updaters
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
)
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.core.rate_limit_deps import rate_limit_ip, rate_limit_user
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse
import logging
from jose import jwt, JWTError

router = APIRouter(prefix="/auth")
logger = logging.getLogger(__name__)


def _log_auth_event_background(request: Request, event_type: str, success: bool, user_id: str = None, reason: str = None):
    """Schedule audit log write in background."""
    import asyncio
    async def _write():
        try:
            from app.core.audit import audit_logger, AuditEventType
            if success:
                await audit_logger.log_auth_success(
                    request=request,
                    user_id=user_id,
                    event_type=AuditEventType(event_type),
                )
            else:
                await audit_logger.log_auth_failure(
                    request=request,
                    reason=reason or "Unknown error",
                    event_type=AuditEventType(event_type),
                    user_id=user_id,
                )
        except Exception as e:
            logger.warning(f"Failed to write auth audit log: {e}")
    
    asyncio.create_task(_write())



def get_cookie_options(path: str = "/") -> dict:
    """Get cookie options based on environment."""
    # Determine if we're in development or production
    is_dev = settings.APP_ENV.lower() == "dev"

    return {
        "key": "refresh_token",
        "httponly": True,  # Prevent JavaScript access
        "secure": not is_dev,  # Only set secure=True in production (HTTPS)
        "samesite": "lax",  # Use 'lax' in all environments
        "max_age": settings.REFRESH_TOKEN_EXPIRE_DAYS
        * 24
        * 60
        * 60,  # Convert days to seconds
        "path": path,
    }




@router.post("/register", response_model=UserResponse)
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_ip(limit=3, window_seconds=3600))
):
    """Register a new user and initialize their data."""
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is disabled")

    try:
        # Check if user already exists
        existing_user = await User.get_by_email(db, user_data.email)
        if existing_user:
            _log_auth_event_background(request, "auth.register_failed", success=False, reason="Email already registered")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

        # Create new user
        hashed_password = hash_password(user_data.password)
        # Generate UUID without dashes
        user_id = str(uuid4()).replace("-", "")

        # Note: Admin roles will be implemented in the future
        user = User(
            id=user_id,  # UUID without dashes for compatibility
            email=user_data.email,
            password=hashed_password,
            username=user_data.username,
            # New users should start on the latest schema version
            version="0.6.0",
        )
        await user.save(db)

        # Initialize user data
        try:
            from app.core.user_initializer import initialize_user_data, get_initializers

            # Check if initializers are registered
            initializers = get_initializers()
            logger.info(
                f"Found {len(initializers)} registered initializers: {list(initializers.keys())}"
            )

            logger.info(f"Initializing data for new user: {user.id}")
            initialization_success = await initialize_user_data(str(user.id), db)

            if not initialization_success:
                logger.error(f"Failed to initialize data for user {user.id}")
                # We don't want to fail registration if initialization fails
                # The user can still log in, but might not have all data set up
            else:
                logger.info(f"Successfully initialized data for user {user.id}")
        except Exception as init_error:
            logger.error(f"Error during user initialization: {init_error}")
            # Continue with registration even if initialization fails

        # Log successful registration
        _log_auth_event_background(request, "auth.register_success", success=True, user_id=str(user.id))
        
        return UserResponse(
            id=str(user.id),
            email=user.email,
            username=user.username,
            version=user.version,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        _log_auth_event_background(request, "auth.register_failed", success=False, reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not register user",
        )


@router.post("/login")
async def login(
    request: Request,
    user_data: UserLogin,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_ip(limit=5, window_seconds=300))
):
    """Login user and return access token."""
    try:
        # Authenticate user
        user = await User.get_by_email(db, user_data.email)
        if not user or not verify_password(user_data.password, user.password):
            _log_auth_event_background(request, "auth.login_failed", success=False, reason="Invalid email or password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )


        # Run pending user updates based on version
        try:
            from app.core.user_updater import update_user_data

            updated = await update_user_data(user, db)
            if not updated:
                raise Exception("User update failed")
        except Exception as e:
            logger.error(f"Error updating user {user.id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User update failed",
            )


        # Create access token
        access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=datetime_timedelta(
                minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
            ),
        )
        logger.info(f"Created access token for user {user.email}")

        # Create refresh token
        refresh_token = create_access_token(
            data={
                "sub": str(user.id),
                "refresh": True,
            },
            expires_delta=datetime_timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )

        # Store refresh token in database
        user.refresh_token = refresh_token
        expiry_time = datetime.now(timezone.utc) + datetime_timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
        user.refresh_token_expires = expiry_time.isoformat()
        await user.save(db)

        # Set refresh token cookie
        cookie_options = get_cookie_options()
        response.set_cookie(
            cookie_options["key"],
            refresh_token,
            max_age=cookie_options["max_age"],
            path=cookie_options["path"],
            domain=None,
            secure=cookie_options["secure"],
            httponly=cookie_options["httponly"],
            samesite=cookie_options["samesite"],
        )

        # Get the current time for token issuance timestamp
        current_time = datetime.now(timezone.utc)

        response_data = {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_expires_in": settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            "issued_at": int(current_time.timestamp()),
            "user_id": str(user.id),
            "user": UserResponse(
                id=str(user.id),
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                profile_picture=user.profile_picture,
                is_active=user.is_active,
                is_verified=user.is_verified,
                version=user.version,
            ),
        }

        _log_auth_event_background(request, "auth.login_success", success=True, user_id=str(user.id))

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during login: {e}")
        _log_auth_event_background(request, "auth.login_failed", success=False, reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Login failed"
        )


@router.post("/refresh")
async def refresh_token(
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_ip(limit=10, window_seconds=300))
):
    """Refresh access token using a valid refresh token from HTTP-only cookie."""
    try:
        # Get refresh token from cookie only (no request body fallback)
        refresh_token = request.cookies.get("refresh_token")

        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No refresh token cookie",
            )

        # Verify signature and decode refresh token properly
        try:
            payload = jwt.decode(
                refresh_token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        if not payload.get("refresh", False):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type: not a refresh token",
            )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
            )

        # Normalize user_id format (remove dashes)
        user_id_str = user_id.replace("-", "")

        # Look up user
        user = await User.get_by_id(db, user_id_str)
        if not user:
            logger.warning("Refresh token for non-existent user", user_id=user_id_str)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token: user not found",
            )

        # Validate refresh token matches DB (secure rotation)
        if not user.refresh_token:
            logger.warning("No active session for user", user_id=user_id_str)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No active session",
            )
        elif user.refresh_token != refresh_token:
            # Potential token theft -- invalidate session and reject
            logger.warning("Refresh token mismatch, invalidating session", user_id=user_id_str)
            user.refresh_token = None
            user.refresh_token_expires = None
            await user.save(db)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Check DB-level expiration
        if user.refresh_token_expires:
            try:
                token_expires = datetime.fromisoformat(user.refresh_token_expires)
                if datetime.now(timezone.utc) > token_expires:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Refresh token has expired",
                    )
            except ValueError:
                logger.error("Unparseable refresh_token_expires", user_id=user_id_str)

        # Generate new tokens
        current_time = datetime.now(timezone.utc)

        new_access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=datetime_timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        )

        refresh_token_expires = datetime_timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        new_refresh_token = create_access_token(
            data={"sub": str(user.id), "refresh": True},
            expires_delta=refresh_token_expires,
        )

        # Store new refresh token in DB
        user.refresh_token = new_refresh_token
        expiry_time = current_time + refresh_token_expires
        user.refresh_token_expires = expiry_time.isoformat()
        await user.save(db)

        # Set new refresh token cookie
        cookie_options = get_cookie_options("/")
        response.set_cookie(
            cookie_options["key"],
            new_refresh_token,
            max_age=cookie_options["max_age"],
            path=cookie_options["path"],
            domain=None,
            secure=cookie_options["secure"],
            httponly=cookie_options["httponly"],
            samesite=cookie_options["samesite"],
        )

        return {
            "access_token": new_access_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_expires_in": settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            "issued_at": int(current_time.timestamp()),
            "user_id": str(user.id),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error refreshing token",
        )


@router.post("/logout")
async def logout(
    response: Response,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Logout user and clear refresh token."""
    try:
        # Fetch user from database
        stmt = select(User).where(User.id == auth.user_id)
        result = await db.execute(stmt)
        current_user_data = result.scalar_one_or_none()
        if not current_user_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
        # Clear refresh token in database
        current_user_data.refresh_token = None
        current_user_data.refresh_token_expires = None
        await current_user_data.save(db)

        # Clear refresh token cookie
        cookie_options = get_cookie_options()
        response.delete_cookie(
            cookie_options["key"],
            path=cookie_options["path"],
            domain=None,
            secure=cookie_options["secure"],
            httponly=cookie_options["httponly"],
            samesite=cookie_options["samesite"],
        )

        return {"message": "Successfully logged out"}

    except Exception as e:
        logger.error(f"Logout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not logout user",
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user information."""
    try:
        logger.info("Getting current user info")

        # Fetch user from database using auth context
        stmt = select(User).where(User.id == auth.user_id)
        result = await db.execute(stmt)
        current_user_data = result.scalar_one_or_none()
        
        if not current_user_data:
            logger.error("No user found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        # Convert user to dict and convert UUID to string for the response
        user_dict = {
            "id": str(current_user_data.id),
            "username": current_user_data.username,
            "email": current_user_data.email,
            "full_name": current_user_data.full_name,
            "profile_picture": current_user_data.profile_picture,
            "is_active": current_user_data.is_active,
            "is_verified": current_user_data.is_verified,
            "version": current_user_data.version,
        }

        logger.info(f"Returning user info for: {current_user_data.email}")
        return UserResponse(**user_dict)
    except Exception as e:
        logger.error(f"Error getting user info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.put("/profile/username", response_model=UserResponse)
async def update_username(
    request: Request,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Parse request body to get username
    try:
        body = await request.json()
        username = body.get("username")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required"
            )
    except Exception as e:
        logger.error(f"Error parsing request body: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request body"
        )
    """Update the current user's username."""
    try:
        # Fetch user from database using auth context
        stmt = select(User).where(User.id == auth.user_id)
        result = await db.execute(stmt)
        current_user_data = result.scalar_one_or_none()
        if not current_user_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
        logger.info(f"Updating username for user: {current_user_data.id}")

        # Check if username is already taken
        query = select(User).where(User.username == username)
        result = await db.execute(query)
        existing_user = result.scalar_one_or_none()

        if existing_user and existing_user.id != current_user_data.id:
            logger.error(f"Username already taken: {username}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken"
            )

        # Update username
        current_user_data.username = username
        await current_user_data.save(db)

        # Convert user to dict and convert UUID to string for the response
        user_dict = {
            "id": str(current_user_data.id),
            "username": current_user_data.username,
            "email": current_user_data.email,
            "full_name": current_user_data.full_name,
            "profile_picture": current_user_data.profile_picture,
            "is_active": current_user_data.is_active,
            "is_verified": current_user_data.is_verified,
            "version": current_user_data.version,
        }

        logger.info(f"Username updated successfully for user: {current_user_data.id}")
        return UserResponse(**user_dict)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating username: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


@router.put("/profile/password")
async def update_password(
    request: Request,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Parse request body to get password data
    try:
        body = await request.json()
        current_password = body.get("current_password")
        new_password = body.get("new_password")
        confirm_password = body.get("confirm_password")

        # Validate required fields
        if not current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required",
            )
        if not new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password is required",
            )
        if not confirm_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password confirmation is required",
            )
    except Exception as e:
        logger.error(f"Error parsing request body: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request body"
        )
    """Update the current user's password."""
    try:
        # Fetch user from database using auth context
        stmt = select(User).where(User.id == auth.user_id)
        result = await db.execute(stmt)
        current_user_data = result.scalar_one_or_none()
        if not current_user_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
        logger.info(f"Updating password for user: {current_user_data.id}")

        # Verify current password
        if not verify_password(current_password, current_user_data.password):
            logger.error(
                f"Current password verification failed for user: {current_user_data.id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

        # Verify new password matches confirmation
        if new_password != confirm_password:
            logger.error(
                f"Password confirmation mismatch for user: {current_user_data.id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password and confirmation do not match",
            )

        # Validate new password length
        if len(new_password) < 8:
            logger.error(f"New password too short for user: {current_user_data.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must be at least 8 characters long",
            )

        # Update password
        current_user_data.password = hash_password(new_password)
        await current_user_data.save(db)

        logger.info(f"Password updated successfully for user: {current_user_data.id}")
        return {"message": "Password updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )


