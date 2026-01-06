# API Reference

This reference documents the BrainDrive APIs available to plugin developers and integrations.

## Overview

BrainDrive exposes three API surfaces:

| API Surface | Description | When to Use |
|-------------|-------------|-------------|
| [Backend REST API](./backend-api.md) | FastAPI endpoints at `localhost:8005` | Direct HTTP calls, backend integrations |
| [Service Bridges](./service-bridges-api.md) | Frontend TypeScript interfaces | Plugin frontend code (React components) |
| [Plugin API Contracts](./plugin-api-contracts.md) | Plugin registration and lifecycle | Plugin manifest, module definitions |
| [Common Workflows](./workflows.md) | Practical examples and recipes | Tying the APIs together to build features |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      BrainDrive Frontend                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Plugin A  │  │   Plugin B  │  │    Core UI          │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                     │             │
│         └────────────────┼─────────────────────┘             │
│                          │                                   │
│                  ┌───────▼───────┐                           │
│                  │Service Bridges│                           │
│                  │ (TypeScript)  │                           │
│                  └───────┬───────┘                           │
└──────────────────────────┼───────────────────────────────────┘
                           │ HTTP/WebSocket
┌──────────────────────────▼───────────────────────────────────┐
│                   BrainDrive Backend                          │
│                   FastAPI (port 8005)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │   Auth   │ │ Plugins  │ │  Pages   │ │   AI Providers   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## Authentication

BrainDrive uses **JWT tokens** for authentication.

- Login sets access and refresh JWTs as HTTP-only cookies (response body is empty)
- For non-browser clients, send `Authorization: Bearer <access_token>`
- Refresh tokens via `POST /api/v1/auth/refresh` (sets a new access cookie; response body is empty)
- Plugins access user context automatically through the Service Bridges

**Current scope:** All resources are user-owned. There is no multi-tenant or admin scope at this time.

## Base URL

| Environment | URL |
|-------------|-----|
| Local Development | `http://localhost:8005` |
| API Docs (Swagger) | `http://localhost:8005/docs` |

BrainDrive is self-hosted; there is no shared staging host. Use the base URL of your deployed instance in production. The `/api/v1` prefix is stable until v2.

## Response Format

All endpoints return JSON. Successful responses typically include the requested data directly. Error responses follow this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

Validation errors include field-level details:

```json
{
  "detail": [
    {
      "loc": ["body", "field_name"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

## Common HTTP Status Codes

All endpoints are rate-limited. Expect `429 Too Many Requests` if clients exceed limits.

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | No Content (successful deletion) |
| 400 | Bad Request (invalid input) |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (valid token, insufficient permissions) |
| 404 | Not Found |
| 422 | Validation Error |
| 429 | Too Many Requests (rate limited) |
| 500 | Internal Server Error |

## Rate Limiting

The API employs rate limiting to ensure stability and fair use. If you send too many requests in a short period, you will receive a `429 Too Many Requests` HTTP status code.

When this occurs, the response will include a `Retry-After` header indicating how many seconds you should wait before sending another request.

> **Note:** The specific rate limits are not yet published. This documentation will be updated once the limits are finalized.

## Pagination

For endpoints that return a list of items (e.g., `GET /api/v1/pages`), the results are paginated. Pagination is controlled via query parameters:

-   `limit`: The number of items to return per page.
-   `offset`: The starting index from which to return items.

A paginated response body will have the following structure:

```json
{
  "items": [ ... ],
  "total_items": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

> **Note:** Pagination is not yet implemented on all list endpoints. Please check the documentation for each specific endpoint in the [Backend REST API Reference](./backend-api.md) to confirm if it supports pagination.

## Quick Links

- **Getting started with plugins?** See [Plugin API Contracts](./plugin-api-contracts.md)
- **Making API calls from a plugin?** See [Service Bridges](./service-bridges-api.md)
- **Building a backend integration?** See [Backend REST API](./backend-api.md)
- **Looking for examples?** See [Common API Workflows](./workflows.md)
