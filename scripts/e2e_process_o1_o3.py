#!/usr/bin/env python3
"""Live probe harness for Process O.1 and O.3.

O.1: VS-12/TR-4/VS-6 long-window mixed-traffic soak closure.
O.3: TR-3/TR-5/VS-4 external provider runtime matrix expansion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ProbeSummary:
    run_id: str
    started_at: str
    base_url: str
    provider: str
    model: str
    reset_applied: bool
    assertions: List[AssertionResult] = field(default_factory=list)
    scenarios: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: Optional[str] = None


@dataclass
class Config:
    base_url: str
    email: str
    password: str
    provider: str
    settings_id: str
    server_id: str
    model: str
    openrouter_model: str
    openai_model: str
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path
    soak_iterations: int
    skip_o1: bool
    skip_o3: bool


@dataclass
class LocalWebhookServer:
    server: ThreadingHTTPServer
    thread: threading.Thread
    records: List[Dict[str, Any]]
    base_url: str

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


_LAST_REQUEST_SENT_MONOTONIC: Optional[float] = None


def _assert(summary: ProbeSummary, name: str, condition: bool, detail: str = "") -> None:
    summary.assertions.append(AssertionResult(name=name, passed=bool(condition), detail=detail))
    if not condition:
        raise RuntimeError(f"Assertion failed: {name}. {detail}".strip())


def _decode_json_bytes(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {"raw": text}
    if isinstance(parsed, dict):
        return parsed
    return {"data": parsed}


def _throttle(request_delay_seconds: float) -> None:
    global _LAST_REQUEST_SENT_MONOTONIC
    if request_delay_seconds <= 0:
        return
    now = time.monotonic()
    if _LAST_REQUEST_SENT_MONOTONIC is not None:
        elapsed = now - _LAST_REQUEST_SENT_MONOTONIC
        if elapsed < request_delay_seconds:
            time.sleep(request_delay_seconds - elapsed)
    _LAST_REQUEST_SENT_MONOTONIC = time.monotonic()


def _http_json(
    *,
    method: str,
    url: str,
    timeout_seconds: int,
    request_delay_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    token: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    body: Optional[bytes] = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempt = 0
    while True:
        request_obj = urllib_request.Request(url=url, data=body, headers=headers, method=method.upper())
        try:
            _throttle(request_delay_seconds)
            with urllib_request.urlopen(request_obj, timeout=timeout_seconds) as response:
                return int(response.status), _decode_json_bytes(response.read())
        except urllib_error.HTTPError as exc:
            parsed = _decode_json_bytes(exc.read())
            if exc.code == 429 and attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            return int(exc.code), parsed
        except urllib_error.URLError as exc:
            if attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _login(config: Config) -> Tuple[str, str]:
    status, response = _http_json(
        method="POST",
        url=f"{config.base_url.rstrip('/')}/api/v1/auth/login",
        payload={"email": config.email, "password": config.password},
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=max(config.request_delay_seconds, 1.0),
        max_retries=max(config.http_max_retries, 6),
        retry_base_seconds=max(config.http_retry_base_seconds, 2.5),
    )
    if status != 200:
        raise RuntimeError(f"Login failed: status={status} response={response}")
    token = response.get("access_token")
    user_id = response.get("user_id")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Login missing access_token")
    if not isinstance(user_id, str) or not user_id.strip():
        raise RuntimeError("Login missing user_id")
    return token, user_id


def _normalize_user_id(user_id: str) -> str:
    return str(user_id).replace("-", "").strip()


def _reset_scope_from_template(config: Config, user_id: str) -> Dict[str, Any]:
    normalized_user_id = _normalize_user_id(user_id)
    user_scope = config.library_root / "users" / normalized_user_id
    if user_scope.exists():
        shutil.rmtree(user_scope)

    script_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "scripts"
        / "bootstrap_library_user_scope.py"
    )
    cmd = [
        sys.executable,
        str(script_path),
        "--library-root",
        str(config.library_root),
        "--user-id",
        normalized_user_id,
        "--template-root",
        str(config.template_root),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "Scope bootstrap failed: "
            f"stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bootstrap returned non-JSON: {completed.stdout}") from exc


def _build_chat_payload(
    *,
    provider: str,
    settings_id: str,
    server_id: str,
    model: str,
    user_id: str,
    message: str,
    conversation_type: str,
    mcp_scope_mode: str,
    mcp_project_slug: Optional[str] = None,
    mcp_project_name: Optional[str] = None,
    params_extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "mcp_tools_enabled": True,
        "mcp_scope_mode": mcp_scope_mode,
        "mcp_sync_on_request": False,
        "mcp_auto_approve_mutating": False,
        "mcp_max_tool_iterations": 6,
        "mcp_provider_timeout_seconds": 60,
    }
    if mcp_project_slug is not None:
        params["mcp_project_slug"] = mcp_project_slug
    if mcp_project_name is not None:
        params["mcp_project_name"] = mcp_project_name
        params["mcp_project_source"] = "ui"
    if params_extra:
        params.update(params_extra)

    payload: Dict[str, Any] = {
        "provider": provider,
        "settings_id": settings_id,
        "server_id": server_id,
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "user_id": user_id,
        "conversation_type": conversation_type,
        "params": params,
        "stream": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload


def _chat(config: Config, token: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    return _http_json(
        method="POST",
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/chat",
        payload=payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )


def _approve_pending_request(
    *,
    config: Config,
    token: str,
    base_payload: Dict[str, Any],
    conversation_id: str,
    request_id: str,
    action: str,
) -> Tuple[int, Dict[str, Any]]:
    payload = dict(base_payload)
    payload["conversation_id"] = conversation_id
    payload["messages"] = [{"role": "user", "content": action}]
    params = dict(payload.get("params") or {})
    params["mcp_approval"] = {"action": action, "request_id": request_id}
    payload["params"] = params
    return _chat(config, token, payload)


def _start_local_webhook_server() -> LocalWebhookServer:
    records: List[Dict[str, Any]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except Exception:
                payload = {"_raw": raw}

            records.append(
                {
                    "path": self.path,
                    "headers": {k: v for k, v in self.headers.items()},
                    "payload": payload,
                }
            )

            response = {"status": "accepted", "ack_id": f"ack-{len(records)}"}
            encoded = json.dumps(response).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return LocalWebhookServer(
        server=server,
        thread=thread,
        records=records,
        base_url=f"http://{host}:{port}",
    )


def _run_o1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"iterations": config.soak_iterations}
    webhook = _start_local_webhook_server()
    scenario["webhook_base_url"] = webhook.base_url

    digest_email_runs: List[Dict[str, Any]] = []
    digest_slack_runs: List[Dict[str, Any]] = []
    pre_compaction_runs: List[Dict[str, Any]] = []
    capture_runs: List[Dict[str, Any]] = []

    digest_email_conversation_id: Optional[str] = None
    digest_slack_conversation_id: Optional[str] = None
    chat_conversation_id: Optional[str] = None
    capture_conversation_id: Optional[str] = None

    started_mono = time.monotonic()

    try:
        for index in range(config.soak_iterations):
            pair = index // 2
            email_event_id = f"o1-email-{pair}"
            slack_event_id = f"o1-slack-{pair}"
            pre_event_id = f"o1-pre-{pair}"

            email_status, email_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=config.provider,
                    settings_id=config.settings_id,
                    server_id=config.server_id,
                    model=config.model,
                    user_id=user_id,
                    message=f"Process O.1 digest email iteration {index + 1}.",
                    conversation_type="digest-email",
                    mcp_scope_mode="none",
                    conversation_id=digest_email_conversation_id,
                    params_extra={
                        "mcp_tools_enabled": False,
                        "mcp_tool_profile": "read_only",
                        "mcp_digest_schedule_enabled": True,
                        "mcp_digest_force_run": True,
                        "mcp_digest_schedule_event_id": email_event_id,
                        "mcp_digest_delivery_send_enabled": True,
                        "mcp_digest_delivery_endpoint": f"{webhook.base_url}/email",
                    },
                ),
            )
            if digest_email_conversation_id is None:
                digest_email_conversation_id = str(email_response.get("conversation_id") or "")
            email_tooling = email_response.get("tooling_state") or {}
            digest_email_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": email_event_id,
                    "status_code": email_status,
                    "digest_schedule_status": email_tooling.get("digest_schedule_status"),
                    "digest_schedule_duplicate_guard": email_tooling.get("digest_schedule_duplicate_guard"),
                    "delivery_send_status": email_tooling.get("digest_delivery_send_status"),
                    "delivery_channel": email_tooling.get("digest_delivery_channel"),
                }
            )

            slack_status, slack_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=config.provider,
                    settings_id=config.settings_id,
                    server_id=config.server_id,
                    model=config.model,
                    user_id=user_id,
                    message=f"Process O.1 digest slack iteration {index + 1}.",
                    conversation_type="digest-slack",
                    mcp_scope_mode="none",
                    conversation_id=digest_slack_conversation_id,
                    params_extra={
                        "mcp_tools_enabled": False,
                        "mcp_tool_profile": "read_only",
                        "mcp_digest_schedule_enabled": True,
                        "mcp_digest_force_run": True,
                        "mcp_digest_schedule_event_id": slack_event_id,
                        "mcp_digest_delivery_send_enabled": True,
                        "mcp_digest_delivery_endpoint": f"{webhook.base_url}/slack",
                    },
                ),
            )
            if digest_slack_conversation_id is None:
                digest_slack_conversation_id = str(slack_response.get("conversation_id") or "")
            slack_tooling = slack_response.get("tooling_state") or {}
            digest_slack_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": slack_event_id,
                    "status_code": slack_status,
                    "digest_schedule_status": slack_tooling.get("digest_schedule_status"),
                    "digest_schedule_duplicate_guard": slack_tooling.get("digest_schedule_duplicate_guard"),
                    "delivery_send_status": slack_tooling.get("digest_delivery_send_status"),
                    "delivery_channel": slack_tooling.get("digest_delivery_channel"),
                }
            )

            long_message = " ".join(["o1-context-pressure"] * 320)
            pre_status, pre_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=config.provider,
                    settings_id=config.settings_id,
                    server_id=config.server_id,
                    model=config.model,
                    user_id=user_id,
                    message=long_message,
                    conversation_type="chat",
                    mcp_scope_mode="none",
                    conversation_id=chat_conversation_id,
                    params_extra={
                        "mcp_tools_enabled": False,
                        "mcp_pre_compaction_flush_enabled": True,
                        "mcp_context_window_tokens": 64,
                        "mcp_pre_compaction_flush_threshold": 0.5,
                        "mcp_pre_compaction_event_id": pre_event_id,
                    },
                ),
            )
            if chat_conversation_id is None:
                chat_conversation_id = str(pre_response.get("conversation_id") or "")
            pre_tooling = pre_response.get("tooling_state") or {}
            pre_compaction_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": pre_event_id,
                    "status_code": pre_status,
                    "pre_compaction_flush_status": pre_tooling.get("pre_compaction_flush_status"),
                    "pre_compaction_flush_duplicate_guard": pre_tooling.get("pre_compaction_flush_duplicate_guard"),
                }
            )

            capture_payload = _build_chat_payload(
                provider=config.provider,
                settings_id=config.settings_id,
                server_id=config.server_id,
                model=config.model,
                user_id=user_id,
                message=f"Create task O1 iteration {index + 1}: reconcile finance notes by 2026-03-20 for Dave J.",
                conversation_type="capture",
                mcp_scope_mode="project",
                mcp_project_slug="projects/active/finance",
                mcp_project_name="Finance",
                conversation_id=capture_conversation_id,
                params_extra={"mcp_max_tool_iterations": 7},
            )
            cap_status, cap_response = _chat(config, token, capture_payload)
            if capture_conversation_id is None:
                capture_conversation_id = str(cap_response.get("conversation_id") or "")
            capture_entry: Dict[str, Any] = {
                "iteration": index + 1,
                "status_code": cap_status,
                "approval_required": cap_response.get("approval_required"),
                "tooling_state": cap_response.get("tooling_state"),
            }
            if cap_response.get("approval_required") is True:
                request = cap_response.get("approval_request") or {}
                request_id = str(request.get("request_id") or "").strip()
                if request_id:
                    action = "approve" if index % 2 == 0 else "reject"
                    resume_status, resume_response = _approve_pending_request(
                        config=config,
                        token=token,
                        base_payload=capture_payload,
                        conversation_id=str(cap_response.get("conversation_id") or ""),
                        request_id=request_id,
                        action=action,
                    )
                    capture_entry["approval_action"] = action
                    capture_entry["approval_resume_status_code"] = resume_status
                    capture_entry["approval_resolution"] = resume_response.get("approval_resolution")
            capture_runs.append(capture_entry)

        elapsed_seconds = round(time.monotonic() - started_mono, 3)

        email_dup = sum(1 for row in digest_email_runs if str(row.get("digest_schedule_status") or "") == "duplicate_guard")
        slack_dup = sum(1 for row in digest_slack_runs if str(row.get("digest_schedule_status") or "") == "duplicate_guard")
        pre_dup = sum(
            1
            for row in pre_compaction_runs
            if str(row.get("pre_compaction_flush_status") or "") == "duplicate_guard"
        )
        capture_resolved = sum(
            1
            for row in capture_runs
            if str((row.get("approval_resolution") or {}).get("status") or "") in {"approved", "rejected"}
        )
        email_sent = sum(1 for row in digest_email_runs if str(row.get("delivery_send_status") or "") == "sent")
        slack_sent = sum(1 for row in digest_slack_runs if str(row.get("delivery_send_status") or "") == "sent")
        email_hits = sum(1 for rec in webhook.records if str(rec.get("path") or "").startswith("/email"))
        slack_hits = sum(1 for rec in webhook.records if str(rec.get("path") or "").startswith("/slack"))

        scenario["digest_email_runs"] = digest_email_runs
        scenario["digest_slack_runs"] = digest_slack_runs
        scenario["pre_compaction_runs"] = pre_compaction_runs
        scenario["capture_runs"] = capture_runs
        scenario["aggregate"] = {
            "elapsed_seconds": elapsed_seconds,
            "email_duplicate_guard_count": email_dup,
            "slack_duplicate_guard_count": slack_dup,
            "pre_compaction_duplicate_guard_count": pre_dup,
            "capture_resolved_count": capture_resolved,
            "email_send_count": email_sent,
            "slack_send_count": slack_sent,
            "email_webhook_hits": email_hits,
            "slack_webhook_hits": slack_hits,
        }

        min_dup_expected = max(2, config.soak_iterations // 6)
        min_resolution_expected = max(4, config.soak_iterations // 3)
        min_delivery_expected = max(4, config.soak_iterations // 3)

        _assert(summary, "o1_email_duplicate_guard_seen", email_dup >= min_dup_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_slack_duplicate_guard_seen", slack_dup >= min_dup_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_pre_compaction_duplicate_guard_seen", pre_dup >= min_dup_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_capture_resolution_seen", capture_resolved >= min_resolution_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_email_delivery_seen", email_sent >= min_delivery_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_slack_delivery_seen", slack_sent >= min_delivery_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_email_webhook_hits_seen", email_hits >= min_delivery_expected, str(scenario["aggregate"]))
        _assert(summary, "o1_slack_webhook_hits_seen", slack_hits >= min_delivery_expected, str(scenario["aggregate"]))

        return scenario
    finally:
        webhook.shutdown()


def _run_o3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {
        "catalog": {},
        "runtime_matrix": [],
        "skipped": [],
    }

    cat_status, cat_response = _http_json(
        method="GET",
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/catalog?user_id=current",
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )
    _assert(summary, "o3_catalog_status_200", cat_status == 200, str(cat_response))

    providers = cat_response.get("providers") if isinstance(cat_response, dict) else []
    _assert(summary, "o3_catalog_has_providers", isinstance(providers, list) and len(providers) >= 1, str(cat_response))

    scenario["catalog"] = {
        "status": cat_status,
        "providers": providers,
    }

    provider_map: Dict[str, Dict[str, Any]] = {}
    for entry in providers if isinstance(providers, list) else []:
        if isinstance(entry, dict):
            provider_map[str(entry.get("id") or "").strip()] = entry

    openrouter_entry = provider_map.get("openrouter")
    _assert(summary, "o3_openrouter_visible", isinstance(openrouter_entry, dict), str(providers))
    _assert(
        summary,
        "o3_openrouter_configured",
        bool((openrouter_entry or {}).get("configured")),
        str(openrouter_entry),
    )

    candidates: List[Tuple[str, str, str]] = [
        ("openrouter", "openrouter_api_keys_settings", config.openrouter_model),
        ("openai", "openai_api_keys_settings", config.openai_model),
    ]

    for provider_id, settings_id, model in candidates:
        entry = provider_map.get(provider_id)
        if not isinstance(entry, dict) or not bool(entry.get("configured")):
            scenario["skipped"].append(
                {
                    "provider": provider_id,
                    "reason": "not_configured",
                    "configured": bool(entry.get("configured")) if isinstance(entry, dict) else False,
                }
            )
            continue

        server_id = str(entry.get("default_server_id") or "").strip()
        if not server_id:
            if provider_id == "openrouter":
                server_id = "openrouter_default_server"
            elif provider_id == "openai":
                server_id = "openai_default_server"

        route_status, route_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=provider_id,
                settings_id=settings_id,
                server_id=server_id,
                model=model,
                user_id=user_id,
                message="List one markdown path in life/finances before answering in one sentence.",
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug="life/finances",
                mcp_project_name="Finances",
                params_extra={
                    "mcp_max_tool_iterations": 6,
                    "mcp_tool_profile": "read_only",
                },
            ),
        )
        route_state = route_response.get("tooling_state") if isinstance(route_response, dict) else {}
        route_ok = (
            route_status == 200
            and isinstance(route_state, dict)
            and bool(str(route_state.get("tool_routing_mode") or "").strip())
            and bool(str(route_state.get("tool_execution_mode") or "").strip())
        )

        citation_status, citation_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=provider_id,
                settings_id=settings_id,
                server_id=server_id,
                model=model,
                user_id=user_id,
                message="Which markdown file in life/finances mentions '(to be populated during onboarding)'? include sources.",
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug="life/finances",
                mcp_project_name="Finances",
                params_extra={
                    "mcp_max_tool_iterations": 6,
                    "mcp_tool_profile": "read_only",
                },
            ),
        )
        citation_text = str(((citation_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        citation_state = citation_response.get("tooling_state") if isinstance(citation_response, dict) else {}
        citations = citation_state.get("response_citations") if isinstance(citation_state, dict) else None
        citation_ok = (
            citation_status == 200
            and (
                "sources:" in citation_text.lower()
                or "sources\n" in citation_text.lower()
                or (isinstance(citations, list) and len(citations) > 0)
                or ".md" in citation_text.lower()
            )
        )

        native_status, native_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=provider_id,
                settings_id=settings_id,
                server_id=server_id,
                model=model,
                user_id=user_id,
                message="Read life/finances context and answer briefly.",
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug="life/finances",
                mcp_project_name="Finances",
                params_extra={
                    "mcp_max_tool_iterations": 6,
                    "mcp_tool_profile": "read_only",
                    "mcp_native_tool_calling": True,
                },
            ),
        )
        native_state = native_response.get("tooling_state") if isinstance(native_response, dict) else {}
        native_ok = (
            native_status == 200
            and isinstance(native_state, dict)
            and bool(str(native_state.get("tool_routing_mode") or "").strip())
            and bool(str(native_state.get("routing_capability_source") or "").strip())
        )

        scenario["runtime_matrix"].append(
            {
                "provider": provider_id,
                "settings_id": settings_id,
                "server_id": server_id,
                "model": model,
                "routing_probe": {
                    "status": route_status,
                    "ok": route_ok,
                    "tooling_state": route_state,
                },
                "citation_probe": {
                    "status": citation_status,
                    "ok": citation_ok,
                    "response_citations": citations,
                    "has_sources_block": "sources:" in citation_text.lower() or "sources\n" in citation_text.lower(),
                    "text_excerpt": citation_text[:400],
                },
                "native_override_probe": {
                    "status": native_status,
                    "ok": native_ok,
                    "tooling_state": native_state,
                },
            }
        )

    openrouter_rows = [row for row in scenario["runtime_matrix"] if row.get("provider") == "openrouter"]
    _assert(summary, "o3_openrouter_runtime_row_present", len(openrouter_rows) >= 1, str(scenario["runtime_matrix"]))

    openrouter_pass = any(
        bool(row.get("routing_probe", {}).get("ok"))
        and bool(row.get("citation_probe", {}).get("ok"))
        and bool(row.get("native_override_probe", {}).get("ok"))
        for row in openrouter_rows
    )
    _assert(summary, "o3_openrouter_runtime_pass", openrouter_pass, str(openrouter_rows))

    any_external_pass = any(
        bool(row.get("routing_probe", {}).get("ok")) and bool(row.get("citation_probe", {}).get("ok"))
        for row in scenario["runtime_matrix"]
        if str(row.get("provider") or "") in {"openrouter", "openai"}
    )
    _assert(summary, "o3_external_runtime_matrix_has_pass", any_external_pass, str(scenario["runtime_matrix"]))

    policy_cmd = [
        "/home/hacker/anaconda3/envs/BrainDriveDev/bin/python",
        "-m",
        "pytest",
        "-q",
        "tests/test_mcp_chat_gating.py",
        "-k",
        "other_native_providers_default_to_single_path or model_hint_promotes_unknown_provider_to_native_mode",
    ]
    policy_completed = subprocess.run(
        policy_cmd,
        cwd=str(Path(__file__).resolve().parents[1] / "backend"),
        capture_output=True,
        text=True,
        check=False,
    )
    scenario["policy_regression"] = {
        "command": " ".join(policy_cmd),
        "returncode": policy_completed.returncode,
        "stdout_tail": policy_completed.stdout[-2000:],
        "stderr_tail": policy_completed.stderr[-1000:],
    }
    _assert(summary, "o3_policy_regression_pass", policy_completed.returncode == 0, policy_completed.stdout[-800:])

    citation_cmd = [
        "/home/hacker/anaconda3/envs/BrainDriveDev/bin/python",
        "-m",
        "pytest",
        "-q",
        "tests/test_mcp_tool_loop.py",
        "tests/test_mcp_stream_tool_loop.py",
        "-k",
        "question_response_appends_grounded_sources",
    ]
    citation_completed = subprocess.run(
        citation_cmd,
        cwd=str(Path(__file__).resolve().parents[1] / "backend"),
        capture_output=True,
        text=True,
        check=False,
    )
    scenario["citation_regression"] = {
        "command": " ".join(citation_cmd),
        "returncode": citation_completed.returncode,
        "stdout_tail": citation_completed.stdout[-2000:],
        "stderr_tail": citation_completed.stderr[-1000:],
    }
    _assert(summary, "o3_citation_regression_pass", citation_completed.returncode == 0, citation_completed.stdout[-800:])

    return scenario


def _serialize_summary(summary: ProbeSummary) -> Dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "base_url": summary.base_url,
        "provider": summary.provider,
        "model": summary.model,
        "reset_applied": summary.reset_applied,
        "assertions": [
            {
                "name": assertion.name,
                "passed": assertion.passed,
                "detail": assertion.detail,
            }
            for assertion in summary.assertions
        ],
        "scenarios": summary.scenarios,
        "success": summary.success,
        "error": summary.error,
    }


def _parse_args(argv: Optional[List[str]] = None) -> Config:
    parser = argparse.ArgumentParser(description="Live Process O.1/O.3 harness")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_servers_settings")
    parser.add_argument("--server-id", default="ollama_default_server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--openrouter-model", default="openai/gpt-4o-mini")
    parser.add_argument("--openai-model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-o13")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=0.8)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--soak-iterations", type=int, default=16)
    parser.add_argument("--reset-from-template", action="store_true")
    parser.add_argument("--skip-o1", action="store_true")
    parser.add_argument("--skip-o3", action="store_true")
    parser.add_argument(
        "--library-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library",
    )
    parser.add_argument(
        "--template-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library_templates/Base_Library",
    )
    args = parser.parse_args(argv)
    return Config(
        base_url=str(args.base_url),
        email=str(args.email),
        password=str(args.password),
        provider=str(args.provider),
        settings_id=str(args.settings_id),
        server_id=str(args.server_id),
        model=str(args.model),
        openrouter_model=str(args.openrouter_model),
        openai_model=str(args.openai_model),
        output_dir=Path(args.output_dir).resolve(),
        timeout_seconds=max(10, int(args.timeout_seconds)),
        request_delay_seconds=max(0.0, float(args.request_delay_seconds)),
        http_max_retries=max(0, int(args.http_max_retries)),
        http_retry_base_seconds=max(0.1, float(args.http_retry_base_seconds)),
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root).resolve(),
        template_root=Path(args.template_root).resolve(),
        soak_iterations=max(12, int(args.soak_iterations)),
        skip_o1=bool(args.skip_o1),
        skip_o3=bool(args.skip_o3),
    )


def main(argv: Optional[List[str]] = None) -> int:
    config = _parse_args(argv)
    if config.skip_o1 and config.skip_o3:
        raise SystemExit("At least one of O.1 or O.3 must run (remove one skip flag).")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    run_dir = config.output_dir / f"run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = ProbeSummary(
        run_id=run_id,
        started_at=started_at,
        base_url=config.base_url,
        provider=config.provider,
        model=config.model,
        reset_applied=False,
    )

    try:
        token, user_id = _login(config)
        summary.scenarios["auth"] = {"user_id": user_id}

        if config.reset_from_template:
            bootstrap = _reset_scope_from_template(config, user_id)
            summary.reset_applied = True
            summary.scenarios["bootstrap"] = bootstrap

        if config.skip_o1:
            summary.scenarios["process_o1"] = {"skipped": True}
        else:
            summary.scenarios["process_o1"] = _run_o1(summary, config, token, user_id)

        if config.skip_o3:
            summary.scenarios["process_o3"] = {"skipped": True}
        else:
            summary.scenarios["process_o3"] = _run_o3(summary, config, token, user_id)

        summary.success = all(assertion.passed for assertion in summary.assertions)
    except Exception as exc:
        summary.success = False
        summary.error = str(exc)

    output_path = run_dir / "summary.json"
    output_path.write_text(
        json.dumps(_serialize_summary(summary), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(_serialize_summary(summary), ensure_ascii=True, indent=2))
    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
