# BrainDrive Backend

## 🚀 Overview

BrainDrive Backend is the engine behind the BrainDrive application—a modular, extensible AI platform. This FastAPI-based backend provides robust APIs for managing users, plugins, conversations, settings, and more, with a focus on flexibility, security, and developer experience.

## 🛠️ Tech Stack

* **[FastAPI](https://fastapi.tiangolo.com/)** — High-performance, Python-based web framework
* **[SQLModel](https://sqlmodel.tiangolo.com/)** — ORM built on SQLAlchemy and Pydantic
* **[Uvicorn](https://www.uvicorn.org/)** — Lightning-fast ASGI server
* **[Pydantic](https://docs.pydantic.dev/)** — Data validation and serialization
* **[Alembic](https://alembic.sqlalchemy.org/)** — Database migrations
* **[SQLite](https://www.sqlite.org/)** — Default lightweight database engine
* **[Structlog](https://www.structlog.org/)** — Structured logging
* **[Passlib](https://passlib.readthedocs.io/)** — Password hashing
* **[Python-Jose](https://python-jose.readthedocs.io/)** — JWT creation and verification

## ✨ Features

* 🔒 JWT-based authentication with refresh tokens
* 👤 User registration, login, and profile management
* 🔄 User updaters run automatically after each login
* ⚙️ Dynamic settings system with multi-tier support
* 🤖 Modular plugin system with automatic discovery
* 📚 AI provider registry and switching support
* 🧭 Dynamic navigation and component rendering
* 💬 Conversation history management
* 🏷️ Tag-based organization system
* 🌐 CORS, environment profiles, and structured logging

## 📄 Document Processing

- Layout-aware extraction for PDF, DOCX/RTF, PPTX, spreadsheets (XLSX/XLS/ODS), CSV, JSON, Markdown/HTML/XML/text, EML, and EPUB.
- Registry-driven detection (magic, MIME, extension) powers `/api/v1/documents/supported-types` and keeps upload validation in sync.
- Runtime guards: 10MB upload ceiling, configurable character caps, and chunking defaults tuned for chat context (25 segments, 2k chars, 200 overlap).
- Optional query flags on `/process` and `/process-multiple`: `include_chunks`, `max_chars`, `preserve_layout` (PDF), and `strip_boilerplate` (HTML/EML).

## 📦 Installation

- [Installation Guide](../INSTALL.md) - Complete instructions how to setup your BrainDrive

## ▶️ Running the Backend

### Development Mode

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8005
```

### Production Mode

1. Set in `.env`: `APP_ENV=prod`, `DEBUG=false`, `RELOAD=false`
2. Run with process manager (e.g., systemd, supervisor):

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

## 📖 API Docs

Once running:

* Swagger UI: [http://localhost:8005/api/v1/docs](http://localhost:8005/api/v1/docs)
* ReDoc: [http://localhost:8005/api/v1/redoc](http://localhost:8005/api/v1/redoc)

## 🧪 Development Workflow

1. Activate your environment (`conda activate BrainDriveDev` or `source venv/bin/activate`)
2. Pull latest changes
3. Install new dependencies if needed
4. Test locally
5. Add/update requirements with:

   ```bash
   pip freeze > requirements.txt
   ```

## 🛠 Troubleshooting

| Issue                 | Solution                                       |
| --------------------- | ---------------------------------------------- |
| Package install fails | `pip install --upgrade pip`, retry install     |
| Port in use           | Change `PORT` in `.env`                        |
| Module not found      | `pip install <module>` and update requirements |
| DB errors             | Check `.env` values and DB file                |
| Activation fails      | Confirm conda/venv setup and shell support     |

## 📄 License

[MIT License](../LICENSE)

## 🤝 Contributing

We welcome contributions! Please open issues or submit PRs for bugs, enhancements, or documentation improvements.

* Follow PEP8 and use type hints
* Document new APIs with OpenAPI annotations
* Run tests before submitting changes

## 🌐 Additional Resources

* [FastAPI Docs](https://fastapi.tiangolo.com/)
* [Alembic Docs](https://alembic.sqlalchemy.org/)
* [SQLModel Docs](https://sqlmodel.tiangolo.com/)
* [Structlog Docs](https://www.structlog.org/)

