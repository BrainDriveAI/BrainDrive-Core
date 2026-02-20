#!/usr/bin/env python3
"""Live probe harness for Process N.1, N.2, and N.3.

N.1: VS-9/VS-17 deterministic onboarding parity for remaining life topics +
      owner-profile edge-path approval flows (missing/oversized/corrupt).
N.2: TR-3/TR-5/VS-4 configured non-ollama runtime matrix with routing/citation evidence.
N.3: VS-12/TR-4 extended mixed-traffic soak with multi-channel delivery endpoints.
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


APPROVAL_REQUIRED_TEXT = (
    "Approval required before executing mutating tool call. "
    "Reply `approve` to continue or `reject` to cancel."
)


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
    alt_model: str
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path
    soak_iterations: int


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
    action: str = "approve",
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

            record = {
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items()},
                "payload": payload,
            }
            records.append(record)

            if self.path.startswith("/fail"):
                response = {"error": "simulated_unavailable"}
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            ack_id = f"ack-{len(records)}"
            response = {"ack_id": ack_id, "status": "accepted"}
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


def _upsert_setting_instance(
    *,
    config: Config,
    token: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    return _http_json(
        method="POST",
        url=f"{config.base_url.rstrip('/')}/api/v1/settings/instances",
        payload=payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )


def _run_n1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    onboarding_topics: List[Tuple[str, str, str]] = [
        ("relationships", "Relationships", "Start my relationships onboarding interview."),
        ("career", "Career", "Start my career onboarding interview."),
        ("whyfinder", "WhyFinder", "Start my whyfinder onboarding interview."),
    ]
    topic_results: List[Dict[str, Any]] = []

    for topic_slug, topic_title, start_message in onboarding_topics:
        start_payload = _build_chat_payload(
            provider=config.provider,
            settings_id=config.settings_id,
            server_id=config.server_id,
            model=config.model,
            user_id=user_id,
            message=start_message,
            conversation_type=f"life-{topic_slug}",
            mcp_scope_mode="project",
            mcp_project_slug=f"life/{topic_slug}",
            mcp_project_name=topic_title,
            params_extra={"mcp_max_tool_iterations": 6},
        )
        start_status, start_response = _chat(config, token, start_payload)
        start_text = str(((start_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        start_state = start_response.get("tooling_state") or {}
        conversation_id = str(start_response.get("conversation_id") or "")

        _assert(
            summary,
            f"n1_{topic_slug}_kickoff_status",
            start_status == 200,
            str(start_response),
        )
        _assert(
            summary,
            f"n1_{topic_slug}_kickoff_question",
            "question 1 of" in start_text.lower(),
            start_text,
        )
        _assert(
            summary,
            f"n1_{topic_slug}_kickoff_scope",
            start_state.get("conversation_orchestration") == "life_onboarding_deterministic"
            and str(start_state.get("mcp_project_slug") or "").strip() in {topic_slug, f"life/{topic_slug}"},
            str(start_state),
        )
        _assert(
            summary,
            f"n1_{topic_slug}_conversation_id",
            bool(conversation_id),
            str(start_response),
        )

        answer_payload = _build_chat_payload(
            provider=config.provider,
            settings_id=config.settings_id,
            server_id=config.server_id,
            model=config.model,
            user_id=user_id,
            message=f"My focus is improving {topic_slug} consistency this quarter.",
            conversation_type=f"life-{topic_slug}",
            mcp_scope_mode="project",
            mcp_project_slug=f"life/{topic_slug}",
            mcp_project_name=topic_title,
            params_extra={"mcp_max_tool_iterations": 6},
            conversation_id=conversation_id,
        )
        answer_status, answer_response = _chat(config, token, answer_payload)
        answer_text = str(((answer_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        _assert(
            summary,
            f"n1_{topic_slug}_answer_status",
            answer_status == 200,
            str(answer_response),
        )
        _assert(
            summary,
            f"n1_{topic_slug}_answer_pending_approval",
            "approve" in answer_text.lower(),
            answer_text,
        )

        approve_payload = _build_chat_payload(
            provider=config.provider,
            settings_id=config.settings_id,
            server_id=config.server_id,
            model=config.model,
            user_id=user_id,
            message="approve",
            conversation_type=f"life-{topic_slug}",
            mcp_scope_mode="project",
            mcp_project_slug=f"life/{topic_slug}",
            mcp_project_name=topic_title,
            params_extra={"mcp_max_tool_iterations": 6},
            conversation_id=conversation_id,
        )
        approve_status, approve_response = _chat(config, token, approve_payload)
        approve_text = str(((approve_response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        _assert(
            summary,
            f"n1_{topic_slug}_approve_status",
            approve_status == 200,
            str(approve_response),
        )
        _assert(
            summary,
            f"n1_{topic_slug}_approve_progresses",
            any(
                token_text in approve_text.lower()
                for token_text in ("question 2 of", "initial goals or tasks", "onboarding is complete")
            ),
            approve_text,
        )

        topic_results.append(
            {
                "topic": topic_slug,
                "start_text": start_text,
                "answer_text": answer_text,
                "approve_text": approve_text,
                "tooling_state": start_state,
            }
        )

    scenario["onboarding_topics"] = topic_results

    normalized_user_id = _normalize_user_id(user_id)
    profile_path = config.library_root / "users" / normalized_user_id / "me" / "profile.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    scenario["profile_path"] = str(profile_path)

    # Missing path recovery with approval chain.
    if profile_path.exists():
        profile_path.unlink()
    missing_payload = _build_chat_payload(
        provider=config.provider,
        settings_id=config.settings_id,
        server_id=config.server_id,
        model=config.model,
        user_id=user_id,
        message="Update my profile with I do weekly planning every Sunday evening.",
        conversation_type="capture",
        mcp_scope_mode="none",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    missing_status, missing_start = _chat(config, token, missing_payload)
    missing_request = missing_start.get("approval_request") or {}
    missing_conversation_id = str(missing_start.get("conversation_id") or "")
    _assert(summary, "n1_profile_missing_status", missing_status == 200, str(missing_start))
    _assert(
        summary,
        "n1_profile_missing_first_approval",
        missing_start.get("approval_required") is True
        and str((missing_request.get("arguments") or {}).get("path") or "").strip() == "me/profile.md",
        str(missing_start),
    )
    _assert(
        summary,
        "n1_profile_missing_request_id",
        bool(str(missing_request.get("request_id") or "").strip()),
        str(missing_request),
    )

    followups: List[Dict[str, Any]] = []
    current = missing_start
    guard = 0
    while current.get("approval_required") is True and guard < 8:
        request = current.get("approval_request") or {}
        request_id = str(request.get("request_id") or "").strip()
        _assert(
            summary,
            f"n1_profile_missing_followup_request_id_{guard + 1}",
            bool(request_id),
            str(request),
        )
        resume_status, current = _approve_pending_request(
            config=config,
            token=token,
            base_payload=missing_payload,
            conversation_id=missing_conversation_id,
            request_id=request_id,
            action="approve",
        )
        _assert(
            summary,
            f"n1_profile_missing_followup_status_{guard + 1}",
            resume_status == 200,
            str(current),
        )
        _assert(
            summary,
            f"n1_profile_missing_followup_resolution_{guard + 1}",
            (current.get("approval_resolution") or {}).get("status") == "approved",
            str(current.get("approval_resolution")),
        )
        followups.append(
            {
                "step": guard + 1,
                "approval_request": request,
                "approval_resolution": current.get("approval_resolution"),
                "approval_required_next": current.get("approval_required"),
            }
        )
        guard += 1

    scenario["profile_missing_followup_chain"] = followups
    _assert(
        summary,
        "n1_profile_missing_recreated",
        profile_path.is_file(),
        str(profile_path),
    )

    # Oversized context path should mark truncation and still surface approval.
    profile_path.write_text("line\n" * 5000, encoding="utf-8")
    oversized_payload = _build_chat_payload(
        provider=config.provider,
        settings_id=config.settings_id,
        server_id=config.server_id,
        model=config.model,
        user_id=user_id,
        message="Update my profile with I prefer concise planning notes and short check-ins.",
        conversation_type="capture",
        mcp_scope_mode="none",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    oversized_status, oversized_response = _chat(config, token, oversized_payload)
    oversized_state = oversized_response.get("tooling_state") or {}
    oversized_request = oversized_response.get("approval_request") or {}
    scenario["profile_oversized"] = {
        "tooling_state": oversized_state,
        "approval_request": oversized_request,
    }
    _assert(summary, "n1_profile_oversized_status", oversized_status == 200, str(oversized_response))
    _assert(
        summary,
        "n1_profile_oversized_metadata",
        oversized_state.get("owner_profile_status") == "loaded"
        and bool(oversized_state.get("owner_profile_truncated")),
        str(oversized_state),
    )
    _assert(
        summary,
        "n1_profile_oversized_approval",
        oversized_response.get("approval_required") is True
        and str((oversized_request.get("arguments") or {}).get("path") or "").strip() == "me/profile.md",
        str(oversized_response),
    )

    # Corrupt context path should show read_error but still allow approval flow.
    profile_path.write_bytes(b"\xff\xfe\xfd")
    corrupt_payload = _build_chat_payload(
        provider=config.provider,
        settings_id=config.settings_id,
        server_id=config.server_id,
        model=config.model,
        user_id=user_id,
        message="Update my profile with I do weekly planning on Sunday nights.",
        conversation_type="capture",
        mcp_scope_mode="none",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    corrupt_status, corrupt_start = _chat(config, token, corrupt_payload)
    corrupt_state = corrupt_start.get("tooling_state") or {}
    corrupt_request = corrupt_start.get("approval_request") or {}
    scenario["profile_corrupt_start"] = {
        "tooling_state": corrupt_state,
        "approval_request": corrupt_request,
    }
    _assert(summary, "n1_profile_corrupt_status", corrupt_status == 200, str(corrupt_start))
    _assert(
        summary,
        "n1_profile_corrupt_metadata",
        corrupt_state.get("owner_profile_status") == "read_error",
        str(corrupt_state),
    )
    _assert(
        summary,
        "n1_profile_corrupt_approval",
        corrupt_start.get("approval_required") is True
        and str((corrupt_request.get("arguments") or {}).get("path") or "").strip() == "me/profile.md",
        str(corrupt_start),
    )
    corrupt_request_id = str(corrupt_request.get("request_id") or "").strip()
    _assert(summary, "n1_profile_corrupt_request_id", bool(corrupt_request_id), str(corrupt_request))
    corrupt_resume_status, corrupt_resume = _approve_pending_request(
        config=config,
        token=token,
        base_payload=corrupt_payload,
        conversation_id=str(corrupt_start.get("conversation_id") or ""),
        request_id=corrupt_request_id,
        action="approve",
    )
    scenario["profile_corrupt_resume"] = corrupt_resume
    _assert(summary, "n1_profile_corrupt_resume_status", corrupt_resume_status == 200, str(corrupt_resume))
    _assert(
        summary,
        "n1_profile_corrupt_resolution",
        (corrupt_resume.get("approval_resolution") or {}).get("status") == "approved",
        str(corrupt_resume.get("approval_resolution")),
    )

    return scenario


def _run_n2(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    openai_settings_payload = {
        "definition_id": "openai_api_keys_settings",
        "name": "OpenAI API Keys",
        "value": {
            "api_key": "sk-local-test-key",
            "base_url": "http://localhost:11434/v1",
        },
        "scope": "user",
        "user_id": user_id,
    }
    settings_status, settings_response = _upsert_setting_instance(
        config=config,
        token=token,
        payload=openai_settings_payload,
    )
    scenario["openai_settings_upsert"] = {
        "status": settings_status,
        "response": settings_response,
    }
    _assert(
        summary,
        "n2_openai_settings_upsert",
        settings_status in {200, 201},
        str(settings_response),
    )

    catalog_status, catalog = _http_json(
        method="GET",
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/catalog",
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )
    providers = catalog.get("providers") if isinstance(catalog, dict) else []
    scenario["provider_catalog"] = {"status": catalog_status, "providers": providers}
    _assert(summary, "n2_catalog_status_200", catalog_status == 200, str(catalog))
    openai_entry = None
    for entry in providers if isinstance(providers, list) else []:
        if str(entry.get("id") or "").strip() == "openai":
            openai_entry = entry
            break
    _assert(summary, "n2_openai_in_catalog", isinstance(openai_entry, dict), str(providers))
    _assert(
        summary,
        "n2_openai_marked_configured",
        bool((openai_entry or {}).get("configured")),
        str(openai_entry),
    )

    model_candidates = [config.model]
    alt = str(config.alt_model or "").strip()
    if alt and alt not in model_candidates:
        model_candidates.append(alt)

    matrix: List[Dict[str, Any]] = []
    any_non_ollama_pass = False
    any_citation_acceptance = False
    n2_scope_slug = "life/finances"
    n2_scope_name = "Finances"
    for model in model_candidates:
        routing_status, routing_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider="openai",
                settings_id="openai_api_keys_settings",
                server_id="openai_default_server",
                model=model,
                user_id=user_id,
                message="List one markdown path in life/finances before answering.",
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug=n2_scope_slug,
                mcp_project_name=n2_scope_name,
                params_extra={
                    "mcp_max_tool_iterations": 6,
                    "mcp_tool_profile": "read_only",
                },
            ),
        )
        routing_state = routing_response.get("tooling_state") if isinstance(routing_response, dict) else {}
        routing_ok = (
            routing_status == 200
            and isinstance(routing_state, dict)
            and str(routing_state.get("tool_routing_mode") or "").strip() in {"dual_path_fallback", "single_path_native"}
            and str(routing_state.get("tool_execution_mode") or "").strip()
            in {"single_path_compat", "single_path_native"}
        )

        citation_status, citation_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider="openai",
                settings_id="openai_api_keys_settings",
                server_id="openai_default_server",
                model=model,
                user_id=user_id,
                message="Which markdown file in life/finances mentions '(to be populated during onboarding)'? cite the path.",
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug=n2_scope_slug,
                mcp_project_name=n2_scope_name,
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
                or (isinstance(citations, list) and len(citations) > 0)
                or ".md" in citation_text
            )
        )

        model_result = {
            "model": model,
            "routing_probe": {
                "status": routing_status,
                "ok": routing_ok,
                "tooling_state": routing_state,
            },
            "citation_probe": {
                "status": citation_status,
                "ok": citation_ok,
                "response_citations": citations,
                "has_sources_block": "sources:" in citation_text.lower(),
                "text_excerpt": citation_text[:400],
            },
        }
        matrix.append(model_result)
        if routing_ok:
            any_non_ollama_pass = True
        if citation_ok:
            any_citation_acceptance = True

    scenario["openai_runtime_matrix"] = matrix
    _assert(summary, "n2_openai_runtime_has_pass", any_non_ollama_pass, str(matrix))
    _assert(summary, "n2_openai_citation_acceptance", any_citation_acceptance, str(matrix))
    return scenario


def _run_n3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"iterations": config.soak_iterations}
    webhook = _start_local_webhook_server()
    scenario["webhook_base_url"] = webhook.base_url

    digest_email_runs: List[Dict[str, Any]] = []
    digest_slack_runs: List[Dict[str, Any]] = []
    pre_compaction_runs: List[Dict[str, Any]] = []
    capture_runs: List[Dict[str, Any]] = []

    digest_email_conversation_id: Optional[str] = None
    digest_slack_conversation_id: Optional[str] = None
    pre_conversation_id: Optional[str] = None

    try:
        for index in range(config.soak_iterations):
            pair_id = index // 2
            email_event_id = f"n3-email-{pair_id}"
            slack_event_id = f"n3-slack-{pair_id}"
            pre_event_id = f"n3-pre-{pair_id}"

            email_status, email_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=config.provider,
                    settings_id=config.settings_id,
                    server_id=config.server_id,
                    model=config.model,
                    user_id=user_id,
                    message="Run scheduled digest delivery now.",
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
            email_state = email_response.get("tooling_state") or {}
            digest_email_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": email_event_id,
                    "status_code": email_status,
                    "digest_schedule_status": email_state.get("digest_schedule_status"),
                    "digest_schedule_duplicate_guard": email_state.get("digest_schedule_duplicate_guard"),
                    "delivery_send_status": email_state.get("digest_delivery_send_status"),
                    "delivery_channel": email_state.get("digest_delivery_channel"),
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
                    message="Run scheduled digest delivery now.",
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
            slack_state = slack_response.get("tooling_state") or {}
            digest_slack_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": slack_event_id,
                    "status_code": slack_status,
                    "digest_schedule_status": slack_state.get("digest_schedule_status"),
                    "digest_schedule_duplicate_guard": slack_state.get("digest_schedule_duplicate_guard"),
                    "delivery_send_status": slack_state.get("digest_delivery_send_status"),
                    "delivery_channel": slack_state.get("digest_delivery_channel"),
                }
            )

            long_message = " ".join(["context-pressure"] * 240)
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
                    conversation_id=pre_conversation_id,
                    params_extra={
                        "mcp_tools_enabled": False,
                        "mcp_pre_compaction_flush_enabled": True,
                        "mcp_context_window_tokens": 64,
                        "mcp_pre_compaction_flush_threshold": 0.5,
                        "mcp_pre_compaction_event_id": pre_event_id,
                    },
                ),
            )
            if pre_conversation_id is None:
                pre_conversation_id = str(pre_response.get("conversation_id") or "")
            pre_state = pre_response.get("tooling_state") or {}
            pre_compaction_runs.append(
                {
                    "iteration": index + 1,
                    "event_id": pre_event_id,
                    "status_code": pre_status,
                    "pre_compaction_flush_status": pre_state.get("pre_compaction_flush_status"),
                    "pre_compaction_flush_duplicate_guard": pre_state.get("pre_compaction_flush_duplicate_guard"),
                }
            )

            capture_status, capture_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=config.provider,
                    settings_id=config.settings_id,
                    server_id=config.server_id,
                    model=config.model,
                    user_id=user_id,
                    message=f"Create a task to reconcile finance notes for iteration {index + 1}.",
                    conversation_type="capture",
                    mcp_scope_mode="project",
                    mcp_project_slug="projects/active/finance",
                    mcp_project_name="Finance",
                    params_extra={
                        "mcp_max_tool_iterations": 6,
                    },
                ),
            )
            capture_entry: Dict[str, Any] = {
                "iteration": index + 1,
                "status_code": capture_status,
                "approval_required": capture_response.get("approval_required"),
                "tooling_state": capture_response.get("tooling_state"),
            }
            if capture_response.get("approval_required") is True:
                approval_request = capture_response.get("approval_request") or {}
                action = "approve" if index % 2 == 0 else "reject"
                resume_status, resume_response = _approve_pending_request(
                    config=config,
                    token=token,
                    base_payload=_build_chat_payload(
                        provider=config.provider,
                        settings_id=config.settings_id,
                        server_id=config.server_id,
                        model=config.model,
                        user_id=user_id,
                        message="approve",
                        conversation_type="capture",
                        mcp_scope_mode="project",
                        mcp_project_slug="projects/active/finance",
                        mcp_project_name="Finance",
                        params_extra={"mcp_max_tool_iterations": 6},
                    ),
                    conversation_id=str(capture_response.get("conversation_id") or ""),
                    request_id=str(approval_request.get("request_id") or ""),
                    action=action,
                )
                capture_entry["approval_action"] = action
                capture_entry["approval_resume_status_code"] = resume_status
                capture_entry["approval_resolution"] = resume_response.get("approval_resolution")
            capture_runs.append(capture_entry)

        scenario["digest_email_runs"] = digest_email_runs
        scenario["digest_slack_runs"] = digest_slack_runs
        scenario["pre_compaction_runs"] = pre_compaction_runs
        scenario["capture_runs"] = capture_runs

        email_duplicate_guard_count = sum(
            1 for item in digest_email_runs if str(item.get("digest_schedule_status") or "") == "duplicate_guard"
        )
        slack_duplicate_guard_count = sum(
            1 for item in digest_slack_runs if str(item.get("digest_schedule_status") or "") == "duplicate_guard"
        )
        pre_duplicate_guard_count = sum(
            1 for item in pre_compaction_runs if str(item.get("pre_compaction_flush_status") or "") == "duplicate_guard"
        )
        capture_resolved_count = sum(
            1
            for item in capture_runs
            if str((item.get("approval_resolution") or {}).get("status") or "").strip() in {"approved", "rejected"}
        )
        email_send_count = sum(1 for item in digest_email_runs if str(item.get("delivery_send_status") or "") == "sent")
        slack_send_count = sum(1 for item in digest_slack_runs if str(item.get("delivery_send_status") or "") == "sent")
        email_webhook_hits = sum(1 for rec in webhook.records if str(rec.get("path") or "").startswith("/email"))
        slack_webhook_hits = sum(1 for rec in webhook.records if str(rec.get("path") or "").startswith("/slack"))

        scenario["aggregate"] = {
            "email_duplicate_guard_count": email_duplicate_guard_count,
            "slack_duplicate_guard_count": slack_duplicate_guard_count,
            "pre_compaction_duplicate_guard_count": pre_duplicate_guard_count,
            "capture_resolved_count": capture_resolved_count,
            "email_send_count": email_send_count,
            "slack_send_count": slack_send_count,
            "email_webhook_hits": email_webhook_hits,
            "slack_webhook_hits": slack_webhook_hits,
        }

        _assert(summary, "n3_email_duplicate_guard_seen", email_duplicate_guard_count >= 1, str(digest_email_runs))
        _assert(summary, "n3_slack_duplicate_guard_seen", slack_duplicate_guard_count >= 1, str(digest_slack_runs))
        _assert(summary, "n3_pre_compaction_duplicate_guard_seen", pre_duplicate_guard_count >= 1, str(pre_compaction_runs))
        _assert(summary, "n3_capture_resolution_seen", capture_resolved_count >= 1, str(capture_runs))
        _assert(summary, "n3_email_delivery_sent_seen", email_send_count >= 1, str(digest_email_runs))
        _assert(summary, "n3_slack_delivery_sent_seen", slack_send_count >= 1, str(digest_slack_runs))
        _assert(summary, "n3_email_webhook_hits_seen", email_webhook_hits >= 1, str(scenario["aggregate"]))
        _assert(summary, "n3_slack_webhook_hits_seen", slack_webhook_hits >= 1, str(scenario["aggregate"]))

        return scenario
    finally:
        webhook.shutdown()


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
    parser = argparse.ArgumentParser(description="Live Process N.1/N.2/N.3 harness")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_servers_settings")
    parser.add_argument("--server-id", default="ollama_default_server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--alt-model", default="hf.co/mradermacher/granite-4.0-micro-GGUF:Q8_0")
    parser.add_argument("--output-dir", default="tmp/live-process-n123")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--request-delay-seconds", type=float, default=0.9)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=1.6)
    parser.add_argument("--soak-iterations", type=int, default=10)
    parser.add_argument("--reset-from-template", action="store_true")
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
        alt_model=str(args.alt_model),
        output_dir=Path(args.output_dir).resolve(),
        timeout_seconds=max(10, int(args.timeout_seconds)),
        request_delay_seconds=max(0.0, float(args.request_delay_seconds)),
        http_max_retries=max(0, int(args.http_max_retries)),
        http_retry_base_seconds=max(0.1, float(args.http_retry_base_seconds)),
        soak_iterations=max(4, int(args.soak_iterations)),
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root).resolve(),
        template_root=Path(args.template_root).resolve(),
    )


def main(argv: Optional[List[str]] = None) -> int:
    config = _parse_args(argv)
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

        summary.scenarios["process_n1"] = _run_n1(summary, config, token, user_id)
        summary.scenarios["process_n2"] = _run_n2(summary, config, token, user_id)
        summary.scenarios["process_n3"] = _run_n3(summary, config, token, user_id)

        summary.success = all(assertion.passed for assertion in summary.assertions)
    except Exception as exc:
        summary.success = False
        summary.error = str(exc)

    output_path = run_dir / "summary.json"
    output_path.write_text(json.dumps(_serialize_summary(summary), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(_serialize_summary(summary), ensure_ascii=True, indent=2))
    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
