from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta as datetime_timedelta, UTC
from typing import Optional
from jose import jwt, JWTError
import logging
from passlib.context import CryptContext
from app.core.config import settings
from app.models.user import User
from app.core.database import get_db
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
from app.core.json_storage import JSONStorage
from sqlalchemy import select

logger = logging.getLogger(__name__)

security = HTTPBearer()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

# Configure Passlib with bcrypt for password hashing
# Using bcrypt with a work factor of 12 (2^12 iterations)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash using Passlib."""
    try:
        if not plain_password or not hashed_password:
            return False
        
        # Use Passlib's verify function to check the password
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        return False

def hash_password(password: str) -> str:
    """Hash a password for storing using Passlib with bcrypt."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[datetime_timedelta] = None) -> str:
    """Create a new access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + datetime_timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Convert datetime to Unix timestamp for JWT
    to_encode.update({"exp": expire.timestamp()})
    
    # Let JWT library handle iat automatically if not provided
    if "iat" not in to_encode:
        to_encode.update({"iat": datetime.now(UTC).timestamp()})
    
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def authenticate_user(db: AsyncSession, user_data: dict) -> Optional[User]:
    """Authenticate a user and return user information."""
    try:
        # Get user by email
        user = await User.get_by_email(db, user_data["email"])
        if not user:
            logger.warning(f"User not found: {user_data['email']}")
            return None

        # Verify password
        if not verify_password(user_data["password"], user.password):
            logger.warning(f"Invalid password for user: {user_data['email']}")
            return None

        return user
    except Exception as e:
        logger.error(f"Error authenticating user: {e}")
        return None

def decode_access_token(token: str) -> dict:
    """Decode and validate an access token."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise JWTError("Invalid token format")

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        logger.error("Error decoding token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    """Get the current user from the JWT token."""

    try:
        # Log the token being used (first 10 chars only for security)
        token_preview = token[:10] + "..." if token else "None"
        logger.info(f"Authenticating with token: {token_preview}")
        
        # Decode the token
        payload = decode_access_token(token)
        
        # Log the payload (excluding sensitive data)
        user_id = payload.get("sub")
        logger.info(f"Token payload contains user_id: {user_id}")
        
        if user_id is None:
            logger.error("Token payload does not contain 'sub' field")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Format user_id for database lookup
        try:
            # Remove any dashes that might be in the user_id
            user_id_str = user_id.replace('-', '')
            logger.info(f"Formatted user_id for database lookup: {user_id_str}")
        except Exception as e:
            logger.error(f"Failed to format user_id: {e}")
            raise HTTPException(status_code=401, detail="Invalid user ID format")
            
        # Get the user from the database
        try:
            # Use the formatted string for the query
            stmt = select(User).where(User.id == user_id_str)
            result = await db.execute(stmt)
            user = result.scalar_one_or_none()
            
            if user:
                logger.info(f"Found user with ID: {user_id}")
            else:
                logger.error(f"No user found with ID: {user_id}")
                raise HTTPException(status_code=401, detail="User not found")
                
            return user
        except Exception as e:
            logger.error(f"Error getting user by ID: {e}")
            raise HTTPException(status_code=401, detail="User not found")
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error getting current user: {e}")
        raise HTTPException(status_code=401, detail="Could not validate credentials")

async def get_current_active_superuser(current_user: User = Depends(get_current_user)) -> User:
    """Check if the current user is an admin/superuser."""
    if not hasattr(current_user, 'is_admin') or not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user
