# Backend REST API Reference

The BrainDrive backend is a FastAPI application running on port 8005. All endpoints are prefixed with `/api/v1/`. All endpoints are rate-limited.

## Table of Contents

- [Authentication](#authentication)
- [Settings](#settings)
- [Plugins](#plugins)
- [Pages](#pages)
- [Plugin State](#plugin-state)
- [Conversations & Messages](#conversations--messages)
- [Personas](#personas)
- [Tags](#tags)
- [AI Providers](#ai-providers)
- [Jobs](#jobs)
- [Navigation Routes](#navigation-routes)
- [Components](#components)
- [Documents](#documents)
- [Search (SearXNG)](#search-searxng)

---

## Authentication

Authenticated endpoints accept JWT access tokens via the `Authorization` header. Browser clients typically use HTTP-only cookies set by the auth endpoints.

```
Authorization: Bearer <your_jwt_token>
```

### Register User

```http
POST /api/v1/auth/register
```

Creates a new user account.

**Request Body:**
```json
{
  "username": "string",
  "email": "string",
  "password": "string",
  "full_name": "string (optional)",
  "profile_picture": "string (optional)"
}
```

**Response:** `UserResponse`
```json
{
  "id": "uuid",
  "username": "string",
  "email": "string",
  "full_name": "string | null",
  "profile_picture": "string | null",
  "is_active": true,
  "is_verified": false
}
```

### Login

```http
POST /api/v1/auth/login
```

Authenticates a user and sets access/refresh JWTs as HTTP-only cookies.

**Request Body:**
```json
{
  "email": "string",
  "password": "string"
}
```

**Response:** Response body is empty; access and refresh tokens are set as HTTP-only cookies.

### Refresh Token

```http
POST /api/v1/auth/refresh
```

Refreshes the access token using the refresh token cookie. Response body is empty.

### Logout

```http
POST /api/v1/auth/logout
```

Invalidates the current session and clears auth cookies.

### Get Current User

```http
GET /api/v1/auth/me
```

Returns the currently authenticated user's information.

**Response:** `UserResponse`

### Update Username

```http
PUT /api/v1/auth/profile/username
```

**Request Body:**
```json
{
  "username": "new_username"
}
```

### Update Password

```http
PUT /api/v1/auth/profile/password
```

**Request Body:**
```json
{
  "current_password": "string",
  "new_password": "string"
}
```

---

## Settings

BrainDrive uses a two-tier settings system. First, a **Definition** is created to establish the schema for a setting (e.g., its name, type, default value). This is typically done by a plugin's `lifecycle_manager.py`. Then, an **Instance** is created when a user saves a value for that setting.

-   **Setting Definitions API**: Use this to manage the available settings in the system.
-   **Setting Instances API**: Use this to get or set the actual values for a user.

Scopes (`system`, `user`, `page`) are accepted but currently treated as user scope. Planned behavior: page scope overrides user scope, with user values as fallback.

### Setting Definitions

Definitions describe what settings exist and their structure.

#### List Setting Definitions

```http
GET /api/v1/settings/definitions
```

**Query Parameters:**
- `category` (optional): Filter by category
- `scope` (optional): Filter by scope (`system`, `user`, `page`)

**Response:** Array of `SettingDefinitionResponse`

#### Create Setting Definition

```http
POST /api/v1/settings/definitions
```

**Request Body:**
```json
{
  "id": "unique_setting_id",
  "name": "Display Name",
  "description": "What this setting does",
  "category": "general",
  "type": "string | number | boolean | object | array",
  "default_value": "any",
  "allowed_scopes": ["system", "user", "page"],
  "is_multiple": false,
  "validation": { "min": 0, "max": 100 },
  "tags": ["optional", "tags"]
}
```

#### Update Setting Definition

```http
PATCH /api/v1/settings/definitions/{definition_id}
PUT /api/v1/settings/definitions/{definition_id}
```

#### Delete Setting Definition

```http
DELETE /api/v1/settings/definitions/{definition_id}
```

### Setting Instances

Instances are actual setting values for users/pages.

#### List Setting Instances

```http
GET /api/v1/settings/instances
```

**Query Parameters:**
- `definition_id` (optional): Filter by definition
- `scope` (optional): Filter by scope (system, user, page)
- `user_id` (optional): Filter by user
- `page_id` (optional): Filter by page

#### Get Setting Instance

```http
GET /api/v1/settings/instances/{instance_id}
```

#### Create Setting Instance

```http
POST /api/v1/settings/instances
```

**Request Body:**
```json
{
  "definition_id": "setting_definition_id",
  "name": "instance_name",
  "value": "any_value",
  "scope": "system | user | page",
  "user_id": "uuid (optional)",
  "page_id": "uuid (optional, required if scope is page)"
}
```

#### Update Setting Instance

```http
PATCH /api/v1/settings/instances/{instance_id}
PUT /api/v1/settings/instances/{instance_id}
```

**Request Body:**
```json
{
  "name": "string (optional)",
  "value": "any"
}
```

#### Delete Setting Instance

```http
DELETE /api/v1/settings/instances/{instance_id}
```

---

## Plugins

These endpoints are used to manage the plugin lifecycle. They are the backend counterpart to the actions a user takes in the Plugin Manager UI.

-   **Manifest Endpoints** (`/manifest`, `/manager`): Provide lists of plugins for different UI contexts.
-   **Lifecycle Endpoints** (`/register`, `/install`, `/uninstall`): Manage the installation and removal of plugins.
    -   Use the global `POST /api/v1/plugins/install` when you need to support multiple installation methods (upload, URL, marketplace).
    -   Use `POST /api/v1/plugins/install-from-url` for a direct installation from a Git repository.
-   **Information Endpoints** (`/info`, `/status`): Get details about specific plugins.

### Get Plugin Manifest

```http
GET /api/v1/plugins/manifest
```

Returns all registered plugins and their modules.

### Get Plugin Manifest for Designer

```http
GET /api/v1/plugins/manifest/designer
```

Returns plugin manifest optimized for the page designer UI.

### Get Plugins for Manager

```http
GET /api/v1/plugins/manager
```

Returns plugins with management metadata (install status, updates available).

### Get Plugin by ID

```http
GET /api/v1/plugins/{plugin_id}/info
```

### Get Plugin by Slug

```http
GET /api/v1/plugins/by-slug/{plugin_slug}
```

### Register Plugin

```http
POST /api/v1/plugins/{plugin_slug}/register
```

Registers a plugin with the system. Called during plugin initialization.

### Unregister Plugin

```http
DELETE /api/v1/plugins/{plugin_id}
```

### Update Plugin Status

```http
PATCH /api/v1/plugins/{plugin_id}
```

Enable/disable a plugin.

### Get Plugin Modules

```http
GET /api/v1/plugins/{plugin_id}/modules
```

Returns all modules exposed by a plugin.

### Get Module Detail

```http
GET /api/v1/plugins/{plugin_id}/modules/{module_id}
```

### Update Module Status

```http
PATCH /api/v1/plugins/{plugin_id}/modules/{module_id}
```

Enable/disable a specific module.

### Install Plugin

```http
POST /api/v1/plugins/install
```

**Request Body (multipart/form-data):**
- `method`: "upload" | "url" | "marketplace"
- `repo_url`: Git repository URL (if method is "url")
- `file`: Plugin zip file (if method is "upload")
- `version`: Specific version to install

Or install from URL directly (equivalent to `method: "url"`):

```http
POST /api/v1/plugins/install-from-url
```

**Request Body:**
```json
{
  "repo_url": "https://github.com/org/plugin-repo",
  "version": "v1.0.0 (optional)"
}
```

### Install Plugin by Slug

```http
POST /api/v1/plugins/{plugin_slug}/install
```

Direct install for a specific plugin. Use the global `/install` endpoints when you need upload/URL/marketplace selection.

### Update Plugin

```http
POST /api/v1/plugins/{plugin_slug}/update
```

### Uninstall Plugin

```http
DELETE /api/v1/plugins/{plugin_slug}/uninstall
```

### Get Plugin Status

```http
GET /api/v1/plugins/{plugin_slug}/status
```

### Get Available Plugins

```http
GET /api/v1/plugins/available
```

Returns plugins available for installation from the marketplace.

### Check for Updates

```http
GET /api/v1/plugins/updates/available
GET /api/v1/plugins/{plugin_slug}/update/available
```

### Get Plugin Categories

```http
GET /api/v1/plugins/categories
```

### Get Plugin Tags

```http
GET /api/v1/plugins/tags
```

### Restart Plugin Service

```http
POST /api/v1/plugins/{plugin_slug}/services/restart
```

### Refresh Plugin Cache

```http
POST /api/v1/plugins/refresh-cache
```

---

## Pages

Pages are user-created views that contain plugin components.

### List Pages

```http
GET /api/v1/pages
```

**Response:**
```json
{
  "pages": [...],
  "total": 42
}
```

### Create Page

```http
POST /api/v1/pages
```

<details>
<summary>Request Body</summary>

```json
{
  "name": "My Page",
  "route": "/my-page",
  "route_segment": "my-page",
  "parent_route": "/parent (optional)",
  "parent_type": "page | navigation",
  "is_parent_page": false,
  "description": "Page description",
  "icon": "icon-name",
  "content": {
    "layout": {},
    "components": []
  }
}
```
</details>

<details>
<summary>Response: <code>PageResponse</code></summary>

```json
{
  "id": "uuid",
  "name": "My Page",
  "route": "/my-page",
  "content": {...},
  "creator_id": "uuid",
  "is_published": false,
  "created_at": "2025-01-05T...",
  "updated_at": "2025-01-05T..."
}
```
</details>

### Get Page

```http
GET /api/v1/pages/{page_id}
```

### Get Page by Route

```http
GET /api/v1/pages/route/{route}
```

**Example:** `GET /api/v1/pages/route/my-page`

### Update Page

```http
PUT /api/v1/pages/{page_id}
```

### Update Page Hierarchy

```http
PUT /api/v1/pages/{page_id}/hierarchy
```

Move a page to a different parent or change its position.

<details>
<summary>Request Body</summary>

```json
{
  "parent_route": "/new-parent",
  "parent_type": "page",
  "route_segment": "new-segment",
  "is_parent_page": false
}
```
</details>

### Delete Page

```http
DELETE /api/v1/pages/{page_id}
```

### Publish Page

```http
POST /api/v1/pages/{page_id}/publish
```

**Request Body:**
```json
{
  "publish": true
}
```

### Create Page Backup

```http
POST /api/v1/pages/{page_id}/backup
```

**Request Body:**
```json
{
  "create_backup": true
}
```

---

## Plugin State

Plugins can persist state data using this API. States can be scoped to a plugin, page, or user.

### Create Plugin State

```http
POST /api/v1/plugin-state/
```

<details>
<summary>Request Body</summary>

```json
{
  "plugin_id": "my-plugin",
  "page_id": "uuid (optional)",
  "state_key": "my_state_key (optional)",
  "state_strategy": "persistent | session | none | custom",
  "state_data": { "any": "json data" },
  "ttl_expires_at": "2025-12-31T... (optional)",
  "device_id": "string (optional)",
  "state_schema_version": "1.0 (optional)"
}
```
</details>

**Response:** `PluginStateResponse`

### List Plugin States

```http
GET /api/v1/plugin-state/
```

**Query Parameters:**
- `plugin_id` (optional)
- `page_id` (optional)
- `state_key` (optional)
- `state_strategy` (optional)
- `sync_status` (optional)
- `is_active` (optional)
- `device_id` (optional)
- `limit` (optional)
- `offset` (optional)

### Get Plugin State

```http
GET /api/v1/plugin-state/{state_id}
```

### Update Plugin State

```http
PUT /api/v1/plugin-state/{state_id}
```

<details>
<summary>Request Body</summary>

```json
{
  "state_data": { "updated": "data" },
  "state_strategy": "persistent | session | none | custom (optional)",
  "ttl_expires_at": "datetime (optional)"
}
```
</details>

### Delete Plugin State

```http
DELETE /api/v1/plugin-state/{state_id}
```

### Bulk Create Plugin States

```http
POST /api/v1/plugin-state/bulk
```

<details>
<summary>Request Body</summary>

```json
{
  "states": [
    { "plugin_id": "...", "state_data": {...} },
    { "plugin_id": "...", "state_data": {...} }
  ]
}
```
</details>

### Get Plugin State Stats

```http
GET /api/v1/plugin-state/stats
```

Returns storage statistics for plugin states.

### Cleanup Expired States

```http
DELETE /api/v1/plugin-state/cleanup
```

Removes expired non-persistent states.

---

## Conversations & Messages

### List User Conversations

```http
GET /api/v1/users/{user_id}/conversations
GET /api/v1/conversations/by-persona/{persona_id}
```

### Create Conversation

```http
POST /api/v1/conversations
```

**Request Body:**
```json
{
  "user_id": "uuid",
  "title": "Conversation Title (optional)",
  "page_context": "string (optional)",
  "page_id": "uuid (optional)",
  "model": "model-name (optional)",
  "server": "server-id (optional)",
  "conversation_type": "chat",
  "persona_id": "uuid (optional)"
}
```

### Get Conversation

```http
GET /api/v1/conversations/{conversation_id}
GET /api/v1/conversations/{conversation_id}/with-messages
GET /api/v1/conversations/{conversation_id}/with-persona
```

### Update Conversation

```http
PUT /api/v1/conversations/{conversation_id}
```

### Update Conversation Persona

```http
PUT /api/v1/conversations/{conversation_id}/persona
```

### Delete Conversation

```http
DELETE /api/v1/conversations/{conversation_id}
```

### List Messages

```http
GET /api/v1/conversations/{conversation_id}/messages
```

### Create Message

```http
POST /api/v1/conversations/{conversation_id}/messages
```

**Request Body:**
```json
{
  "sender": "user | assistant | system",
  "message": "Message content",
  "message_metadata": { "any": "metadata" }
}
```

### Conversation Tags

```http
POST /api/v1/conversations/{conversation_id}/tags
DELETE /api/v1/conversations/{conversation_id}/tags/{tag_id}
```

---

## Personas

Personas are reusable AI configurations with system prompts and model settings.

### List Personas

```http
GET /api/v1/personas
```

**Response:**
```json
{
  "personas": [...],
  "total_items": 10,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

### Create Persona

```http
POST /api/v1/personas
```

**Request Body:**
```json
{
  "name": "Helpful Assistant",
  "description": "A friendly AI helper",
  "system_prompt": "You are a helpful assistant...",
  "model_settings": {
    "temperature": 0.7,
    "max_tokens": 2000
  },
  "avatar": "avatar-url (optional)",
  "tags": ["general", "assistant"],
  "sample_greeting": "Hello! How can I help?",
  "is_active": true
}
```

### Get Persona

```http
GET /api/v1/personas/{persona_id}
```

### Update Persona

```http
PUT /api/v1/personas/{persona_id}
```

### Delete Persona

```http
DELETE /api/v1/personas/{persona_id}
```

### Get Available Tags

```http
GET /api/v1/personas/tags
```

---

## Tags

Tags can be applied to conversations and other resources.

### List User Tags

```http
GET /api/v1/users/{user_id}/tags
```

### Create Tag

```http
POST /api/v1/tags
```

**Request Body:**
```json
{
  "name": "important",
  "color": "#ff0000",
  "user_id": "uuid"
}
```

### Update Tag

```http
PUT /api/v1/tags/{tag_id}
```

### Delete Tag

```http
DELETE /api/v1/tags/{tag_id}
```

### Get Conversations by Tag

```http
GET /api/v1/tags/{tag_id}/conversations
```

---

## AI Providers

### Get Available Providers

```http
GET /api/v1/ai/providers/providers
```

### Validate Provider Configuration

```http
POST /api/v1/ai/providers/validate
```

**Request Body:**
```json
{
  "provider": "ollama | openai | anthropic",
  "config": {
    "server_url": "http://localhost:11434",
    "api_key": "optional"
  }
}
```

### Get Models

```http
GET /api/v1/ai/providers/models?provider=ollama&settings_id=...&server_id=...
GET /api/v1/ai/providers/all-models
```

### Generate Text

```http
POST /api/v1/ai/providers/generate
```

**Request Body:**
```json
{
  "provider": "ollama",
  "settings_id": "uuid",
  "server_id": "uuid",
  "model": "llama2",
  "prompt": "Your prompt here",
  "params": {
    "temperature": 0.7,
    "max_tokens": 1000
  },
  "stream": false
}
```

### Chat Completion

```http
POST /api/v1/ai/providers/chat
```

**Request Body:**
```json
{
  "provider": "ollama",
  "settings_id": "uuid",
  "server_id": "uuid",
  "model": "llama2",
  "messages": [
    { "role": "system", "content": "You are helpful." },
    { "role": "user", "content": "Hello!" }
  ],
  "stream": true,
  "conversation_id": "uuid (optional)",
  "persona_id": "uuid (optional)"
}
```

### Cancel Generation

```http
POST /api/v1/ai/providers/cancel
```

### Provider Settings

```http
GET /api/v1/ai/settings/providers
POST /api/v1/ai/settings/providers
DELETE /api/v1/ai/settings/providers/{provider}/{instance_id}
GET /api/v1/ai/settings/servers/{settings_id}
```

---

## Jobs

Background job management for long-running tasks.

### Create Job

```http
POST /api/v1/jobs
```

**Request Body:**
```json
{
  "job_type": "plugin_install | model_download | ...",
  "payload": { "task": "specific", "data": "..." },
  "priority": 1,
  "scheduled_for": "2025-01-06T... (optional)",
  "idempotency_key": "unique-key (optional)"
}
```

### List Jobs

```http
GET /api/v1/jobs
```

**Query Parameters:**
- `status`: pending | running | completed | failed | cancelled
- `job_type`: Filter by type

### Get Job

```http
GET /api/v1/jobs/{job_id}
```

**Response:**
```json
{
  "id": "uuid",
  "job_type": "string",
  "status": "pending | running | completed | failed | cancelled",
  "priority": 1,
  "progress_percent": 50,
  "result": {...},
  "error": "error message if failed",
  "created_at": "...",
  "started_at": "...",
  "completed_at": "..."
}
```

### Cancel Job

```http
POST /api/v1/jobs/{job_id}/cancel
```

### Retry Job

```http
POST /api/v1/jobs/{job_id}/retry
```

### Get Job Events

```http
GET /api/v1/jobs/{job_id}/events
```

### Stream Job Events (SSE)

```http
GET /api/v1/jobs/{job_id}/events/stream
```

Server-Sent Events stream for real-time job progress.

### Get Job Logs

```http
GET /api/v1/jobs/{job_id}/logs
```

### Delete Job

```http
DELETE /api/v1/jobs/{job_id}
```

---

## Navigation Routes

### Get Navigation Tree

```http
GET /api/v1/navigation-routes/tree
```

Returns hierarchical navigation structure.

### List Navigation Routes

```http
GET /api/v1/navigation-routes
```

### Create Navigation Route

```http
POST /api/v1/navigation-routes
```

**Request Body:**
```json
{
  "name": "Route Name",
  "route": "/route-path",
  "icon": "icon-name",
  "description": "Route description",
  "order": 1,
  "parent_id": "uuid (optional)",
  "is_expanded": true
}
```

### Get Navigation Route

```http
GET /api/v1/navigation-routes/{route_id}
```

### Update Navigation Route

```http
PUT /api/v1/navigation-routes/{route_id}
```

### Move Navigation Route

```http
PUT /api/v1/navigation-routes/{route_id}/move
```

**Request Body:**
```json
{
  "parent_id": "new-parent-uuid | null",
  "display_order": 2
}
```

### Batch Update Navigation Routes

```http
POST /api/v1/navigation-routes/batch-update
```

**Request Body:** Array of route updates
```json
[
  { "id": "uuid", "parent_id": "...", "display_order": 1, "is_expanded": true },
  { "id": "uuid", "parent_id": "...", "display_order": 2 }
]
```

### Delete Navigation Route

```http
DELETE /api/v1/navigation-routes/{route_id}
```

---

## Components

Reusable UI components.

### List Components

```http
GET /api/v1/components
```

### Create Component

```http
POST /api/v1/components
```

**Request Body:**
```json
{
  "name": "My Component",
  "component_id": "unique-id",
  "description": "What it does",
  "icon": "icon-name",
  "is_system": false
}
```

### Get Component

```http
GET /api/v1/components/{component_id}
```

### Update Component

```http
PUT /api/v1/components/{component_id}
```

### Delete Component

```http
DELETE /api/v1/components/{component_id}
```

---

## Documents

Document processing for AI context.

### Process Document

```http
POST /api/v1/documents/process
```

**Request:** `multipart/form-data` with `file` field

Extracts text content from uploaded documents.

### Process Multiple Documents

```http
POST /api/v1/documents/process-multiple
```

**Request:** `multipart/form-data` with `files` field

### Process Text Context

```http
POST /api/v1/documents/process-text-context
```

### Get Supported File Types

```http
GET /api/v1/documents/supported-types
```

---

## Search (SearXNG)

Web search integration (requires SearXNG service).

### Web Search

```http
GET /api/v1/searxng/web
```

**Query Parameters:**
- `q`: Search query
- `categories`: Search categories
- `engines`: Specific engines to use

### Scrape URLs

```http
POST /api/v1/searxng/scrape
```

**Request Body:**
```json
{
  "urls": ["https://example.com", "https://other.com"]
}
```

### Health Check

```http
GET /api/v1/searxng/health
```

---

## Ollama Integration

Direct Ollama model management.

### Test Connection

```http
GET /api/v1/ollama/test
```

### Get Models

```http
GET /api/v1/ollama/models
```

### Install Model

```http
POST /api/v1/ollama/install
```

**Request Body:**
```json
{
  "name": "llama2",
  "server_url": "http://localhost:11434",
  "stream": true,
  "force_reinstall": false
}
```

### Get Install Status

```http
GET /api/v1/ollama/install/{task_id}
```

### Stream Install Events

```http
GET /api/v1/ollama/install/{task_id}/events
```

### Cancel Install

```http
DELETE /api/v1/ollama/install/{task_id}
```

### Delete Model

```http
DELETE /api/v1/ollama/delete
```

**Request Body:**
```json
{
  "name": "model-name",
  "server_url": "http://localhost:11434"
}
```

---

## Health Check

```http
GET /health
```

Returns `200 OK` if the service is running.
