# BrainDrive Backend

The API server for BrainDrive - a modular, extensible AI platform.

BrainDrive Backend is a FastAPI-based server that powers the BrainDrive application, providing APIs for managing users, plugins, conversations, AI providers, and more.

> **Note:** The backend is designed to work with the [BrainDrive Frontend](../frontend/README.md). See the [Installation Guide](https://docs.braindrive.ai/core/getting-started/install) for complete setup instructions.

## How Frontend and Backend Work Together

BrainDrive uses a client-server architecture:

| Component | Role |
|-----------|------|
| **Frontend** | React web app that users interact with - handles UI, PageBuilder, and plugin rendering |
| **Backend** | FastAPI server that handles data, authentication, AI providers, and business logic |

The frontend communicates with the backend via REST APIs. When a user performs an action (e.g., sending a chat message, installing a plugin, saving a page), the frontend makes API calls to the backend, which processes the request and returns data.

**Key integration points:**
- **Authentication**: Backend issues JWT tokens; frontend stores and sends them with requests
- **Plugins**: Backend manages plugin metadata and storage; frontend renders plugin UI
- **AI Providers**: Backend connects to AI models (Ollama, etc.); frontend displays responses
- **Pages & Routes**: Backend stores page configurations; frontend renders them via PageBuilder

## Tech Stack

- **[FastAPI](https://fastapi.tiangolo.com/)** - High-performance Python web framework
- **[SQLModel](https://sqlmodel.tiangolo.com/)** - ORM built on SQLAlchemy and Pydantic
- **[Uvicorn](https://www.uvicorn.org/)** - ASGI server
- **[Pydantic](https://docs.pydantic.dev/)** - Data validation and serialization
- **[Alembic](https://alembic.sqlalchemy.org/)** - Database migrations
- **[SQLite](https://www.sqlite.org/)** - Default database engine
- **[Structlog](https://www.structlog.org/)** - Structured logging
- **[Passlib](https://passlib.readthedocs.io/)** - Password hashing
- **[Python-Jose](https://python-jose.readthedocs.io/)** - JWT creation and verification

## Features

- JWT-based authentication with refresh tokens
- User registration, login, and profile management
- User updaters run automatically after each login
- Dynamic settings system with multi-tier support
- Modular plugin system with automatic discovery
- AI provider registry and switching support
- Dynamic navigation and component rendering
- Conversation history management
- Tag-based organization system
- CORS, environment profiles, and structured logging

## Document Processing

- Layout-aware extraction for PDF, DOCX/RTF, PPTX, spreadsheets (XLSX/XLS/ODS), CSV, JSON, Markdown/HTML/XML/text, EML, and EPUB
- Registry-driven detection (magic, MIME, extension) powers `/api/v1/documents/supported-types` and keeps upload validation in sync
- Runtime guards: 10MB upload ceiling, configurable character caps, and chunking defaults tuned for chat context (25 segments, 2k chars, 200 overlap)
- Optional query flags on `/process` and `/process-multiple`: `include_chunks`, `max_chars`, `preserve_layout` (PDF), and `strip_boilerplate` (HTML/EML)

## Installation

See the [Installation Guide](https://docs.braindrive.ai/core/getting-started/install) for complete setup instructions including both frontend and backend.

## Running the Backend

### Development Mode

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8005
```

### Production Mode

1. Set in `.env`: `APP_ENV=prod`, `DEBUG=false`, `RELOAD=false`
2. Run with a process manager (e.g., systemd, supervisor):

```bash
uvicorn main:app --host 0.0.0.0 --port 8005 --workers 4
```

#### Example systemd Unit

```ini
[Unit]
Description=BrainDrive Backend
After=network.target

[Service]
User=BrainDriveAI
WorkingDirectory=/opt/BrainDrive/backend
Environment="PATH=/opt/BrainDrive/backend/venv/bin"
ExecStart=/opt/BrainDrive/backend/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8005 --workers 4
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable braindrive
sudo systemctl start braindrive
```

## API Documentation

Once running:

- Swagger UI: [http://localhost:8005/api/v1/docs](http://localhost:8005/api/v1/docs)
- ReDoc: [http://localhost:8005/api/v1/redoc](http://localhost:8005/api/v1/redoc)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Package install fails | `pip install --upgrade pip`, retry install |
| Port in use | Change `PORT` in `.env` |
| Module not found | `pip install <module>` and update requirements |
| DB errors | Check `.env` values and DB file |
| Activation fails | Confirm conda/venv setup and shell support |

## Contributing

Interested in developing plugins or contributing to BrainDrive? See the [Plugin Developer Quickstart](https://docs.braindrive.ai/core/getting-started/plugin-developer-quickstart).

When contributing to the backend:
- Follow PEP8 and use type hints
- Document new APIs with OpenAPI annotations
- Run tests before submitting changes

## Documentation

Full documentation is available at [docs.braindrive.ai](https://docs.braindrive.ai).

## Questions?

Post at [community.braindrive.ai](https://community.braindrive.ai). We're here to help build the future of user-owned AI together.

## License

Licensed under the [MIT License](../LICENSE). Your AI. Your Rules.
