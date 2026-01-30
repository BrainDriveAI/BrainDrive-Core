# app/core/config.py
import json
from pathlib import Path
from typing import List, Optional, Tuple, Union
from pydantic_settings import BaseSettings
from pydantic import field_validator

def _env_file_candidates() -> Tuple[Union[str, Path], ...]:
    """Build a prioritized list of .env files for cross-platform support."""
    base_dir = Path(__file__).resolve().parent.parent
    project_root = base_dir.parent
    candidates: List[Union[str, Path]] = [
        base_dir / ".env",
        base_dir / ".env.dev",
        base_dir / ".env.local",
        project_root / ".env",
        project_root / ".env.local",
        project_root / ".env.dev",
        ".env",  # fallback to previous behavior
    ]

    # Preserve order while removing duplicates
    unique_candidates: List[Union[str, Path]] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "BrainDrive"
    APP_ENV: str = "dev"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8005
    RELOAD: bool = True
    LOG_LEVEL: str = "info"
    PROXY_HEADERS: bool = True
    FORWARDED_ALLOW_IPS: str = "*"
    SSL_KEYFILE: Optional[str] = None
    SSL_CERTFILE: Optional[str] = None

    # Security
    SECRET_KEY: str = "your-secret-key-here"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"
    
    # Rate Limiting & Request Size
    MAX_REQUEST_SIZE: int = 5 * 1024 * 1024  # 5MB for JSON bodies
    
    # Service Authentication
    # Static bearer tokens for service-to-service auth
    # Generate secure tokens: python -c "import secrets; print(secrets.token_urlsafe(32))"
    PLUGIN_RUNTIME_TOKEN: str = ""  # For plugin runtime service calls
    JOB_WORKER_TOKEN: str = ""  # For background job worker callbacks
    PLUGIN_LIFECYCLE_TOKEN: str = ""  # For plugin lifecycle operations

    # BrainDrive Library
    # Path to the BrainDrive-Library folder (configurable via .env)
    # Default: ~/BrainDrive-Library
    LIBRARY_PATH: str = str(Path.home() / "BrainDrive-Library")

    # Database
    DATABASE_URL: str = "sqlite:///braindrive.db"
    DATABASE_TYPE: str = "sqlite"
    USE_JSON_STORAGE: bool = False
    JSON_DB_PATH: str = "./storage/database.json"
    SQL_LOG_LEVEL: str = "WARNING"

    # Redis
    USE_REDIS: bool = False
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # CORS Configuration - Revised for cross-platform compatibility
    # Production origins (explicit list for security)
    CORS_ORIGINS: List[str] = []  # Explicit origins for production only
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_MAX_AGE: int = 600
    CORS_EXPOSE_HEADERS: List[str] = []  # e.g., ["X-Request-Id", "X-Total-Count"]
    
    # Development CORS hosts (for regex generation)
    CORS_DEV_HOSTS: List[str] = ["localhost", "127.0.0.1", "[::1]"]  # IPv6 support + network IP

    # Allowed hosts
    ALLOWED_HOSTS: List[str] = ["localhost", "127.0.0.1"]

    ENCRYPTION_MASTER_KEY: str = ""
    ENABLE_TEST_ROUTES: bool = True

    # Library Configuration
    # Path to the BrainDrive Library folder (supports ~ for home directory)
    LIBRARY_PATH: str = "~/BrainDrive-Library"
    CORS_METHODS: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"]
    CORS_HEADERS: List[str] = ["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"]
    @field_validator("CORS_ORIGINS", "CORS_EXPOSE_HEADERS", "CORS_DEV_HOSTS", mode="before")
    @classmethod
    def parse_cors_list(cls, v):
        """Parse CORS-related list fields from string or list"""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            s = v.strip()
            if not s:  # Empty string
                return []
            if s.startswith("["):         # JSON array
                try:
                    return json.loads(s)
                except json.JSONDecodeError:
                    return []
            return [p.strip() for p in s.split(",") if p.strip()]  # comma-separated
        return v or []

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_hosts(cls, v):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                return json.loads(s)
            return [p.strip() for p in s.split(",") if p.strip()]
        return v

    # Property aliases for backwards compatibility with main.py
    @property
    def cors_origins_list(self) -> List[str]:
        """Return CORS origins as a list (includes dev hosts in dev mode)."""
        if self.APP_ENV == "dev":
            # In dev mode, allow all localhost variants
            dev_origins = []
            for host in self.CORS_DEV_HOSTS:
                dev_origins.extend([
                    f"http://{host}:5173",   # Vite dev server
                    f"http://{host}:3000",   # Alt dev port
                    f"http://{host}:3001",   # Plugin dev
                    f"http://{host}:8005",   # Backend
                ])
            return list(set(self.CORS_ORIGINS + dev_origins))
        return self.CORS_ORIGINS

    @property
    def cors_methods_list(self) -> List[str]:
        """Return CORS methods as a list."""
        return self.CORS_METHODS

    @property
    def cors_headers_list(self) -> List[str]:
        """Return CORS headers as a list."""
        return self.CORS_HEADERS

    @property
    def cors_expose_headers_list(self) -> List[str]:
        """Return CORS expose headers as a list."""
        return self.CORS_EXPOSE_HEADERS

    model_config = {
        "env_file": _env_file_candidates(),
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }

    # Properties for CORS lists (used by main.py)
    @property
    def cors_origins_list(self) -> List[str]:
        """Return CORS origins as a list."""
        return self.CORS_ORIGINS if self.CORS_ORIGINS else ["*"]

    @property
    def cors_methods_list(self) -> List[str]:
        """Return CORS methods as a list."""
        return self.CORS_METHODS

    @property
    def cors_headers_list(self) -> List[str]:
        """Return CORS headers as a list."""
        return self.CORS_HEADERS

    @property
    def cors_expose_headers_list(self) -> List[str]:
        """Return CORS expose headers as a list."""
        return self.CORS_EXPOSE_HEADERS

settings = Settings()
__all__ = ["settings"]
