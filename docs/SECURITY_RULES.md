# BrainDrive Security Development Rules

This document defines security patterns and rules that must be followed when developing BrainDrive endpoints and features.

---

## Authentication & Authorization Rules

### Rule 1: Never use `oauth2_scheme` in endpoint modules

**❌ DON'T:**
```python
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@router.get("/some-endpoint")
async def my_endpoint(token: str = Depends(oauth2_scheme)):
    ...
```

**✅ DO:**
```python
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext

@router.get("/some-endpoint")
async def my_endpoint(auth: AuthContext = Depends(require_user)):
    ...
```

**Why:** `oauth2_scheme` is an internal authentication detail. Endpoints should use the standardized auth dependencies (`require_user`, `require_admin`, `optional_user`) which provide a clean `AuthContext` object.

**Enforcement:** Automated check runs via `make check-security` and fails CI if violated.

---

## Available Auth Dependencies

Use these standard dependencies in your endpoint functions:

### `require_user` - Authenticated User Required
```python
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext

@router.get("/my-endpoint")
async def my_endpoint(auth: AuthContext = Depends(require_user)):
    user_id = auth.user_id
    username = auth.username
    ...
```

### `require_admin` - Admin User Required
```python
from app.core.auth_deps import require_admin

@router.post("/admin-action")
async def admin_action(auth: AuthContext = Depends(require_admin)):
    # Only admins can access this endpoint
    ...
```

### `optional_user` - Optional Authentication
```python
from app.core.auth_deps import optional_user

@router.get("/public-or-private")
async def flexible_endpoint(auth: Optional[AuthContext] = Depends(optional_user)):
    if auth:
        # User is authenticated
        user_id = auth.user_id
    else:
        # Anonymous access
        ...
```

---

## Quick Reference

| Need | Use This | Location |
|------|----------|----------|
| Authenticated user | `require_user` | `app.core.auth_deps` |
| Admin only | `require_admin` | `app.core.auth_deps` |
| Optional auth | `optional_user` | `app.core.auth_deps` |
| Auth context object | `AuthContext` | `app.core.auth_context` |

---


