#!/usr/bin/env python3
"""Live probe harness for Process I.1, I.2, and I.3.

I.1: VS-11/TR-1 multi-turn evolution interview (scaffold -> interview -> scoped updates).
I.2: VS-5/VS-6 compound edit follow-through and approval UX closure checks.
I.3: VS-12/TR-4 delivery hand-off contract + duplicate-guard soak checks.
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


def _run_i1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    project_conversation_type = "project-side-business"

    scaffold_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Create a new project page for side business.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/finance",
            mcp_project_name="Finance",
        ),
    )
    scaffold_request = scaffold_start.get("approval_request") or {}
    scaffold_args = scaffold_request.get("arguments") or {}
    scaffold_files = scaffold_args.get("files") or []
    scaffold_paths = {
        entry.get("path")
        for entry in scaffold_files
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    created_scope_path = str(scaffold_args.get("path") or "").strip()
    scenario["scaffold_start"] = {
        "approval_required": scaffold_start.get("approval_required"),
        "approval_request": scaffold_request,
        "tooling_state": scaffold_start.get("tooling_state"),
    }
    _assert(
        summary,
        "i1_scaffold_approval_required",
        scaffold_start.get("approval_required") is True,
        str(scaffold_start),
    )
    _assert(
        summary,
        "i1_scaffold_tool_create_project",
        scaffold_request.get("tool") == "create_project",
        str(scaffold_request),
    )
    _assert(
        summary,
        "i1_scaffold_reason_seeded",
        scaffold_request.get("synthetic_reason") == "new_page_engine_scaffold",
        str(scaffold_request),
    )
    _assert(
        summary,
        "i1_scaffold_scope_path",
        isinstance(created_scope_path, str)
        and created_scope_path.startswith("projects/active/")
        and len(created_scope_path) > len("projects/active/"),
        str(scaffold_args),
    )
    _assert(
        summary,
        "i1_scaffold_seed_pack_contains_interview_state",
        {
            "interview.md",
            "interview-followup.md",
            "spec.md",
            "build-plan.md",
            "status.md",
            "_meta/interview-state.md",
        }.issubset(scaffold_paths),
        str(sorted(scaffold_paths)),
    )

    project_meta = next(
        (entry for entry in scaffold_files if entry.get("path") == "_meta/interview-state.md"),
        None,
    )
    parsed_project_meta = json.loads(str((project_meta or {}).get("content") or "{}"))
    _assert(
        summary,
        "i1_scaffold_meta_followup_due",
        bool(parsed_project_meta.get("first_followup_due_utc")),
        str(parsed_project_meta),
    )

    scaffold_conversation_id = scaffold_start.get("conversation_id")
    scaffold_request_id = scaffold_request.get("request_id")
    _assert(
        summary,
        "i1_scaffold_has_conversation_id",
        isinstance(scaffold_conversation_id, str) and bool(scaffold_conversation_id),
        str(scaffold_conversation_id),
    )
    _assert(
        summary,
        "i1_scaffold_has_request_id",
        isinstance(scaffold_request_id, str) and bool(scaffold_request_id),
        str(scaffold_request_id),
    )

    scaffold_approve = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/finance",
            mcp_project_name="Finance",
            conversation_id=scaffold_conversation_id,
            params_extra={
                "mcp_approval": {
                    "action": "approve",
                    "request_id": scaffold_request_id,
                }
            },
        ),
    )
    scaffold_approve_resolution = scaffold_approve.get("approval_resolution") or {}
    scenario["scaffold_approve"] = {
        "approval_resolution": scaffold_approve_resolution,
        "tooling_state": scaffold_approve.get("tooling_state"),
    }
    _assert(
        summary,
        "i1_scaffold_approved",
        scaffold_approve_resolution.get("status") == "approved",
        str(scaffold_approve_resolution),
    )

    created_scope_name = str(created_scope_path.split("/")[-1] or "New Page").replace("-", " ").title()

    interview_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Start page interview for this scope.",
            conversation_type=project_conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=created_scope_path,
            mcp_project_name=created_scope_name,
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_sync_on_request": False,
            },
        ),
    )
    interview_conversation_id = interview_start.get("conversation_id")
    interview_start_text = str(
        (((interview_start.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    interview_start_state = interview_start.get("tooling_state") or {}
    scenario["interview_start"] = {
        "conversation_id": interview_conversation_id,
        "assistant_text": interview_start_text,
        "tooling_state": interview_start_state,
    }
    _assert(
        summary,
        "i1_interview_started",
        "question 1 of" in interview_start_text.lower(),
        interview_start_text,
    )
    _assert(
        summary,
        "i1_interview_orchestration_mode",
        interview_start_state.get("conversation_orchestration") == "new_page_interview_deterministic",
        str(interview_start_state),
    )

    interview_answer = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message=(
                "Success means a repeatable weekly pipeline and a signed first customer by 2026-03-15."
            ),
            conversation_type=project_conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=created_scope_path,
            mcp_project_name=created_scope_name,
            conversation_id=interview_conversation_id,
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_sync_on_request": False,
            },
        ),
    )
    interview_answer_text = str(
        (((interview_answer.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["interview_answer"] = {
        "assistant_text": interview_answer_text,
        "tooling_state": interview_answer.get("tooling_state"),
    }
    lowered_answer_text = interview_answer_text.lower()
    _assert(
        summary,
        "i1_interview_prepared_scoped_updates",
        "prepared scoped updates" in lowered_answer_text and "reply `approve`" in lowered_answer_text,
        interview_answer_text,
    )
    _assert(
        summary,
        "i1_interview_preview_paths_present",
        "spec.md" in lowered_answer_text
        and "build-plan.md" in lowered_answer_text
        and "status.md" in lowered_answer_text,
        interview_answer_text,
    )

    interview_approve = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type=project_conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=created_scope_path,
            mcp_project_name=created_scope_name,
            conversation_id=interview_conversation_id,
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_sync_on_request": False,
            },
        ),
    )
    interview_approve_text = str(
        (((interview_approve.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    interview_approve_state = interview_approve.get("tooling_state") or {}

    if "could not apply all scoped updates yet" in interview_approve_text.lower():
        interview_approve_retry = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message="approve",
                conversation_type=project_conversation_type,
                mcp_scope_mode="project",
                mcp_project_slug=created_scope_path,
                mcp_project_name=created_scope_name,
                conversation_id=interview_conversation_id,
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_sync_on_request": False,
                },
            ),
        )
        interview_approve = interview_approve_retry
        interview_approve_text = str(
            (((interview_approve.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
            or ""
        )
        interview_approve_state = interview_approve.get("tooling_state") or {}
        scenario["interview_approve_retry"] = {
            "assistant_text": interview_approve_text,
            "tooling_state": interview_approve_state,
        }

    scenario["interview_approve"] = {
        "assistant_text": interview_approve_text,
        "tooling_state": interview_approve_state,
    }
    _assert(
        summary,
        "i1_interview_saved_updates",
        "saved scoped updates" in interview_approve_text.lower(),
        interview_approve_text,
    )
    _assert(
        summary,
        "i1_interview_execution_count",
        int(interview_approve_state.get("tool_calls_executed_count") or 0) >= 3,
        str(interview_approve_state),
    )
    _assert(
        summary,
        "i1_interview_deterministic_stop_reason",
        interview_approve_state.get("tool_loop_stop_reason") == "deterministic_new_page_interview_turn",
        str(interview_approve_state),
    )

    scenario["created_scope"] = {
        "path": created_scope_path,
        "name": created_scope_name,
    }
    return scenario


def _run_i2(
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
    *,
    scope_path: str,
    scope_name: str,
) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"attempts": []}
    project_conversation_type = "project-side-business"
    marker = f"I2 marker {summary.run_id}"
    prompts = [
        (
            "compound_two_files",
            (
                'Edit spec.md and build-plan.md. Add "Define launch criteria." and '
                '"Track weekly execution cadence."'
            ),
        ),
        (
            "compound_three_files",
            (
                f'Edit spec.md, build-plan.md, and status.md. Add "{marker} spec", '
                f'"{marker} plan", and "{marker} status".'
            ),
        ),
    ]

    selected_response: Optional[Dict[str, Any]] = None
    selected_prompt: Optional[str] = None
    for label, prompt in prompts:
        response = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=prompt,
                conversation_type=project_conversation_type,
                mcp_scope_mode="project",
                mcp_project_slug=scope_path,
                mcp_project_name=scope_name,
                params_extra={"mcp_native_tool_calling": True},
            ),
        )
        approval = response.get("approval_request") or {}
        preview = approval.get("preview") if isinstance(approval, dict) else None
        assistant_text = str(
            (((response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
            or ""
        )
        attempt = {
            "label": label,
            "prompt": prompt,
            "approval_required": response.get("approval_required"),
            "assistant_text": assistant_text,
            "approval_request": approval,
            "tooling_state": response.get("tooling_state"),
        }
        scenario["attempts"].append(attempt)
        if (
            response.get("approval_required") is True
            and isinstance(approval, dict)
            and str(approval.get("tool") or "").strip() == "edit_markdown"
            and str(approval.get("synthetic_reason") or "").strip()
            == "compound_edit_followthrough"
            and isinstance(preview, dict)
        ):
            selected_response = response
            selected_prompt = prompt
            break

    _assert(
        summary,
        "i2_compound_approval_found",
        selected_response is not None,
        json.dumps(scenario["attempts"], indent=2),
    )
    assert selected_response is not None
    first_approval = selected_response.get("approval_request") or {}
    first_preview = first_approval.get("preview") or {}
    first_text = str(
        (((selected_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    _assert(summary, "i2_approval_copy_locked", first_text == APPROVAL_REQUIRED_TEXT, first_text)
    _assert(
        summary,
        "i2_preview_has_diff_or_summary",
        bool(str(first_preview.get("diff") or "").strip())
        or bool(str(first_preview.get("summary") or "").strip()),
        str(first_preview),
    )
    _assert(
        summary,
        "i2_preview_marker",
        first_preview.get("previewTool") == "preview_markdown_change",
        str(first_preview),
    )

    if first_preview.get("diffTruncated") is True:
        _assert(
            summary,
            "i2_preview_truncation_notice",
            first_preview.get("previewNotice") == "Diff truncated for approval preview.",
            str(first_preview),
        )

    conversation_id = selected_response.get("conversation_id")
    request_id = first_approval.get("request_id")
    _assert(
        summary,
        "i2_has_conversation_id",
        isinstance(conversation_id, str) and bool(conversation_id),
        str(conversation_id),
    )
    _assert(
        summary,
        "i2_has_request_id",
        isinstance(request_id, str) and bool(request_id),
        str(request_id),
    )

    approve_response = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type=project_conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=scope_path,
            mcp_project_name=scope_name,
            conversation_id=conversation_id,
            params_extra={
                "mcp_native_tool_calling": True,
                "mcp_approval": {
                    "action": "approve",
                    "request_id": request_id,
                }
            },
        ),
    )
    approve_resolution = approve_response.get("approval_resolution") or {}
    approve_state = approve_response.get("tooling_state") or {}
    final_text = str(
        (((approve_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["approval_resume"] = {
        "selected_prompt": selected_prompt,
        "approval_resolution": approve_resolution,
        "tooling_state": approve_state,
        "assistant_text": final_text,
    }
    _assert(
        summary,
        "i2_approval_resolved",
        approve_resolution.get("status") == "approved",
        str(approve_resolution),
    )
    _assert(
        summary,
        "i2_compound_execution_count",
        int(approve_state.get("tool_calls_executed_count") or 0) >= 2,
        str(approve_state),
    )
    _assert(
        summary,
        "i2_confirmation_copy_multi_op",
        (
            "approved and executed `edit_markdown` successfully" in final_text.lower()
            or "edits have been successfully applied" in final_text.lower()
            or "successfully applied" in final_text.lower()
        ),
        final_text,
    )
    _assert(summary, "i2_confirmation_text_non_empty", bool(final_text.strip()), final_text)

    return scenario


def _run_i3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    digest_event_id = f"i3-digest-email-{summary.run_id}"
    digest_start = _chat(
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
                "mcp_digest_schedule_event_id": digest_event_id,
                "mcp_digest_sections": ["top_priorities", "needs_attention"],
            },
        ),
    )
    digest_conversation_id = digest_start.get("conversation_id")
    digest_state = digest_start.get("tooling_state") or {}
    digest_handoff = digest_start.get("delivery_handoff") or {}
    scenario["digest_email_start"] = {
        "conversation_id": digest_conversation_id,
        "tooling_state": digest_state,
        "delivery_handoff": digest_handoff,
        "approval_required": digest_start.get("approval_required"),
    }
    _assert(
        summary,
        "i3_digest_email_conversation_id",
        isinstance(digest_conversation_id, str) and bool(digest_conversation_id),
        str(digest_conversation_id),
    )
    _assert(
        summary,
        "i3_digest_email_channel_metadata",
        digest_state.get("digest_delivery_channel") == "email"
        and digest_state.get("mcp_project_slug") == "digest"
        and digest_state.get("conversation_orchestration") == "digest_heartbeat"
        and digest_state.get("tool_profile") in {"digest", "read_only"},
        str(digest_state),
    )
    _assert(
        summary,
        "i3_digest_email_handoff_contract",
        digest_handoff.get("channel") == "email"
        and digest_handoff.get("conversation_type") == "digest-email"
        and digest_handoff.get("format") == "markdown"
        and isinstance(digest_handoff.get("body"), str)
        and bool(str(digest_handoff.get("body") or "").strip()),
        str(digest_handoff),
    )
    tooling_handoff = digest_state.get("digest_delivery_handoff") or {}
    _assert(
        summary,
        "i3_digest_email_handoff_metadata",
        tooling_handoff.get("channel") == "email",
        str(tooling_handoff),
    )

    digest_repeat = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Run scheduled digest delivery now.",
            conversation_type="digest-email",
            mcp_scope_mode="none",
            conversation_id=digest_conversation_id,
            params_extra={
                "mcp_tools_enabled": False,
                "mcp_tool_profile": "read_only",
                "mcp_digest_schedule_enabled": True,
                "mcp_digest_force_run": True,
                "mcp_digest_schedule_event_id": digest_event_id,
                "mcp_digest_sections": ["top_priorities", "needs_attention"],
            },
        ),
    )
    digest_repeat_state = digest_repeat.get("tooling_state") or {}
    scenario["digest_email_repeat"] = {
        "tooling_state": digest_repeat_state,
        "delivery_handoff": digest_repeat.get("delivery_handoff"),
    }
    _assert(
        summary,
        "i3_digest_duplicate_guard_status",
        digest_repeat_state.get("digest_schedule_status") == "duplicate_guard",
        str(digest_repeat_state),
    )
    _assert(
        summary,
        "i3_digest_duplicate_guard_history_seen",
        digest_repeat_state.get("digest_schedule_duplicate_guard") == "history_seen",
        str(digest_repeat_state),
    )

    preflush_event_id = f"i3-preflush-{summary.run_id}"
    long_message = " ".join(["context-window-pressure"] * 220)
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
        "i3_preflush_conversation_id",
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
        "i3_preflush_duplicate_guard_status",
        preflush_repeat_state.get("pre_compaction_flush_status") == "duplicate_guard",
        str(preflush_repeat_state),
    )
    _assert(
        summary,
        "i3_preflush_duplicate_guard_history_seen",
        preflush_repeat_state.get("pre_compaction_flush_duplicate_guard") == "history_seen",
        str(preflush_repeat_state),
    )

    sustained_checks: List[Dict[str, Any]] = []
    for index in range(5):
        event_id = f"i3-digest-email-sustain-{summary.run_id}-{index + 1}"
        sustained = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=f"Run digest delivery pass {index + 1}.",
                conversation_type="digest-email",
                mcp_scope_mode="none",
                conversation_id=digest_conversation_id,
                params_extra={
                    "mcp_tools_enabled": False,
                    "mcp_tool_profile": "read_only",
                    "mcp_digest_schedule_enabled": True,
                    "mcp_digest_force_run": True,
                    "mcp_digest_schedule_event_id": event_id,
                },
            ),
        )
        sustained_state = sustained.get("tooling_state") or {}
        sustained_handoff = sustained.get("delivery_handoff") or {}
        sustained_checks.append(
            {
                "event_id": event_id,
                "status": sustained_state.get("digest_schedule_status"),
                "channel": sustained_state.get("digest_delivery_channel"),
                "handoff_channel": sustained_handoff.get("channel"),
            }
        )
        _assert(
            summary,
            f"i3_sustained_status_{index + 1}",
            sustained_state.get("digest_schedule_status")
            in {"triggered", "awaiting_approval", "completed_tool_calls", "completed_noop", "duplicate_guard"},
            str(sustained_state),
        )
        _assert(
            summary,
            f"i3_sustained_handoff_{index + 1}",
            sustained_handoff.get("channel") == "email"
            and sustained_handoff.get("conversation_type") == "digest-email",
            str(sustained_handoff),
        )
    scenario["digest_email_sustained"] = sustained_checks

    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process I.1/I.2/I.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-i123")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=1.6)
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

        process_i1 = _run_i1(summary, config, token, user_id)
        summary.scenarios["process_i1"] = process_i1
        created_scope = process_i1.get("created_scope") if isinstance(process_i1, dict) else {}
        created_scope_path = str((created_scope or {}).get("path") or "").strip()
        created_scope_name = str((created_scope or {}).get("name") or "New Page").strip()
        if not created_scope_path:
            raise RuntimeError("Process I.1 did not return created scope path")

        summary.scenarios["process_i2"] = _run_i2(
            summary,
            config,
            token,
            user_id,
            scope_path=created_scope_path,
            scope_name=created_scope_name,
        )
        summary.scenarios["process_i3"] = _run_i3(summary, config, token, user_id)
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
