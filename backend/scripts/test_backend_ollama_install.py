#!/usr/bin/env python3
"""End-to-end tester for BrainDrive's Ollama install flow.

The script logs in via the public API, optionally cleans up an existing model,
requests a new install job, and streams the legacy SSE feed so we can validate
progress percent behaviour from the backend rather than the raw Ollama server.

Example:
    python backend/scripts/test_backend_ollama_install.py \
        --api-base http://10.1.2.149:8005 \
        --email aaaa@gmail.com \
        --password 1001vb60 \
        --server-url http://10.1.2.149:11434 \
        --model phi3:latest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

import httpx


DEFAULT_API_BASE = os.getenv("BRAINDRIVE_API_BASE", "http://10.1.2.149:8005")
DEFAULT_SERVER_URL = os.getenv("OLLAMA_SERVER_URL", "http://10.1.2.149:11434")


class BrainDriveClient:
    """Small helper around httpx to talk to the backend API with auth."""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._token: Optional[str] = None

    async def login(self, email: str, password: str) -> str:
        response = await self._client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Login succeeded but no access_token returned")
        self._token = token
        return token

    @property
    def token(self) -> str:
        if not self._token:
            raise RuntimeError("Client is not authenticated; call login() first")
        return self._token

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def delete_model(self, *, model_name: str, server_url: str, api_key: Optional[str]) -> None:
        payload = {
            "name": model_name,
            "server_url": server_url,
        }
        if api_key:
            payload["api_key"] = api_key
        response = await self._client.request(
            "DELETE",
            "/api/v1/ollama/delete",
            json=payload,
            headers=self._auth_headers(),
        )
        if response.status_code not in (200, 204):
            # Deletions will fail if the model is missing; log and continue.
            detail = response.text
            print(f"[delete] warning: {response.status_code} {detail}")
        else:
            print(f"[delete] model '{model_name}' removal requested")

    async def enqueue_install(
        self,
        *,
        model_name: str,
        server_url: str,
        api_key: Optional[str],
        force: bool,
    ) -> str:
        payload: Dict[str, Any] = {
            "name": model_name,
            "server_url": server_url,
            "force_reinstall": force,
        }
        if api_key:
            payload["api_key"] = api_key
        response = await self._client.post(
            "/api/v1/ollama/install",
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        data = response.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Install request succeeded but task_id missing: {data}")
        print(f"[install] queued job {task_id} for model '{model_name}'")
        return task_id

    async def poll_job_snapshot(self, task_id: str) -> Dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/ollama/install/{task_id}",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def stream_install_events(self, task_id: str) -> None:
        headers = self._auth_headers()
        headers["Accept"] = "text/event-stream"
        url = f"/api/v1/ollama/install/{task_id}/events"
        async with self._client.stream("GET", url, headers=headers, timeout=None) as response:
            response.raise_for_status()
            print("[events] connected to SSE stream")
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    # Comment / heartbeat.
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    print(f"[events] malformed payload: {data}")
                    continue
                self._print_event(payload)
                if payload.get("state") in {"completed", "error", "canceled"}:
                    break

    def _print_event(self, payload: Dict[str, Any]) -> None:
        state = payload.get("state") or payload.get("stage") or "unknown"
        progress = payload.get("progress")
        message = payload.get("message") or payload.get("detail") or ""
        digest = payload.get("digest")
        parts = [f"[events] {state}"]
        if isinstance(progress, (int, float)):
            parts.append(f"{progress:5.1f}%")
        if digest:
            parts.append(digest)
        if message:
            parts.append(f"- {message}")
        print(" ".join(parts))

    async def close(self) -> None:
        await self._client.aclose()


async def run_test(args: argparse.Namespace) -> int:
    client = BrainDriveClient(args.api_base, timeout=args.http_timeout)
    try:
        print(f"[auth] logging in as {args.email}")
        await client.login(args.email, args.password)

        server_url = args.server_url.rstrip("/")
        api_key = args.server_api_key or None

        if not args.skip_remove:
            print(f"[setup] removing existing model '{args.model}' (if present)")
            await client.delete_model(model_name=args.model, server_url=server_url, api_key=api_key)

        print(f"[install] enqueueing '{args.model}' on {server_url}")
        task_id = await client.enqueue_install(
            model_name=args.model,
            server_url=server_url,
            api_key=api_key,
            force=args.force_reinstall,
        )

        print("[events] waiting for progress stream...")
        await client.stream_install_events(task_id)

        snapshot = await client.poll_job_snapshot(task_id)
        print("[result]", json.dumps(snapshot, indent=2))
        return 0
    except httpx.HTTPStatusError as exc:
        print(f"[error] HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BrainDrive Ollama install tester")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="BrainDrive API base URL")
    parser.add_argument("--email", default=os.getenv("BRAINDRIVE_EMAIL", "aaaa@gmail.com"), help="Login email")
    parser.add_argument(
        "--password",
        default=os.getenv("BRAINDRIVE_PASSWORD", "1001vb60"),
        help="Login password",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help="Ollama server base URL (ex: http://host:11434)",
    )
    parser.add_argument(
        "--server-api-key",
        default=os.getenv("OLLAMA_API_KEY"),
        help="Optional Ollama server API key",
    )
    parser.add_argument(
        "--model",
        default="phi3:latest",
        help="Model identifier to install",
    )
    parser.add_argument(
        "--skip-remove",
        action="store_true",
        help="Skip deleting the model before installing",
    )
    parser.add_argument(
        "--force-reinstall",
        action="store_true",
        help="Force Ollama to re-download even if cached",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=60.0,
        help="HTTP timeout for non-streaming requests",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    exit_code = asyncio.run(run_test(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
