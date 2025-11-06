import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

import httpx

from app.services.job_manager import BaseJobHandler, JobExecutionContext
from app.utils.ollama import normalize_server_base


class OllamaInstallHandler(BaseJobHandler):
    """Job handler that installs models via an Ollama server."""

    job_type = "ollama.install"
    display_name = "Ollama Model Install"
    description = "Download and install an Ollama model onto the configured server."
    default_config = {"timeout_seconds": 1800}
    logger = logging.getLogger(__name__)

    async def validate_payload(self, payload: Dict[str, Any]) -> None:
        if "model_name" not in payload or not payload["model_name"]:
            raise ValueError("model_name is required")
        if "server_url" not in payload or not payload["server_url"]:
            raise ValueError("server_url is required")

    async def execute(self, context: JobExecutionContext) -> Dict[str, Any]:
        payload = context.payload
        model_name: str = payload["model_name"]
        server_url: str = payload["server_url"]
        api_key: Optional[str] = payload.get("api_key")
        force_reinstall: bool = bool(payload.get("force_reinstall", False))

        server_base = normalize_server_base(server_url)
        pull_url = f"{server_base}/api/pull"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        timeout_seconds = int(payload.get("timeout_seconds", self.default_config["timeout_seconds"]))
        timeout = httpx.Timeout(timeout_seconds, connect=30.0)

        await context.report_progress(
            percent=0,
            stage="queued",
            message="Waiting to start download",
            data={"model_name": model_name, "server_url": server_base},
        )

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            await context.check_for_cancel()
            request_body = {"name": model_name, "stream": True, "keep_alive": False, "force": force_reinstall}
            try:
                async with client.stream("POST", pull_url, headers=headers, json=request_body) as response:
                    await context.report_progress(
                        event_type="status",
                        stage="connecting",
                        message=f"Ollama responded with status {response.status_code}",
                        data={"status_code": response.status_code},
                    )
                    if response.status_code != 200:
                        raw = await response.aread()
                        text = raw.decode(errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                        raise RuntimeError(text or f"Ollama returned HTTP {response.status_code}")

                    await context.report_progress(
                        percent=1,
                        stage="downloading",
                        message="Starting download",
                        data={"force_reinstall": force_reinstall, "progress_percent": 1},
                    )

                    last_logged_bucket = -1
                    async for line in response.aiter_lines():
                        await context.check_for_cancel()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            await context.report_progress(event_type="log", message=line)
                            continue

                        if isinstance(data, dict):
                            if data.get("error"):
                                raise RuntimeError(str(data["error"]))

                            if "status" in data:
                                progress_value = data.get("progress")
                                percent: Optional[int] = None
                                if progress_value is not None:
                                    try:
                                        percent = int(progress_value)
                                    except (ValueError, TypeError):
                                        percent = None
                                if percent is None:
                                    total = data.get("total")
                                    completed = data.get("completed")
                                    if isinstance(total, (int, float)) and total:
                                        try:
                                            ratio = float(completed or 0) / float(total)
                                            percent = int(max(0.0, min(0.99, ratio)) * 100)
                                        except (ValueError, TypeError, ZeroDivisionError):
                                            percent = None
                                if percent is not None:
                                    percent = max(1, min(99, percent))
                                if percent is not None:
                                    bucket = percent // 5
                                    if bucket > last_logged_bucket:
                                        last_logged_bucket = bucket
                                        self.logger.info(
                                            "Ollama install progress %s%% [%s]",
                                            percent,
                                            data.get("status"),
                                        )
                                event_payload = {
                                    **data,
                                    "progress_percent": percent,
                                }
                                await context.report_progress(
                                    percent=percent,
                                    stage="downloading",
                                    message=str(data["status"]),
                                    data=event_payload,
                                )

                            completed_flag = bool(data.get("completed")) or bool(data.get("done"))
                            status_text = str(data.get("status", "")).strip().lower()
                            if completed_flag or status_text == "success":
                                finalizing_payload = {
                                    **data,
                                    "progress_percent": 99,
                                    "stage": "finalizing",
                                    "message": "Download completed, finalizing installation",
                                }
                                await context.report_progress(
                                    percent=99,
                                    stage="finalizing",
                                    message="Download completed, finalizing installation",
                                    data=finalizing_payload,
                                )
                                metadata = await self._wait_for_model_registration(
                                    server_base,
                                    headers,
                                    model_name,
                                    digest=str(data.get("digest")) if data.get("digest") else None,
                                )
                                completed_payload = {
                                    **metadata,
                                    "progress_percent": 100,
                                    "stage": "completed",
                                    "message": "Model installed successfully",
                                }
                                await context.report_progress(
                                    percent=100,
                                    stage="completed",
                                    message="Model installed successfully",
                                    data=completed_payload,
                                )
                                return {
                                    "model_name": model_name,
                                    "server_url": server_base,
                                    "force_reinstall": force_reinstall,
                                    **metadata,
                                }
            except httpx.TimeoutException as exc:
                raise RuntimeError("Timed out while communicating with the Ollama server") from exc
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Ollama request failed: {exc}") from exc

        metadata = await self._wait_for_model_registration(server_base, headers, model_name, digest=None)
        completed_payload = {
            **metadata,
            "progress_percent": 100,
            "stage": "completed",
            "message": "Model installed successfully",
        }
        await context.report_progress(
            percent=100,
            stage="completed",
            message="Model installed successfully",
            data=completed_payload,
        )
        return {
            "model_name": model_name,
            "server_url": server_base,
            "force_reinstall": force_reinstall,
            **metadata,
        }

    async def _wait_for_model_registration(
        self,
        server_base: str,
        headers: Dict[str, str],
        model_name: str,
        digest: Optional[str],
    ) -> Dict[str, Any]:
        canonical = model_name.split(":", 1)[0]
        identifiers = {model_name, canonical}
        if digest:
            identifiers.add(digest)
        backoff = [0, 1, 1, 2, 3]
        extended_waits = [5] * 6 + [10] * 6 + [20] * 3
        for delay in backoff + extended_waits:
            if delay:
                await asyncio.sleep(delay)
            show_entry = await self._fetch_show(server_base, headers, model_name)
            if show_entry:
                return show_entry
            payload = await self._fetch_tags(server_base, headers)
            entry = self._find_model_entry(payload, identifiers)
            if entry:
                return {
                    "digest": entry.get("digest"),
                    "size": entry.get("size"),
                    "modified_at": entry.get("modified") or entry.get("modified_at"),
                }
        raise RuntimeError(f"Model {model_name} not present on Ollama server after install")

    async def _fetch_tags(self, server_base: str, headers: Dict[str, str]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{server_base}/api/tags", headers=headers)
            response.raise_for_status()
            return response.json()

    async def _fetch_show(self, server_base: str, headers: Dict[str, str], model_name: str) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{server_base}/api/show",
                headers=headers,
                json={"name": model_name},
            )
        if response.status_code == 200:
            payload = response.json() or {}
            return {
                "digest": payload.get("digest"),
                "size": payload.get("size"),
                "modified_at": payload.get("modified") or payload.get("modified_at"),
            }
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return None

    def _find_model_entry(self, tags_payload: Dict[str, Any], identifiers: Set[str]) -> Optional[Dict[str, Any]]:
        models = tags_payload.get("models") or []
        for model in models:
            tokens: Set[str] = set()
            for key in ("name", "model", "digest"):
                value = model.get(key)
                if value:
                    tokens.add(str(value))
            aliases = model.get("aliases") or []
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str):
                        tokens.add(alias)
                    elif isinstance(alias, dict):
                        for v in alias.values():
                            if v:
                                tokens.add(str(v))
            expanded_tokens = set(tokens)
            expanded_tokens.update(token.split(":", 1)[0] for token in tokens if ":" in token)
            if identifiers & expanded_tokens:
                return model
        return None
