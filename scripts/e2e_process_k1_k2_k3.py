#!/usr/bin/env python3
"""Live probe harness for Process K.1, K.2, and K.3.

K.1: VS-12/TR-4 outbound delivery sender wiring + ack/failure paths + soak.
K.2: VS-9/VS-17 deterministic non-finance onboarding + profile update/error loops.
K.3: VS-4/TR-3/TR-5 citation acceptance + routing capability matrix expansion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
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
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path


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


def _http_post(
    *,
    url: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
    request_delay_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    global _LAST_REQUEST_SENT_MONOTONIC

    raw = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempt = 0
    while True:
        request_obj = urllib_request.Request(url=url, data=raw, headers=headers, method="POST")
        try:
            if request_delay_seconds > 0:
                now = time.monotonic()
                if _LAST_REQUEST_SENT_MONOTONIC is not None:
                    elapsed = now - _LAST_REQUEST_SENT_MONOTONIC
                    if elapsed < request_delay_seconds:
                        time.sleep(request_delay_seconds - elapsed)
                _LAST_REQUEST_SENT_MONOTONIC = time.monotonic()

            with urllib_request.urlopen(request_obj, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
                if not body.strip():
                    return {}
                return json.loads(body)
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            raise RuntimeError(f"HTTP {exc.code} for {url}: {details}") from exc
        except urllib_error.URLError as exc:
            if attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _login(config: Config) -> Tuple[str, str]:
    response = _http_post(
        url=f"{config.base_url.rstrip('/')}/api/v1/auth/login",
        payload={"email": config.email, "password": config.password},
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=max(config.request_delay_seconds, 1.0),
        max_retries=max(config.http_max_retries, 6),
        retry_base_seconds=max(config.http_retry_base_seconds, 2.5),
    )
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


def _seed_finances_onboarding_complete(config: Config, user_id: str) -> Dict[str, Any]:
    normalized_user_id = _normalize_user_id(user_id)
    onboarding_path = (
        config.library_root
        / "users"
        / normalized_user_id
        / ".braindrive"
        / "onboarding_state.json"
    )
    if not onboarding_path.is_file():
        return {"updated": False, "path": str(onboarding_path), "reason": "missing"}

    try:
        state = json.loads(onboarding_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "updated": False,
            "path": str(onboarding_path),
            "reason": f"read_error:{exc}",
        }
    if not isinstance(state, dict):
        return {"updated": False, "path": str(onboarding_path), "reason": "invalid_json_root"}

    now_iso = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    starter_topics = state.setdefault("starter_topics", {})
    if isinstance(starter_topics, dict):
        starter_topics["finances"] = "complete"
    completed_at = state.setdefault("completed_at", {})
    if isinstance(completed_at, dict):
        completed_at["finances"] = now_iso
    topic_progress = state.setdefault("topic_progress", {})
    if isinstance(topic_progress, dict):
        finances_progress = topic_progress.setdefault("finances", {})
        if isinstance(finances_progress, dict):
            finances_progress["status"] = "complete"
            finances_progress["phase"] = "complete"
            finances_progress["completed_at_utc"] = now_iso
            finances_progress["last_updated_at_utc"] = now_iso
            if not finances_progress.get("started_at_utc"):
                finances_progress["started_at_utc"] = now_iso
    state["active_topic"] = None
    state["updated_at_utc"] = now_iso
    onboarding_path.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {"updated": True, "path": str(onboarding_path), "updated_at_utc": now_iso}


def _build_chat_payload(
    *,
    config: Config,
    user_id: str,
    message: str,
    conversation_type: str,
    mcp_scope_mode: str,
    mcp_project_slug: Optional[str] = None,
    mcp_project_name: Optional[str] = None,
    params_extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    page_id: Optional[str] = None,
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
        "provider": config.provider,
        "settings_id": config.settings_id,
        "server_id": config.server_id,
        "model": config.model,
        "messages": [{"role": "user", "content": message}],
        "user_id": user_id,
        "conversation_type": conversation_type,
        "params": params,
        "stream": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if page_id:
        payload["page_id"] = page_id
    return payload


def _chat(config: Config, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _http_post(
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/chat",
        payload=payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )


def _start_local_webhook_server() -> LocalWebhookServer:
    records: List[Dict[str, Any]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - stdlib hook name
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
            payload: Any
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
                body = {"error": "simulated_unavailable"}
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return

            ack_id = f"ack-{len(records)}"
            body = {"ack_id": ack_id, "status": "accepted"}
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return LocalWebhookServer(
        server=server,
        thread=thread,
        records=records,
        base_url=base_url,
    )


def _run_k1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    webhook = _start_local_webhook_server()
    scenario["webhook_base_url"] = webhook.base_url

    try:
        success_event_id = f"k1-success-{summary.run_id}"
        success = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message="Run scheduled digest delivery now.",
                conversation_type="digest-email",
                mcp_scope_mode="none",
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_tool_profile": "read_only",
                    "mcp_digest_schedule_enabled": True,
                    "mcp_digest_force_run": True,
                    "mcp_digest_schedule_event_id": success_event_id,
                    "mcp_digest_delivery_send_enabled": True,
                    "mcp_digest_delivery_endpoint": f"{webhook.base_url}/ok",
                },
            ),
        )
        success_handoff = success.get("delivery_handoff") or {}
        success_state = success.get("tooling_state") or {}
        success_conversation_id = success.get("conversation_id")
        scenario["digest_delivery_success"] = {
            "delivery_handoff": success_handoff,
            "tooling_state": success_state,
            "conversation_id": success_conversation_id,
        }
        _assert(
            summary,
            "k1_send_success_handoff_status",
            success_handoff.get("delivery_send_status") == "sent"
            and success_handoff.get("delivery_send_http_status") == 202
            and bool(str(success_handoff.get("delivery_send_ack_id") or "")),
            str(success_handoff),
        )
        _assert(
            summary,
            "k1_send_success_persisted",
            success_handoff.get("delivery_record_status") == "persisted"
            and bool(str(success_handoff.get("delivery_record_path") or "")),
            str(success_handoff),
        )
        _assert(
            summary,
            "k1_send_success_tooling_metadata",
            success_state.get("digest_delivery_send_status") == "sent"
            and success_state.get("digest_delivery_send_http_status") == 202,
            str(success_state),
        )

        failure_event_id = f"k1-failure-{summary.run_id}"
        failure = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message="Run scheduled digest delivery now.",
                conversation_type="digest-email",
                mcp_scope_mode="none",
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_tool_profile": "read_only",
                    "mcp_digest_schedule_enabled": True,
                    "mcp_digest_force_run": True,
                    "mcp_digest_schedule_event_id": failure_event_id,
                    "mcp_digest_delivery_send_enabled": True,
                    "mcp_digest_delivery_endpoint": f"{webhook.base_url}/fail",
                },
            ),
        )
        failure_handoff = failure.get("delivery_handoff") or {}
        failure_state = failure.get("tooling_state") or {}
        scenario["digest_delivery_failure"] = {
            "delivery_handoff": failure_handoff,
            "tooling_state": failure_state,
        }
        _assert(
            summary,
            "k1_send_failure_handoff_status",
            failure_handoff.get("delivery_send_status") == "http_error"
            and int(failure_handoff.get("delivery_send_http_status") or 0) == 503,
            str(failure_handoff),
        )
        _assert(
            summary,
            "k1_send_failure_metadata",
            failure_state.get("digest_delivery_send_status") == "http_error"
            and int(failure_state.get("digest_delivery_send_http_status") or 0) == 503,
            str(failure_state),
        )

        soak_checks: List[Dict[str, Any]] = []
        for index in range(12):
            event_id = f"k1-soak-{summary.run_id}-{index + 1}"
            soak = _chat(
                config,
                token,
                _build_chat_payload(
                    config=config,
                    user_id=user_id,
                    message=f"Run digest delivery soak pass {index + 1}.",
                    conversation_type="digest-email",
                    mcp_scope_mode="none",
                    conversation_id=success_conversation_id,
                    params_extra={
                        "mcp_tools_enabled": False,
                        "mcp_tool_profile": "read_only",
                        "mcp_digest_schedule_enabled": True,
                        "mcp_digest_force_run": True,
                        "mcp_digest_schedule_event_id": event_id,
                        "mcp_digest_delivery_send_enabled": True,
                        "mcp_digest_delivery_endpoint": f"{webhook.base_url}/ok",
                    },
                ),
            )
            soak_handoff = soak.get("delivery_handoff") or {}
            soak_state = soak.get("tooling_state") or {}
            soak_checks.append(
                {
                    "event_id": event_id,
                    "send_status": soak_handoff.get("delivery_send_status"),
                    "record_status": soak_handoff.get("delivery_record_status"),
                    "digest_status": soak_state.get("digest_schedule_status"),
                }
            )
            _assert(
                summary,
                f"k1_soak_send_status_{index + 1}",
                soak_handoff.get("delivery_send_status") == "sent",
                str(soak_handoff),
            )
            _assert(
                summary,
                f"k1_soak_record_status_{index + 1}",
                soak_handoff.get("delivery_record_status") == "persisted",
                str(soak_handoff),
            )
        scenario["digest_delivery_soak"] = soak_checks
        _assert(
            summary,
            "k1_webhook_records_seen",
            len(webhook.records) >= 14,
            f"records={len(webhook.records)}",
        )

        preflush_event_id = f"k1-preflush-{summary.run_id}"
        long_message = " ".join(["context-pressure"] * 220)
        preflush_start = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=long_message,
                conversation_type="chat",
                mcp_scope_mode="none",
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_pre_compaction_flush_enabled": True,
                    "mcp_context_window_tokens": 64,
                    "mcp_pre_compaction_flush_threshold": 0.5,
                    "mcp_pre_compaction_event_id": preflush_event_id,
                },
            ),
        )
        preflush_conversation_id = preflush_start.get("conversation_id")
        preflush_repeat = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=long_message,
                conversation_type="chat",
                mcp_scope_mode="none",
                conversation_id=preflush_conversation_id,
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_pre_compaction_flush_enabled": True,
                    "mcp_context_window_tokens": 64,
                    "mcp_pre_compaction_flush_threshold": 0.5,
                    "mcp_pre_compaction_event_id": preflush_event_id,
                },
            ),
        )
        preflush_state = preflush_repeat.get("tooling_state") or {}
        scenario["pre_compaction_duplicate_guard"] = preflush_state
        _assert(
            summary,
            "k1_preflush_duplicate_guard",
            preflush_state.get("pre_compaction_flush_status") == "duplicate_guard"
            and preflush_state.get("pre_compaction_flush_duplicate_guard") == "history_seen",
            str(preflush_state),
        )

        return scenario
    finally:
        webhook.shutdown()


def _approve_pending_request(
    *,
    config: Config,
    token: str,
    base_payload: Dict[str, Any],
    conversation_id: str,
    request_id: str,
) -> Dict[str, Any]:
    payload = dict(base_payload)
    payload["conversation_id"] = conversation_id
    payload["messages"] = [{"role": "user", "content": "approve"}]
    params = dict(payload.get("params") or {})
    params["mcp_approval"] = {"action": "approve", "request_id": request_id}
    payload["params"] = params
    return _chat(config, token, payload)


def _run_k2(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    fitness_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Start my fitness onboarding interview.",
            conversation_type="life-fitness",
            mcp_scope_mode="project",
            mcp_project_slug="life/fitness",
            mcp_project_name="Fitness",
            params_extra={"mcp_max_tool_iterations": 6},
        ),
    )
    fitness_text = str(((fitness_start.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    fitness_state = fitness_start.get("tooling_state") or {}
    scenario["fitness_onboarding_start"] = {
        "conversation_id": fitness_start.get("conversation_id"),
        "text": fitness_text,
        "tooling_state": fitness_state,
    }
    _assert(
        summary,
        "k2_fitness_onboarding_question",
        "question 1 of" in fitness_text.lower() and "fitness" in fitness_text.lower(),
        fitness_text,
    )
    _assert(
        summary,
        "k2_fitness_onboarding_state",
        fitness_state.get("conversation_orchestration") == "life_onboarding_deterministic"
        and fitness_state.get("mcp_project_slug") in {"fitness", "life/fitness"},
        str(fitness_state),
    )

    profile_base_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Update my profile with I prefer concise check-ins and 7am deep work.",
        conversation_type="capture",
        mcp_scope_mode="none",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    profile_start = _chat(config, token, profile_base_payload)
    profile_request = profile_start.get("approval_request") or {}
    profile_conversation_id = str(profile_start.get("conversation_id") or "")
    scenario["profile_update_start"] = {
        "approval_required": profile_start.get("approval_required"),
        "approval_request": profile_request,
        "conversation_id": profile_conversation_id,
    }
    _assert(
        summary,
        "k2_profile_update_first_approval",
        profile_start.get("approval_required") is True
        and profile_request.get("tool") in {"write_markdown", "create_markdown"}
        and str((profile_request.get("arguments") or {}).get("path") or "").strip() == "me/profile.md",
        str(profile_start),
    )
    first_request_id = str(profile_request.get("request_id") or "")
    _assert(summary, "k2_profile_update_request_id", bool(first_request_id), str(profile_request))
    profile_resume = _approve_pending_request(
        config=config,
        token=token,
        base_payload=profile_base_payload,
        conversation_id=profile_conversation_id,
        request_id=first_request_id,
    )
    scenario["profile_update_resume"] = {
        "approval_resolution": profile_resume.get("approval_resolution"),
        "approval_required": profile_resume.get("approval_required"),
        "approval_request": profile_resume.get("approval_request"),
    }
    _assert(
        summary,
        "k2_profile_update_resolution",
        (profile_resume.get("approval_resolution") or {}).get("status") == "approved",
        str(profile_resume.get("approval_resolution")),
    )

    normalized_user_id = _normalize_user_id(user_id)
    profile_path = config.library_root / "users" / normalized_user_id / "me" / "profile.md"
    if profile_path.exists():
        profile_path.unlink()
    scenario["profile_file_deleted_for_error_path"] = str(profile_path)

    missing_base_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Update my profile with I do weekly planning every Sunday.",
        conversation_type="capture",
        mcp_scope_mode="none",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    missing_start = _chat(config, token, missing_base_payload)
    missing_first_request = missing_start.get("approval_request") or {}
    missing_conversation_id = str(missing_start.get("conversation_id") or "")
    _assert(
        summary,
        "k2_profile_missing_first_approval",
        missing_start.get("approval_required") is True
        and str(missing_first_request.get("tool") or "").strip() in {"write_markdown", "create_markdown"}
        and str((missing_first_request.get("arguments") or {}).get("path") or "").strip()
        == "me/profile.md",
        str(missing_start),
    )

    missing_first_resume = _approve_pending_request(
        config=config,
        token=token,
        base_payload=missing_base_payload,
        conversation_id=missing_conversation_id,
        request_id=str(missing_first_request.get("request_id") or ""),
    )
    missing_second_request = missing_first_resume.get("approval_request") or {}
    scenario["profile_missing_resume_1"] = {
        "approval_resolution": missing_first_resume.get("approval_resolution"),
        "approval_required": missing_first_resume.get("approval_required"),
        "approval_request": missing_second_request,
    }
    _assert(
        summary,
        "k2_profile_missing_write_resolution",
        (missing_first_resume.get("approval_resolution") or {}).get("status") == "approved",
        str(missing_first_resume.get("approval_resolution")),
    )
    if missing_first_resume.get("approval_required") is True:
        followup_chain: List[Dict[str, Any]] = []
        saw_create_followup = False
        pending_response = missing_first_resume
        guard = 0
        while pending_response.get("approval_required") is True and guard < 6:
            pending_request = pending_response.get("approval_request") or {}
            pending_tool = str(pending_request.get("tool") or "").strip()
            pending_request_id = str(pending_request.get("request_id") or "").strip()
            pending_args = pending_request.get("arguments") or {}

            if pending_tool == "create_markdown":
                saw_create_followup = True
                _assert(
                    summary,
                    "k2_profile_missing_create_followup_path",
                    str((pending_args.get("path") or "")).strip() == "me/profile.md",
                    str(pending_request),
                )

            _assert(
                summary,
                f"k2_profile_missing_followup_request_id_{guard + 1}",
                bool(pending_request_id),
                str(pending_request),
            )

            next_response = _approve_pending_request(
                config=config,
                token=token,
                base_payload=missing_base_payload,
                conversation_id=missing_conversation_id,
                request_id=pending_request_id,
            )
            followup_chain.append(
                {
                    "step": guard + 1,
                    "request": pending_request,
                    "approval_resolution": next_response.get("approval_resolution"),
                    "approval_required_next": next_response.get("approval_required"),
                }
            )
            _assert(
                summary,
                f"k2_profile_missing_followup_resolution_{guard + 1}",
                (next_response.get("approval_resolution") or {}).get("status") == "approved",
                str(next_response.get("approval_resolution")),
            )
            pending_response = next_response
            guard += 1

        scenario["profile_missing_followup_chain"] = followup_chain
        _assert(
            summary,
            "k2_profile_missing_followup_progress",
            saw_create_followup or profile_path.is_file(),
            str(followup_chain),
        )
        settled_without_pending = pending_response.get("approval_required") is not True
        settled_with_optional_followup = False
        if not settled_without_pending:
            pending_request = pending_response.get("approval_request") or {}
            pending_tool = str(pending_request.get("tool") or "").strip()
            pending_args = pending_request.get("arguments") or {}
            pending_path = str((pending_args.get("path") or "")).strip()
            pending_summary = str(pending_request.get("summary") or "").strip().lower()
            if pending_tool in {"write_markdown", "create_markdown"} and (
                pending_path == "me/profile.md" or "me/profile.md" in pending_summary
            ):
                settled_with_optional_followup = True
                scenario["profile_missing_optional_followup_pending"] = pending_request

        _assert(
            summary,
            "k2_profile_missing_followup_settled",
            settled_without_pending or settled_with_optional_followup,
            str(pending_response),
        )
    else:
        _assert(
            summary,
            "k2_profile_missing_auto_recovered",
            missing_first_resume.get("approval_required") is not True,
            str(missing_first_resume),
        )

    _assert(
        summary,
        "k2_profile_file_recreated",
        profile_path.is_file(),
        str(profile_path),
    )

    return scenario


def _attempt_citation_probe(
    *,
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
    scope_mode: str,
    conversation_type: str,
    project_slug: str,
    project_name: str,
    prompts: List[str],
    params_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for prompt in prompts:
        response = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=prompt,
                conversation_type=conversation_type,
                mcp_scope_mode=scope_mode,
                mcp_project_slug=project_slug,
                mcp_project_name=project_name,
                params_extra=params_extra,
            ),
        )
        tooling = response.get("tooling_state") or {}
        citations = tooling.get("response_citations")
        text = str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        has_sources_block = "Sources:" in text
        has_citation_meta = isinstance(citations, list) and len(citations) > 0
        attempt = {
            "prompt": prompt,
            "tooling_state": tooling,
            "has_sources_block": has_sources_block,
            "has_citation_meta": has_citation_meta,
            "citation_count": len(citations) if isinstance(citations, list) else 0,
        }
        attempts.append(attempt)
        if has_sources_block or has_citation_meta:
            return {
                "accepted": True,
                "attempts": attempts,
            }
    return {
        "accepted": False,
        "attempts": attempts,
    }


def _run_k3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    scenario["onboarding_seed"] = _seed_finances_onboarding_complete(config, user_id)

    routing_default = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Create a task to review project budget next week.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/finance",
            mcp_project_name="Finance",
            params_extra={"mcp_max_tool_iterations": 5},
        ),
    )
    routing_default_state = routing_default.get("tooling_state") or {}
    scenario["routing_default_project_scope"] = routing_default_state
    _assert(
        summary,
        "k3_routing_default_project_scope",
        routing_default_state.get("tool_routing_mode") == "dual_path_fallback"
        and routing_default_state.get("tool_profile") == "full"
        and routing_default_state.get("tool_profile_source") == "routing_scope_policy"
        and routing_default_state.get("tool_policy_mode") == "dual_path_project_scope_compat",
        str(routing_default_state),
    )

    routing_native_override = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Create a task to review project budget next week.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/finance",
            mcp_project_name="Finance",
            params_extra={
                "mcp_max_tool_iterations": 5,
                "mcp_native_tool_calling": True,
            },
        ),
    )
    routing_native_state = routing_native_override.get("tooling_state") or {}
    scenario["routing_native_override"] = routing_native_state
    _assert(
        summary,
        "k3_routing_native_override",
        routing_native_state.get("tool_routing_mode") == "single_path_native"
        and routing_native_state.get("tool_execution_mode") == "single_path_native"
        and routing_native_state.get("routing_capability_source") == "request_override",
        str(routing_native_state),
    )

    routing_life_scope = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Create a task to review my fitness weekly plan.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="life/fitness",
            mcp_project_name="Fitness",
            params_extra={"mcp_max_tool_iterations": 5},
        ),
    )
    routing_life_state = routing_life_scope.get("tooling_state") or {}
    scenario["routing_life_scope"] = routing_life_state
    _assert(
        summary,
        "k3_routing_life_scope_policy",
        routing_life_state.get("tool_policy_mode") == "dual_path_life_scope_compat"
        and routing_life_state.get("tool_profile") == "full",
        str(routing_life_state),
    )

    project_citation_probe = _attempt_citation_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        scope_mode="project",
        conversation_type="chat",
        project_slug="projects/active/finance",
        project_name="Finance",
        prompts=[
            "What does my current budget say right now?",
            "Use library files and tell me what the current budget page says.",
            "Find the current budget details from the finance markdown files.",
            "Read the project finance markdown files and answer with a short Sources: list.",
            "Use read-only library tools and include Sources: with file paths.",
        ],
        params_extra={"mcp_max_tool_iterations": 6, "mcp_tool_profile": "read_only"},
    )
    life_citation_probe = _attempt_citation_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        scope_mode="project",
        conversation_type="chat",
        project_slug="life/finances",
        project_name="Finances",
        prompts=[
            "What should I focus on in finances based on my library notes?",
            "Use life finances markdown context and answer with the top focus areas.",
            "Search my finances pages and summarize key focus areas.",
            "Read life/finances markdown context and include a Sources: section.",
            "Use read-only tools and answer with file-backed focus areas plus Sources.",
        ],
        params_extra={"mcp_max_tool_iterations": 6, "mcp_tool_profile": "read_only"},
    )
    scenario["citation_project_scope"] = project_citation_probe
    scenario["citation_life_scope"] = life_citation_probe

    _assert(
        summary,
        "k3_project_scope_citation_acceptance",
        bool(project_citation_probe.get("accepted")),
        str(project_citation_probe),
    )
    _assert(
        summary,
        "k3_life_scope_citation_acceptance",
        bool(life_citation_probe.get("accepted")),
        str(life_citation_probe),
    )
    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process K.1/K.2/K.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-k123")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=1.8)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=2.0)
    parser.add_argument(
        "--reset-from-template",
        action="store_true",
        default=True,
        help="Reset user scope from template before running probes (default: true).",
    )
    parser.add_argument(
        "--no-reset-from-template",
        action="store_false",
        dest="reset_from_template",
        help="Skip scope reset.",
    )
    parser.add_argument(
        "--library-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library",
    )
    parser.add_argument(
        "--template-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library_templates/Base_Library",
    )
    args = parser.parse_args()

    return Config(
        base_url=args.base_url,
        email=args.email,
        password=args.password,
        provider=args.provider,
        settings_id=args.settings_id,
        server_id=args.server_id,
        model=args.model,
        output_dir=Path(args.output_dir).resolve(),
        timeout_seconds=args.timeout_seconds,
        request_delay_seconds=args.request_delay_seconds,
        http_max_retries=args.http_max_retries,
        http_retry_base_seconds=args.http_retry_base_seconds,
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root).resolve(),
        template_root=Path(args.template_root).resolve(),
    )


def _summary_to_json(summary: ProbeSummary) -> Dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "base_url": summary.base_url,
        "provider": summary.provider,
        "model": summary.model,
        "reset_applied": summary.reset_applied,
        "assertions": [
            {"name": item.name, "passed": item.passed, "detail": item.detail}
            for item in summary.assertions
        ],
        "scenarios": summary.scenarios,
        "success": summary.success,
        "error": summary.error,
    }


def main() -> int:
    config = _parse_args()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = config.output_dir / f"run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = ProbeSummary(
        run_id=run_id,
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        base_url=config.base_url,
        provider=config.provider,
        model=config.model,
        reset_applied=False,
    )

    try:
        token, user_id = _login(config)
        summary.scenarios["auth"] = {"user_id": user_id}

        if config.reset_from_template:
            bootstrap_meta = _reset_scope_from_template(config, user_id)
            summary.reset_applied = True
            summary.scenarios["bootstrap"] = bootstrap_meta

        summary.scenarios["process_k1"] = _run_k1(summary, config, token, user_id)
        summary.scenarios["process_k2"] = _run_k2(summary, config, token, user_id)
        summary.scenarios["process_k3"] = _run_k3(summary, config, token, user_id)
        summary.success = True
    except Exception as exc:  # pragma: no cover - runtime harness
        summary.error = str(exc)
        summary.success = False
    finally:
        output = _summary_to_json(summary)
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"summary_path": str(summary_path.resolve()), "success": summary.success}, indent=2))

    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
