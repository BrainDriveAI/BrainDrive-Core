#!/usr/bin/env python3
"""Live probe harness for Process G.1, G.2, and G.3.

G.1: VS-11 / TR-1 new-page engine scaffold approval + execution.
G.2: VS-5 / VS-6 edit-preview approval payload + execution follow-through.
G.3: TR-4 / VS-12 mixed-flow runtime sweep (capture + digest + normal chat)
      with duplicate-guard checks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
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
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path


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


def _run_g1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    prompt = "Create a new project page for side business."

    create_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=prompt,
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="projects/active/finance",
        mcp_project_name="Finance",
    )
    create_response = _chat(config, token, create_payload)
    scenario["create_response"] = {
        "approval_required": create_response.get("approval_required"),
        "approval_request": create_response.get("approval_request"),
        "conversation_id": create_response.get("conversation_id"),
        "tooling_state": create_response.get("tooling_state"),
    }

    _assert(summary, "g1_approval_required", create_response.get("approval_required") is True, str(create_response))
    approval_request = create_response.get("approval_request") or {}
    _assert(summary, "g1_tool_create_project", approval_request.get("tool") == "create_project", str(approval_request))
    _assert(
        summary,
        "g1_reason_new_page_engine",
        approval_request.get("synthetic_reason") == "new_page_engine_scaffold",
        str(approval_request),
    )

    approval_args = approval_request.get("arguments") or {}
    create_path = str(approval_args.get("path") or "")
    files = approval_args.get("files") or []
    file_paths = {entry.get("path") for entry in files if isinstance(entry, dict)}
    _assert(summary, "g1_path_side_business", create_path == "projects/active/side-business", create_path)
    _assert(
        summary,
        "g1_required_seed_files",
        {"AGENT.md", "interview.md", "spec.md", "build-plan.md"}.issubset(file_paths),
        str(sorted(file_paths)),
    )

    conversation_id = create_response.get("conversation_id")
    request_id = approval_request.get("request_id")
    _assert(summary, "g1_has_conversation_id", isinstance(conversation_id, str) and bool(conversation_id), str(conversation_id))
    _assert(summary, "g1_has_request_id", isinstance(request_id, str) and bool(request_id), str(request_id))

    approve_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="approve",
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="projects/active/finance",
        mcp_project_name="Finance",
        conversation_id=conversation_id,
        params_extra={
            "mcp_approval": {
                "action": "approve",
                "request_id": request_id,
            }
        },
    )
    approve_response = _chat(config, token, approve_payload)
    scenario["approve_response"] = {
        "approval_resolution": approve_response.get("approval_resolution"),
        "tooling_state": approve_response.get("tooling_state"),
        "choice": (approve_response.get("choices") or [{}])[0],
    }
    resolution = approve_response.get("approval_resolution") or {}
    _assert(summary, "g1_approval_resolution_approved", resolution.get("status") == "approved", str(resolution))
    tooling_state = approve_response.get("tooling_state") or {}
    _assert(
        summary,
        "g1_tool_call_executed_after_approval",
        int(tooling_state.get("tool_calls_executed_count") or 0) >= 1,
        str(tooling_state),
    )
    _assert(
        summary,
        "g1_approval_marked_resolved",
        bool(tooling_state.get("approval_resolved")),
        str(tooling_state),
    )

    return scenario


def _run_g2(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"attempts": []}
    marker = f"G2 preview marker {summary.run_id}"
    prompts = [
        (
            "Return ONLY a tool call to edit_markdown. "
            "Path: projects/active/side-business/spec.md. "
            f"Operation: append line '- {marker}'. No prose."
        ),
        (
            "You are required to call edit_markdown now on "
            "projects/active/side-business/spec.md and append this exact line: "
            f"- {marker}"
        ),
        (
            "Append this line to projects/active/side-business/spec.md and use a tool call only: "
            f"- {marker}"
        ),
    ]

    selected_response: Optional[Dict[str, Any]] = None
    selected_prompt: Optional[str] = None
    for prompt in prompts:
        response = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=prompt,
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug="projects/active/side-business",
                mcp_project_name="Side Business",
                params_extra={"mcp_native_tool_calling": True},
            ),
        )
        approval = response.get("approval_request") or {}
        preview = approval.get("preview") if isinstance(approval, dict) else None
        attempt = {
            "prompt": prompt,
            "approval_required": response.get("approval_required"),
            "approval_tool": approval.get("tool") if isinstance(approval, dict) else None,
            "has_preview": isinstance(preview, dict),
            "conversation_id": response.get("conversation_id"),
            "approval_request": approval,
        }
        scenario["attempts"].append(attempt)
        if (
            response.get("approval_required") is True
            and isinstance(approval, dict)
            and str(approval.get("tool") or "").strip() in {"edit_markdown", "write_markdown"}
            and isinstance(preview, dict)
        ):
            selected_response = response
            selected_prompt = prompt
            break

    _assert(summary, "g2_found_preview_approval", selected_response is not None, json.dumps(scenario["attempts"], indent=2))
    assert selected_response is not None
    approval_request = selected_response.get("approval_request") or {}
    preview = approval_request.get("preview") or {}
    _assert(
        summary,
        "g2_preview_contains_diff_or_summary",
        bool(str(preview.get("diff") or "").strip()) or bool(str(preview.get("summary") or "").strip()),
        str(preview),
    )
    _assert(summary, "g2_preview_tool_marker", preview.get("previewTool") == "preview_markdown_change", str(preview))

    conversation_id = selected_response.get("conversation_id")
    request_id = approval_request.get("request_id")
    _assert(summary, "g2_has_conversation_id", isinstance(conversation_id, str) and bool(conversation_id), str(conversation_id))
    _assert(summary, "g2_has_request_id", isinstance(request_id, str) and bool(request_id), str(request_id))

    approve_response = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/side-business",
            mcp_project_name="Side Business",
            conversation_id=conversation_id,
            params_extra={
                "mcp_approval": {
                    "action": "approve",
                    "request_id": request_id,
                }
            },
        ),
    )
    scenario["selected_prompt"] = selected_prompt
    scenario["approve_response"] = {
        "approval_resolution": approve_response.get("approval_resolution"),
        "tooling_state": approve_response.get("tooling_state"),
        "choice": (approve_response.get("choices") or [{}])[0],
    }
    resolution = approve_response.get("approval_resolution") or {}
    _assert(summary, "g2_approval_resolution_approved", resolution.get("status") == "approved", str(resolution))

    final_text = str((((approve_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or "")
    _assert(summary, "g2_resume_has_confirmation_text", bool(final_text.strip()), str(approve_response))
    tooling_state = approve_response.get("tooling_state") or {}
    _assert(
        summary,
        "g2_tool_call_executed_after_approval",
        int(tooling_state.get("tool_calls_executed_count") or 0) >= 1,
        str(tooling_state),
    )
    _assert(
        summary,
        "g2_approval_marked_resolved",
        bool(tooling_state.get("approval_resolved")),
        str(tooling_state),
    )

    return scenario


def _run_g3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    normal_response = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="What are the top priorities for my side business page right now?",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/side-business",
            mcp_project_name="Side Business",
        ),
    )
    scenario["normal_chat"] = {
        "conversation_id": normal_response.get("conversation_id"),
        "tooling_state": normal_response.get("tooling_state"),
    }
    _assert(
        summary,
        "g3_normal_chat_has_tooling_state",
        isinstance(normal_response.get("tooling_state"), dict),
        str(normal_response),
    )

    capture_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Capture this note: review subscription spend and recurring fees this week.",
            conversation_type="capture",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
        ),
    )
    capture_request = capture_start.get("approval_request") or {}
    scenario["capture_start"] = {
        "approval_required": capture_start.get("approval_required"),
        "approval_request": capture_request,
        "conversation_id": capture_start.get("conversation_id"),
    }
    _assert(summary, "g3_capture_approval_required", capture_start.get("approval_required") is True, str(capture_start))
    _assert(summary, "g3_capture_tool_create_markdown", capture_request.get("tool") == "create_markdown", str(capture_request))

    capture_approve = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type="capture",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
            conversation_id=capture_start.get("conversation_id"),
            params_extra={
                "mcp_approval": {
                    "action": "approve",
                    "request_id": capture_request.get("request_id"),
                }
            },
        ),
    )
    scenario["capture_approve"] = {
        "approval_resolution": capture_approve.get("approval_resolution"),
        "tooling_state": capture_approve.get("tooling_state"),
    }
    _assert(
        summary,
        "g3_capture_approval_resolution_approved",
        (capture_approve.get("approval_resolution") or {}).get("status") == "approved",
        str(capture_approve.get("approval_resolution")),
    )

    digest_event_id = "g3-digest-event-1"
    digest_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Run scheduled digest now.",
            conversation_type="digest",
            mcp_scope_mode="none",
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_digest_schedule_enabled": True,
                "mcp_digest_force_run": True,
                "mcp_digest_schedule_event_id": digest_event_id,
            },
        ),
    )
    digest_conversation_id = digest_start.get("conversation_id")
    scenario["digest_start"] = {
        "conversation_id": digest_conversation_id,
        "tooling_state": digest_start.get("tooling_state"),
    }
    _assert(
        summary,
        "g3_digest_has_conversation_id",
        isinstance(digest_conversation_id, str) and bool(digest_conversation_id),
        str(digest_conversation_id),
    )

    digest_repeat = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Run scheduled digest now.",
            conversation_type="digest",
            mcp_scope_mode="none",
            conversation_id=digest_conversation_id,
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_digest_schedule_enabled": True,
                "mcp_digest_force_run": True,
                "mcp_digest_schedule_event_id": digest_event_id,
            },
        ),
    )
    digest_repeat_state = digest_repeat.get("tooling_state") or {}
    scenario["digest_repeat"] = {
        "tooling_state": digest_repeat_state,
    }
    _assert(
        summary,
        "g3_digest_duplicate_guard_status",
        digest_repeat_state.get("digest_schedule_status") == "duplicate_guard",
        str(digest_repeat_state),
    )
    _assert(
        summary,
        "g3_digest_duplicate_guard_history_seen",
        digest_repeat_state.get("digest_schedule_duplicate_guard") == "history_seen",
        str(digest_repeat_state),
    )

    preflush_event_id = "g3-preflush-event-1"
    long_message = " ".join(["context-overflow-check"] * 220)
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
    scenario["preflush_start"] = {
        "conversation_id": preflush_conversation_id,
        "tooling_state": preflush_start.get("tooling_state"),
    }
    _assert(
        summary,
        "g3_preflush_has_conversation_id",
        isinstance(preflush_conversation_id, str) and bool(preflush_conversation_id),
        str(preflush_conversation_id),
    )

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
    preflush_repeat_state = preflush_repeat.get("tooling_state") or {}
    scenario["preflush_repeat"] = {
        "tooling_state": preflush_repeat_state,
    }
    _assert(
        summary,
        "g3_preflush_duplicate_guard_status",
        preflush_repeat_state.get("pre_compaction_flush_status") == "duplicate_guard",
        str(preflush_repeat_state),
    )
    _assert(
        summary,
        "g3_preflush_duplicate_guard_history_seen",
        preflush_repeat_state.get("pre_compaction_flush_duplicate_guard") == "history_seen",
        str(preflush_repeat_state),
    )

    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process G.1/G.2/G.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-g123")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=1.5)
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
            bootstrap = _reset_scope_from_template(config, user_id)
            summary.reset_applied = True
            summary.scenarios["bootstrap"] = bootstrap

        summary.scenarios["g1"] = _run_g1(summary, config, token, user_id)
        summary.scenarios["g2"] = _run_g2(summary, config, token, user_id)
        summary.scenarios["g3"] = _run_g3(summary, config, token, user_id)
        summary.success = True
    except Exception as exc:  # pragma: no cover - runtime harness surface
        summary.error = str(exc)
        summary.success = False

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(_summary_to_json(summary), indent=2), encoding="utf-8")
    print(str(summary_path))
    print(f"success={summary.success}")
    if summary.error:
        print(f"error={summary.error}")

    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
