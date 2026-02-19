#!/usr/bin/env python3
"""Live probe harness for Process J.1, J.2, and J.3.

J.1: VS-10/TR-3/TR-1 multi-target cross-pollination chain + project-scope compat policy.
J.2: VS-1/VS-2/VS-6 capture multi-folder fanout sequencing + task payload quality.
J.3: VS-12/TR-4 delivery integration closure with persisted outbox handoff + soak checks.
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
from copy import deepcopy
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
    state["recommended_next_topic"] = "fitness"
    state["updated_at_utc"] = now_iso

    onboarding_path.write_text(
        json.dumps(state, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
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


def _run_j1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    scenario["onboarding_seed"] = _seed_finances_onboarding_complete(config, user_id)

    project_policy_probe = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="Create a task to lock release checklist by next week.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="projects/active/finance",
            mcp_project_name="Finance",
            params_extra={"mcp_max_tool_iterations": 5},
        ),
    )
    project_policy_state = project_policy_probe.get("tooling_state") or {}
    scenario["project_policy_probe"] = {
        "tooling_state": project_policy_state,
        "approval_required": project_policy_probe.get("approval_required"),
        "approval_request": project_policy_probe.get("approval_request"),
    }
    _assert(
        summary,
        "j1_project_policy_mode",
        project_policy_state.get("tool_policy_mode") == "dual_path_project_scope_compat",
        str(project_policy_state),
    )
    _assert(
        summary,
        "j1_project_policy_profile",
        project_policy_state.get("tool_profile") == "full"
        and project_policy_state.get("tool_profile_source") == "routing_scope_policy",
        str(project_policy_state),
    )

    cross_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="I need a family budget plan and workout routine for my kids.",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
            params_extra={
                "mcp_max_tool_iterations": 6,
            },
        ),
    )
    first_request = cross_start.get("approval_request") or {}
    first_tool = str(first_request.get("tool") or "").strip()
    first_reason = str(first_request.get("synthetic_reason") or "")
    first_is_cross_pollination = first_reason.startswith("cross_pollination_finances_to_")
    conversation_id = cross_start.get("conversation_id")
    scenario["cross_pollination_start"] = {
        "approval_required": cross_start.get("approval_required"),
        "approval_request": first_request,
        "reason_mode": (
            "synthetic_cross_pollination"
            if first_is_cross_pollination
            else "provider_or_other_variance"
        ),
        "tooling_state": cross_start.get("tooling_state"),
        "conversation_id": conversation_id,
    }
    _assert(
        summary,
        "j1_cross_start_approval_required",
        cross_start.get("approval_required") is True,
        str(cross_start),
    )
    _assert(
        summary,
        "j1_cross_first_tool_mutating_approval",
        bool(first_tool)
        and str(first_request.get("safety_class") or "").strip().lower() == "mutating",
        str(first_request),
    )
    _assert(
        summary,
        "j1_cross_first_reason_or_provider_variance",
        first_is_cross_pollination
        or not first_reason,
        str(first_request),
    )
    _assert(
        summary,
        "j1_cross_has_conversation_and_request",
        isinstance(conversation_id, str)
        and bool(conversation_id)
        and isinstance(first_request.get("request_id"), str)
        and bool(str(first_request.get("request_id"))),
        str({"conversation_id": conversation_id, "request": first_request}),
    )

    cross_resume = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type="chat",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
            conversation_id=conversation_id,
            params_extra={
                "mcp_max_tool_iterations": 6,
                "mcp_approval": {
                    "action": "approve",
                    "request_id": first_request.get("request_id"),
                },
            },
        ),
    )
    second_request = cross_resume.get("approval_request") or {}
    second_reason = str(second_request.get("synthetic_reason") or "")
    second_requires_approval = cross_resume.get("approval_required") is True
    second_is_cross_pollination = second_reason.startswith("cross_pollination_finances_to_")
    resume_state = cross_resume.get("tooling_state") or {}
    scenario["cross_pollination_resume"] = {
        "approval_required": cross_resume.get("approval_required"),
        "approval_resolution": cross_resume.get("approval_resolution"),
        "approval_request": second_request,
        "reason_mode": (
            "synthetic_cross_pollination"
            if second_is_cross_pollination
            else "provider_or_other_variance"
        ),
        "tooling_state": resume_state,
    }
    _assert(
        summary,
        "j1_cross_resume_resolution",
        (cross_resume.get("approval_resolution") or {}).get("status") == "approved",
        str(cross_resume.get("approval_resolution")),
    )
    _assert(
        summary,
        "j1_cross_resume_progress",
        second_requires_approval
        or resume_state.get("tool_loop_stop_reason") == "provider_final_response",
        str(cross_resume),
    )
    if second_requires_approval:
        second_tool = str(second_request.get("tool") or "").strip()
        if first_is_cross_pollination:
            _assert(
                summary,
                "j1_cross_second_tool_create_markdown",
                second_tool == "create_markdown",
                str(second_request),
            )
            _assert(
                summary,
                "j1_cross_second_reason_different_target",
                second_is_cross_pollination and second_reason != first_reason,
                str({"first": first_reason, "second": second_reason}),
            )
        else:
            _assert(
                summary,
                "j1_cross_second_tool_provider_variance",
                bool(second_tool)
                and str(second_request.get("safety_class") or "").strip().lower() == "mutating",
                str(second_request),
            )
    else:
        _assert(
            summary,
            "j1_cross_resume_provider_variance_path",
            not second_request,
            str(second_request),
        )
    _assert(
        summary,
        "j1_cross_stop_reason",
        resume_state.get("tool_loop_stop_reason") in {"approval_required", "provider_final_response"},
        str(resume_state),
    )

    return scenario


def _run_j2(
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    base_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="",
        conversation_type="capture",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 6},
    )

    prompts = [
        "Capture this in finances and relationships and add a task to review budget for Dave J by next week.",
        "Capture this note to life/finances and life/relationships, then add a task for Dave J to review budget by next week.",
        "Save this in finances and relationships capture notes and create a task: Dave J reviews budget next week.",
    ]

    selected_attempt: Optional[Dict[str, Any]] = None
    for attempt_idx, prompt in enumerate(prompts, start=1):
        attempt_data: Dict[str, Any] = {"prompt": prompt}
        start_payload = deepcopy(base_payload)
        start_payload["messages"] = [{"role": "user", "content": prompt}]
        start = _chat(config, token, start_payload)
        first_request = start.get("approval_request") or {}
        conversation_id = start.get("conversation_id")
        attempt_data["start"] = {
            "approval_required": start.get("approval_required"),
            "approval_request": first_request,
            "conversation_id": conversation_id,
            "tooling_state": start.get("tooling_state"),
        }
        first_path = str((first_request.get("arguments") or {}).get("path") or "")
        inbox_ok = (
            start.get("approval_required") is True
            and first_request.get("tool") == "create_markdown"
            and "/capture/inbox/" in f"/{first_path.strip('/')}"
        )
        attempt_data["inbox_ok"] = inbox_ok
        if not inbox_ok:
            scenario[f"fanout_attempt_{attempt_idx}"] = attempt_data
            continue

        approval_chain: List[Dict[str, Any]] = [first_request]
        current_request: Dict[str, Any] = first_request
        create_task_request: Optional[Dict[str, Any]] = None
        all_steps_approved = True

        for step_idx in range(1, 6):
            request_id = str(current_request.get("request_id") or "").strip()
            if not request_id:
                break
            next_payload = deepcopy(base_payload)
            next_payload["conversation_id"] = conversation_id
            next_payload["messages"] = [{"role": "user", "content": "approve"}]
            next_payload["params"]["mcp_approval"] = {
                "action": "approve",
                "request_id": request_id,
            }
            step_response = _chat(config, token, next_payload)
            step_request = step_response.get("approval_request") or {}
            attempt_data[f"step_{step_idx}"] = {
                "approval_required": step_response.get("approval_required"),
                "approval_resolution": step_response.get("approval_resolution"),
                "approval_request": step_request,
                "tooling_state": step_response.get("tooling_state"),
            }
            if (step_response.get("approval_resolution") or {}).get("status") != "approved":
                all_steps_approved = False
                break
            if step_response.get("approval_required") is not True:
                break
            approval_chain.append(step_request)
            current_request = step_request
            if step_request.get("tool") == "create_task":
                create_task_request = step_request
                break

        fanout_seen = False
        for request_item in approval_chain:
            if request_item.get("tool") != "create_markdown":
                continue
            reason_value = str(request_item.get("synthetic_reason") or "")
            request_path = str((request_item.get("arguments") or {}).get("path") or "")
            if reason_value.startswith("capture_scope_fanout_"):
                fanout_seen = True
                break
            if request_path.startswith("life/") and "/capture/" in request_path:
                fanout_seen = True
                break

        create_task_args = (create_task_request or {}).get("arguments") or {}
        payload_quality_ok = (
            bool(str(create_task_args.get("owner") or "").strip())
            and bool(str(create_task_args.get("due") or "").strip())
            and str(create_task_args.get("scope") or "").strip() == "life/finances"
        )

        attempt_data["approval_chain"] = approval_chain
        attempt_data["all_steps_approved"] = all_steps_approved
        attempt_data["fanout_seen"] = fanout_seen
        attempt_data["create_task_request"] = create_task_request
        attempt_data["create_task_payload_quality_ok"] = payload_quality_ok
        scenario[f"fanout_attempt_{attempt_idx}"] = attempt_data

        if all_steps_approved and fanout_seen and isinstance(create_task_request, dict) and payload_quality_ok:
            selected_attempt = attempt_data
            break

    _assert(
        summary,
        "j2_first_approval_inbox",
        isinstance(selected_attempt, dict)
        and bool(selected_attempt.get("inbox_ok")),
        str(scenario),
    )
    _assert(
        summary,
        "j2_scope_fanout_seen",
        isinstance(selected_attempt, dict)
        and bool(selected_attempt.get("fanout_seen")),
        str(scenario),
    )
    _assert(
        summary,
        "j2_create_task_approval_seen",
        isinstance(selected_attempt, dict)
        and isinstance(selected_attempt.get("create_task_request"), dict),
        str(scenario),
    )
    _assert(
        summary,
        "j2_create_task_payload_quality",
        isinstance(selected_attempt, dict)
        and bool(selected_attempt.get("create_task_payload_quality_ok")),
        str(scenario),
    )
    scenario["selected_attempt"] = selected_attempt

    return scenario


def _run_j3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    digest_event_id = f"j3-digest-email-{summary.run_id}"
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
        "j3_digest_email_conversation_id",
        isinstance(digest_conversation_id, str) and bool(digest_conversation_id),
        str(digest_conversation_id),
    )
    _assert(
        summary,
        "j3_digest_email_channel_metadata",
        digest_state.get("digest_delivery_channel") == "email"
        and digest_state.get("mcp_project_slug") == "digest"
        and digest_state.get("conversation_orchestration") == "digest_heartbeat"
        and digest_state.get("tool_profile") in {"digest", "read_only"},
        str(digest_state),
    )
    _assert(
        summary,
        "j3_digest_email_handoff_contract",
        digest_handoff.get("channel") == "email"
        and digest_handoff.get("conversation_type") == "digest-email"
        and digest_handoff.get("format") == "markdown"
        and isinstance(digest_handoff.get("body"), str)
        and bool(str(digest_handoff.get("body") or "").strip())
        and str(digest_handoff.get("delivery_record_status") or "").strip() == "persisted"
        and bool(str(digest_handoff.get("delivery_record_path") or "").strip()),
        str(digest_handoff),
    )
    tooling_handoff = digest_state.get("digest_delivery_handoff") or {}
    _assert(
        summary,
        "j3_digest_email_handoff_metadata",
        tooling_handoff.get("channel") == "email"
        and tooling_handoff.get("delivery_record_status") == "persisted",
        str(tooling_handoff),
    )
    handoff_path = Path(str(digest_handoff.get("delivery_record_path") or ""))
    _assert(
        summary,
        "j3_digest_email_handoff_path_exists",
        handoff_path.is_file(),
        str(handoff_path),
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
        "j3_digest_duplicate_guard_status",
        digest_repeat_state.get("digest_schedule_status") == "duplicate_guard",
        str(digest_repeat_state),
    )
    _assert(
        summary,
        "j3_digest_duplicate_guard_history_seen",
        digest_repeat_state.get("digest_schedule_duplicate_guard") == "history_seen",
        str(digest_repeat_state),
    )

    preflush_event_id = f"j3-preflush-{summary.run_id}"
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
        "j3_preflush_conversation_id",
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
        "j3_preflush_duplicate_guard_status",
        preflush_repeat_state.get("pre_compaction_flush_status") == "duplicate_guard",
        str(preflush_repeat_state),
    )
    _assert(
        summary,
        "j3_preflush_duplicate_guard_history_seen",
        preflush_repeat_state.get("pre_compaction_flush_duplicate_guard") == "history_seen",
        str(preflush_repeat_state),
    )

    sustained_checks: List[Dict[str, Any]] = []
    for index in range(8):
        event_id = f"j3-digest-email-sustain-{summary.run_id}-{index + 1}"
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
        sustained_path = Path(str(sustained_handoff.get("delivery_record_path") or ""))
        sustained_checks.append(
            {
                "event_id": event_id,
                "status": sustained_state.get("digest_schedule_status"),
                "channel": sustained_state.get("digest_delivery_channel"),
                "handoff_channel": sustained_handoff.get("channel"),
                "record_status": sustained_handoff.get("delivery_record_status"),
                "record_path": str(sustained_handoff.get("delivery_record_path") or ""),
            }
        )
        _assert(
            summary,
            f"j3_sustained_status_{index + 1}",
            sustained_state.get("digest_schedule_status")
            in {"triggered", "awaiting_approval", "completed_tool_calls", "completed_noop", "duplicate_guard"},
            str(sustained_state),
        )
        _assert(
            summary,
            f"j3_sustained_handoff_{index + 1}",
            sustained_handoff.get("channel") == "email"
            and sustained_handoff.get("conversation_type") == "digest-email"
            and sustained_handoff.get("delivery_record_status") == "persisted",
            str(sustained_handoff),
        )
        _assert(
            summary,
            f"j3_sustained_handoff_path_{index + 1}",
            sustained_path.is_file(),
            str(sustained_path),
        )
    scenario["digest_email_sustained"] = sustained_checks

    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process J.1/J.2/J.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-j123")
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

        summary.scenarios["process_j1"] = _run_j1(summary, config, token, user_id)
        summary.scenarios["process_j2"] = _run_j2(summary, config, token, user_id)
        summary.scenarios["process_j3"] = _run_j3(summary, config, token, user_id)
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
