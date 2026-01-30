# BrainDrive Core

## Overview

BrainDrive Core is a full-stack application with a FastAPI backend and React frontend. It includes a plugin architecture for both frontend and backend plugins.

## Repository Structure

```
backend/
  app/
    api/v1/endpoints/   # REST API endpoints (fs.py, auth.py, etc.)
    core/               # Config, security, auth
    models/             # SQLAlchemy models (User, Plugin, etc.)
    plugins/            # Plugin loading infrastructure
  plugins/shared/       # Shared plugins (frontend + backend)
    BrainDriveChat/     # Chat plugin (versioned: v1.0.21..v1.0.26)
    braindrive-library/ # Library backend plugin
  tests/                # pytest test suite
frontend/
  src/                  # React frontend source
```

## Key Features

- **Backend Plugin Architecture**: Plugins register endpoints via `@plugin_endpoint` decorator. Routes loaded dynamically by `PluginRouteLoader`.
- **Library Integration**: Filesystem primitives (`/api/v1/fs/*`) + Library plugin (`/api/v1/plugin-api/braindrive-library/library/*`) for structured project management.
- **Chat Plugin**: BrainDriveChat supports RAG collections, web search, personas, document upload, and Library context injection.

## Running Tests

### Backend (pytest)
```bash
cd /Users/davidwaring/BrainDrive-Core
python -m pytest backend/tests/ -v
```

### Specific test files
```bash
python -m pytest backend/tests/test_library_plugin_routes.py -v
python -m pytest backend/tests/test_library_e2e.py -v
```

### Frontend (Jest)
```bash
cd /Users/davidwaring/BrainDrive-Core/frontend
npx jest --passWithNoTests
```

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `backend/app/api/v1/endpoints/` | Core API endpoints |
| `backend/plugins/shared/braindrive-library/v1/` | Library backend plugin |
| `backend/plugins/shared/BrainDriveChat/v1.0.26/` | Latest chat plugin |
| `backend/tests/` | Backend test suite |
| `frontend/src/` | React frontend |

## Configuration

- `LIBRARY_PATH`: Path to local BrainDrive Library (default: `~/BrainDrive-Library`). Set in `.env` or backend config.

## Branch Strategy

- `main` — stable
- `feature/fs-primitives` — Core filesystem API (PR #217)
- `feature/backend-plugin-arch` — Backend plugin architecture + Library plugin (PR #219)
