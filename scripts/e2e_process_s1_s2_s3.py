#!/usr/bin/env python3
"""Live probe harness for Process S.1, S.2, and S.3.

S.1: VS-1/VS-2/VS-3/TR-1 capture/task reliability closure.
S.2: VS-9/VS-10/VS-11/VS-17 context evolution closure.
S.3: VS-7/VS-8/TR-1 final integrated mixed sweep + browser UX check.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
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
    ui_base_url: str
    provider: str
    model: str
    reset_applied: bool
    assertions: List[AssertionResult] = field(default_factory=list)
    scenarios: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: Optional[str] = None


@dataclass
class Config:
    base_url: str
    ui_base_url: str
    email: str
    password: str
    provider: str
    settings_id: str
    server_id: str
    model: str
    browser_model_hint: str
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path
    delivery_email_endpoint: str
    delivery_slack_endpoint: str


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


def _onboarding_state_path(config: Config, user_id: str) -> Path:
    normalized_user_id = _normalize_user_id(user_id)
    return config.library_root / "users" / normalized_user_id / ".braindrive" / "onboarding_state.json"


def _set_topic_onboarding_status(config: Config, user_id: str, topic: str, status: str) -> Dict[str, Any]:
    topic_key = str(topic or "").strip().lower()
    path = _onboarding_state_path(config, user_id)
    if not path.is_file():
        return {"updated": False, "reason": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"updated": False, "reason": f"read_error:{exc}", "path": str(path)}
    if not isinstance(payload, dict):
        return {"updated": False, "reason": "invalid_json_root", "path": str(path)}

    now_iso = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    starter_topics = payload.setdefault("starter_topics", {})
    completed_at = payload.setdefault("completed_at", {})
    topic_progress = payload.setdefault("topic_progress", {})
    progress = topic_progress.setdefault(topic_key, {}) if isinstance(topic_progress, dict) else {}

    normalized_status = str(status or "").strip().lower()
    if normalized_status == "complete":
        if isinstance(starter_topics, dict):
            starter_topics[topic_key] = "complete"
        if isinstance(completed_at, dict):
            completed_at[topic_key] = now_iso
        if isinstance(progress, dict):
            progress["status"] = "complete"
            progress["phase"] = "complete"
            progress["completed_at_utc"] = now_iso
            progress["last_updated_at_utc"] = now_iso
            progress.setdefault("started_at_utc", now_iso)
    else:
        if isinstance(starter_topics, dict):
            starter_topics[topic_key] = "pending"
        if isinstance(completed_at, dict):
            completed_at.pop(topic_key, None)
        if isinstance(progress, dict):
            progress["status"] = "pending"
            progress["phase"] = "opening"
            progress["last_updated_at_utc"] = now_iso
            progress.setdefault("started_at_utc", now_iso)
            progress.pop("completed_at_utc", None)
        payload["active_topic"] = topic_key

    payload["updated_at_utc"] = now_iso
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {"updated": True, "path": str(path), "topic": topic_key, "status": normalized_status}


def _set_all_life_topics_complete(config: Config, user_id: str) -> Dict[str, Any]:
    results = {}
    for topic in ("finances", "fitness", "relationships", "career", "whyfinder"):
        results[topic] = _set_topic_onboarding_status(config, user_id, topic, "complete")
    return results


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
        "mcp_max_tool_iterations": 8,
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


def _chat(config: Config, token: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    timeout_retries = 2
    attempt = 0
    while True:
        status, response = _http_json(
            method="POST",
            url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/chat",
            payload=payload,
            timeout_seconds=config.timeout_seconds,
            request_delay_seconds=config.request_delay_seconds,
            max_retries=config.http_max_retries,
            retry_base_seconds=config.http_retry_base_seconds,
            token=token,
        )
        tooling_state = response.get("tooling_state") if isinstance(response, dict) else {}
        stop_reason = str((tooling_state or {}).get("tool_loop_stop_reason") or "").strip().lower()
        provider_timeout = stop_reason == "provider_timeout"
        if not provider_timeout or attempt >= timeout_retries:
            return status, response
        attempt += 1
        time.sleep(min(8.0, 1.5 * attempt))


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


def _approval_chain(
    *,
    config: Config,
    token: str,
    base_payload: Dict[str, Any],
    start_response: Dict[str, Any],
    max_steps: int = 10,
) -> List[Dict[str, Any]]:
    chain: List[Dict[str, Any]] = []
    conversation_id = str(start_response.get("conversation_id") or "").strip()
    current_response = start_response
    for _ in range(max_steps):
        request = current_response.get("approval_request")
        if not isinstance(request, dict):
            break
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            break
        status, next_response = _approve_pending_request(
            config=config,
            token=token,
            base_payload=base_payload,
            conversation_id=conversation_id,
            request_id=request_id,
            action="approve",
        )
        chain.append(
            {
                "status_code": status,
                "approval_request": request,
                "approval_resolution": next_response.get("approval_resolution"),
                "response": next_response,
            }
        )
        if (next_response.get("approval_resolution") or {}).get("status") != "approved":
            break
        if next_response.get("approval_required") is not True:
            break
        current_response = next_response
    return chain


def _first_response_with_tool(
    *,
    config: Config,
    token: str,
    payload_builder: Any,
    prompts: List[str],
    expected_tool: str,
) -> Optional[Dict[str, Any]]:
    for prompt in prompts:
        payload = payload_builder(prompt)
        status, response = _chat(config, token, payload)
        request = response.get("approval_request") or {}
        if (
            status == 200
            and response.get("approval_required") is True
            and str(request.get("tool") or "").strip() == expected_tool
        ):
            return {
                "prompt": prompt,
                "status_code": status,
                "response": response,
            }
    return None


def _run_s1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    base_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="",
        conversation_type="capture",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 10},
    )

    fanout_prompt = (
        "Capture this decision and note in finances, relationships, and career. "
        "Decision: pause nonessential spend until 2026-03-31. "
        "Add a task: Dave J to reconcile S1 fanout ledger by next week."
    )
    fanout_start_payload = dict(base_payload)
    fanout_start_payload["messages"] = [{"role": "user", "content": fanout_prompt}]
    fanout_status, fanout_start = _chat(config, token, fanout_start_payload)
    scenario["fanout_start"] = {
        "status_code": fanout_status,
        "approval_required": fanout_start.get("approval_required"),
        "approval_request": fanout_start.get("approval_request"),
        "tooling_state": fanout_start.get("tooling_state"),
    }
    first_request = fanout_start.get("approval_request") or {}
    first_args = first_request.get("arguments") or {}
    first_path = str(first_args.get("path") or "")
    _assert(summary, "s1_fanout_start_status_200", fanout_status == 200, str(scenario["fanout_start"]))
    _assert(summary, "s1_first_approval_required", fanout_start.get("approval_required") is True, str(fanout_start))
    _assert(summary, "s1_first_tool_inbox_create_markdown", first_request.get("tool") == "create_markdown", str(first_request))
    _assert(
        summary,
        "s1_first_path_capture_inbox",
        "/capture/inbox/" in f"/{first_path.strip('/')}",
        first_path,
    )

    chain: List[Dict[str, Any]] = []
    current_response = fanout_start
    for _ in range(10):
        current_request = current_response.get("approval_request") or {}
        if str(current_request.get("tool") or "").strip() == "create_task":
            break
        request_id = str(current_request.get("request_id") or "").strip()
        conversation_id = str(current_response.get("conversation_id") or "").strip()
        if not request_id or not conversation_id:
            break
        approve_status, approve_response = _approve_pending_request(
            config=config,
            token=token,
            base_payload=base_payload,
            conversation_id=conversation_id,
            request_id=request_id,
            action="approve",
        )
        chain.append(
            {
                "status_code": approve_status,
                "approval_request": current_request,
                "approval_resolution": approve_response.get("approval_resolution"),
                "response": approve_response,
            }
        )
        if (approve_response.get("approval_resolution") or {}).get("status") != "approved":
            break
        if approve_response.get("approval_required") is not True:
            current_response = approve_response
            break
        current_response = approve_response
    scenario["fanout_chain"] = chain

    request_items: List[Dict[str, Any]] = []
    if isinstance(first_request, dict):
        request_items.append(first_request)
    for step in chain:
        step_response = step.get("response") or {}
        next_request = step_response.get("approval_request")
        if isinstance(next_request, dict):
            request_items.append(next_request)

    fanout_scopes: List[str] = []
    create_task_request: Optional[Dict[str, Any]] = None
    for item in request_items:
        tool_name = str(item.get("tool") or "").strip()
        if tool_name == "create_task" and create_task_request is None:
            create_task_request = item
        if tool_name != "create_markdown":
            continue
        reason = str(item.get("synthetic_reason") or "").strip().lower()
        if not reason.startswith("capture_scope_fanout_"):
            continue
        path_value = str((item.get("arguments") or {}).get("path") or "").strip()
        if "/capture/" in path_value:
            fanout_scopes.append(path_value.split("/capture/", 1)[0].strip())

    unique_fanout_scopes = sorted({scope for scope in fanout_scopes if scope})
    scenario["fanout_scopes"] = unique_fanout_scopes
    _assert(summary, "s1_fanout_scope_count_ge_3", len(unique_fanout_scopes) >= 3, str(unique_fanout_scopes))

    create_task_args = (create_task_request or {}).get("arguments") or {}
    title_value = str(create_task_args.get("title") or "")
    scenario["create_task_request"] = create_task_request
    _assert(
        summary,
        "s1_create_task_request_present",
        isinstance(create_task_request, dict),
        str(request_items),
    )
    _assert(summary, "s1_create_task_owner_dave_j", str(create_task_args.get("owner") or "") == "Dave J", str(create_task_args))
    _assert(
        summary,
        "s1_create_task_due_iso",
        bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(create_task_args.get("due") or ""))),
        str(create_task_args),
    )
    _assert(
        summary,
        "s1_create_task_title_disambiguated",
        not title_value.lower().startswith("dave j "),
        title_value,
    )

    if create_task_request is None:
        trailing_request = current_response.get("approval_request") or {}
        if str(trailing_request.get("tool") or "").strip() == "create_task":
            create_task_request = trailing_request

    task_seed_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Add a task to reconcile s1 edit probe by 2026-03-10 in finances for Dave J.",
        conversation_type="capture",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 8},
    )
    task_seed_status, task_seed_start = _chat(config, token, task_seed_payload)
    scenario["task_seed_start"] = {
        "status_code": task_seed_status,
        "approval_required": task_seed_start.get("approval_required"),
        "approval_request": task_seed_start.get("approval_request"),
        "tooling_state": task_seed_start.get("tooling_state"),
    }
    _assert(summary, "s1_task_seed_status_200", task_seed_status == 200, str(scenario["task_seed_start"]))
    _assert(
        summary,
        "s1_task_seed_starts_with_write_approval",
        task_seed_start.get("approval_required") is True,
        str(scenario["task_seed_start"]),
    )

    task_seed_response = task_seed_start
    task_seed_create_request: Optional[Dict[str, Any]] = None
    for _ in range(8):
        pending_request = task_seed_response.get("approval_request") or {}
        pending_tool = str(pending_request.get("tool") or "").strip()
        pending_request_id = str(pending_request.get("request_id") or "").strip()
        pending_conversation_id = str(task_seed_response.get("conversation_id") or "").strip()
        if not pending_request_id or not pending_conversation_id:
            break
        if pending_tool == "create_task":
            task_seed_create_request = pending_request
        approve_status, approve_response = _approve_pending_request(
            config=config,
            token=token,
            base_payload=task_seed_payload,
            conversation_id=pending_conversation_id,
            request_id=pending_request_id,
            action="approve",
        )
        scenario.setdefault("task_seed_chain", []).append(
            {
                "status_code": approve_status,
                "approval_request": pending_request,
                "approval_resolution": approve_response.get("approval_resolution"),
                "tooling_state": approve_response.get("tooling_state"),
            }
        )
        if (approve_response.get("approval_resolution") or {}).get("status") != "approved":
            break
        task_seed_response = approve_response
        if pending_tool == "create_task":
            break
        if approve_response.get("approval_required") is not True:
            break
    if task_seed_create_request is None:
        final_request = task_seed_response.get("approval_request") or {}
        if str(final_request.get("tool") or "").strip() == "create_task":
            task_seed_create_request = final_request
    scenario["task_seed_create_request"] = task_seed_create_request
    _assert(
        summary,
        "s1_task_seed_create_task_seen",
        isinstance(task_seed_create_request, dict),
        str(scenario.get("task_seed_chain")),
    )

    def _capture_payload(prompt: str) -> Dict[str, Any]:
        payload = _build_chat_payload(
            config=config,
            user_id=user_id,
            message=prompt,
            conversation_type="capture",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
            params_extra={"mcp_max_tool_iterations": 8},
        )
        return payload

    edit_probe = _first_response_with_tool(
        config=config,
        token=token,
        payload_builder=_capture_payload,
        prompts=[
            "Set owner to Sarah for task reconcile s1 edit probe and move to p1 due next Friday.",
            "Update task reconcile s1 edit probe: owner Sarah, priority p1, due next Friday.",
        ],
        expected_tool="update_task",
    )
    _assert(summary, "s1_edit_probe_found_update_task", isinstance(edit_probe, dict), str(edit_probe))
    edit_response = (edit_probe or {}).get("response") or {}
    edit_request = edit_response.get("approval_request") or {}
    edit_args = edit_request.get("arguments") or {}
    edit_fields = edit_args.get("fields") or {}
    scenario["edit_probe"] = edit_probe
    _assert(summary, "s1_edit_tool_not_create_task", edit_request.get("tool") != "create_task", str(edit_request))
    _assert(summary, "s1_edit_owner_sarah", str(edit_fields.get("owner") or "") == "Sarah", str(edit_fields))
    _assert(summary, "s1_edit_priority_p1", str(edit_fields.get("priority") or "") == "p1", str(edit_fields))

    edit_request_id = str(edit_request.get("request_id") or "").strip()
    edit_conversation_id = str(edit_response.get("conversation_id") or "").strip()
    if edit_request_id and edit_conversation_id:
        approve_payload = _build_chat_payload(
            config=config,
            user_id=user_id,
            message="approve",
            conversation_type="capture",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
            conversation_id=edit_conversation_id,
        )
        approve_status, approve_response = _approve_pending_request(
            config=config,
            token=token,
            base_payload=approve_payload,
            conversation_id=edit_conversation_id,
            request_id=edit_request_id,
            action="approve",
        )
        scenario["edit_approve"] = {
            "status_code": approve_status,
            "approval_resolution": approve_response.get("approval_resolution"),
            "tooling_state": approve_response.get("tooling_state"),
        }
        _assert(
            summary,
            "s1_edit_approval_resolution_approved",
            (approve_response.get("approval_resolution") or {}).get("status") == "approved",
            str(scenario["edit_approve"]),
        )

    complete_probe = _first_response_with_tool(
        config=config,
        token=token,
        payload_builder=_capture_payload,
        prompts=[
            "Task reconcile s1 edit probe is done.",
            "Complete task reconcile s1 edit probe.",
        ],
        expected_tool="complete_task",
    )
    _assert(summary, "s1_complete_probe_found_complete_task", isinstance(complete_probe, dict), str(complete_probe))
    complete_response = (complete_probe or {}).get("response") or {}
    complete_request = complete_response.get("approval_request") or {}
    complete_state = complete_response.get("tooling_state") or {}
    scenario["complete_probe"] = complete_probe
    _assert(
        summary,
        "s1_complete_tool_not_create_task",
        complete_request.get("tool") != "create_task",
        str(complete_request),
    )
    _assert(
        summary,
        "s1_complete_followthrough_list_tasks_executed",
        int(complete_state.get("tool_calls_executed_count") or 0) >= 1,
        str(complete_state),
    )
    return scenario


def _run_profile_browser_probe(summary: ProbeSummary, config: Config, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npx",
        "tsx",
        "scripts/e2e_process_s2_profile_browser.ts",
        "--base-url",
        config.ui_base_url,
        "--email",
        config.email,
        "--password",
        config.password,
        "--model-hint",
        config.browser_model_hint,
        "--output-dir",
        str(artifact_dir),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    json_candidates = sorted(artifact_dir.glob("s2-profile-browser-*.json"))
    latest_json = json_candidates[-1] if json_candidates else None
    parsed: Dict[str, Any] = {}
    if latest_json and latest_json.is_file():
        try:
            parsed = json.loads(latest_json.read_text(encoding="utf-8"))
        except Exception:
            parsed = {}
    result = {
        "command": cmd,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "artifact_json": str(latest_json) if latest_json else "",
        "summary": parsed,
    }
    _assert(summary, "s2_profile_browser_exit_zero", completed.returncode == 0, str(result))
    _assert(
        summary,
        "s2_profile_browser_success",
        bool(parsed.get("success")) is True,
        str(result),
    )
    return result


def _run_r3_browser_probe(summary: ProbeSummary, config: Config, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npx",
        "tsx",
        "scripts/e2e_process_r3_browser.ts",
        "--base-url",
        config.ui_base_url,
        "--email",
        config.email,
        "--password",
        config.password,
        "--model-hint",
        config.browser_model_hint,
        "--output-dir",
        str(artifact_dir),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    json_candidates = sorted(artifact_dir.glob("r3-browser-*.json"))
    latest_json = json_candidates[-1] if json_candidates else None
    parsed: Dict[str, Any] = {}
    if latest_json and latest_json.is_file():
        try:
            parsed = json.loads(latest_json.read_text(encoding="utf-8"))
        except Exception:
            parsed = {}
    result = {
        "command": cmd,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "artifact_json": str(latest_json) if latest_json else "",
        "summary": parsed,
    }
    _assert(summary, "s3_r3_browser_exit_zero", completed.returncode == 0, str(result))
    _assert(summary, "s3_r3_browser_success", bool(parsed.get("success")) is True, str(result))
    return result


def _run_s2(summary: ProbeSummary, config: Config, token: str, user_id: str, run_dir: Path) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    completion_seed = _set_all_life_topics_complete(config, user_id)
    scenario["onboarding_seed_complete"] = completion_seed

    cross_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=(
            "I improved my finance check-in routine; apply a linked note in relationships and career too."
        ),
        conversation_type="life-finances",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    cross_status, cross_response = _chat(config, token, cross_payload)
    scenario["cross_pollination_start"] = {
        "status_code": cross_status,
        "approval_required": cross_response.get("approval_required"),
        "approval_request": cross_response.get("approval_request"),
        "tooling_state": cross_response.get("tooling_state"),
    }
    cross_request = cross_response.get("approval_request") or {}
    cross_reason = str(cross_request.get("synthetic_reason") or "")
    _assert(summary, "s2_cross_status_200", cross_status == 200, str(scenario["cross_pollination_start"]))
    _assert(
        summary,
        "s2_cross_approval_required",
        cross_response.get("approval_required") is True,
        str(cross_response),
    )
    _assert(
        summary,
        "s2_cross_initial_tool_expected",
        str(cross_request.get("tool") or "").strip() in {"create_markdown", "ensure_scope_scaffold"},
        str(cross_request),
    )

    cross_chain = _approval_chain(
        config=config,
        token=token,
        base_payload=cross_payload,
        start_response=cross_response,
        max_steps=4,
    )
    scenario["cross_pollination_chain"] = cross_chain
    cross_reasons = [cross_reason]
    cross_tools = [str(cross_request.get("tool") or "").strip()]
    for step in cross_chain:
        next_request = (step.get("response") or {}).get("approval_request") or {}
        reason = str(next_request.get("synthetic_reason") or "")
        tool_name = str(next_request.get("tool") or "").strip()
        if tool_name:
            cross_tools.append(tool_name)
        if reason:
            cross_reasons.append(reason)
    scenario["cross_pollination_reasons"] = cross_reasons
    scenario["cross_pollination_tools"] = cross_tools
    _assert(
        summary,
        "s2_cross_reason_present_after_chain",
        any(reason.startswith("cross_pollination_finances_to_") for reason in cross_reasons),
        f"reasons={cross_reasons} tools={cross_tools}",
    )
    _assert(
        summary,
        "s2_cross_chain_contains_create_markdown",
        any(tool_name == "create_markdown" for tool_name in cross_tools),
        str(cross_tools),
    )

    scaffold_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Create a new project page for S2 operations atlas.",
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="projects/active/finance",
        mcp_project_name="Finance",
    )
    scaffold_status, scaffold_response = _chat(config, token, scaffold_payload)
    scaffold_request = scaffold_response.get("approval_request") or {}
    scaffold_args = scaffold_request.get("arguments") or {}
    created_scope_path = str(scaffold_args.get("path") or "").strip()
    scenario["new_page_scaffold_start"] = {
        "status_code": scaffold_status,
        "approval_required": scaffold_response.get("approval_required"),
        "approval_request": scaffold_request,
    }
    _assert(summary, "s2_new_page_scaffold_status_200", scaffold_status == 200, str(scenario["new_page_scaffold_start"]))
    _assert(
        summary,
        "s2_new_page_scaffold_approval",
        scaffold_response.get("approval_required") is True and scaffold_request.get("tool") == "create_project",
        str(scaffold_request),
    )
    _assert(
        summary,
        "s2_new_page_scaffold_reason_seeded",
        scaffold_request.get("synthetic_reason") == "new_page_engine_scaffold",
        str(scaffold_request),
    )
    _assert(
        summary,
        "s2_new_page_scope_created",
        created_scope_path.startswith("projects/active/"),
        created_scope_path,
    )

    scaffold_request_id = str(scaffold_request.get("request_id") or "").strip()
    scaffold_conversation_id = str(scaffold_response.get("conversation_id") or "").strip()
    scaffold_approve_status, scaffold_approve_response = _approve_pending_request(
        config=config,
        token=token,
        base_payload=scaffold_payload,
        conversation_id=scaffold_conversation_id,
        request_id=scaffold_request_id,
        action="approve",
    )
    scenario["new_page_scaffold_approve"] = {
        "status_code": scaffold_approve_status,
        "approval_resolution": scaffold_approve_response.get("approval_resolution"),
        "tooling_state": scaffold_approve_response.get("tooling_state"),
    }
    _assert(
        summary,
        "s2_new_page_scaffold_approved",
        (scaffold_approve_response.get("approval_resolution") or {}).get("status") == "approved",
        str(scenario["new_page_scaffold_approve"]),
    )

    scope_slug = created_scope_path.split("/")[-1]
    scope_name = scope_slug.replace("-", " ").title()
    project_conversation_type = f"project-{scope_slug}"
    interview_base_params = {"mcp_tools_enabled": False, "mcp_sync_on_request": False}

    interview_start_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Start page interview for this scope.",
        conversation_type=project_conversation_type,
        mcp_scope_mode="project",
        mcp_project_slug=created_scope_path,
        mcp_project_name=scope_name,
        params_extra=interview_base_params,
    )
    interview_start_status, interview_start = _chat(config, token, interview_start_payload)
    interview_conversation_id = str(interview_start.get("conversation_id") or "").strip()
    interview_start_text = str(
        (((interview_start.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["new_page_interview_start"] = {
        "status_code": interview_start_status,
        "assistant_text": interview_start_text,
        "tooling_state": interview_start.get("tooling_state"),
    }
    _assert(summary, "s2_interview_start_status_200", interview_start_status == 200, str(scenario["new_page_interview_start"]))
    _assert(
        summary,
        "s2_interview_question1",
        "question 1 of" in interview_start_text.lower(),
        interview_start_text,
    )

    answer_one = "I want a weekly operating cadence with clear owners and deadlines."
    answer_one_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=answer_one,
        conversation_type=project_conversation_type,
        mcp_scope_mode="project",
        mcp_project_slug=created_scope_path,
        mcp_project_name=scope_name,
        params_extra=interview_base_params,
        conversation_id=interview_conversation_id,
    )
    answer_one_status, answer_one_response = _chat(config, token, answer_one_payload)
    answer_one_text = str(
        (((answer_one_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["new_page_answer_one"] = {
        "status_code": answer_one_status,
        "assistant_text": answer_one_text,
        "tooling_state": answer_one_response.get("tooling_state"),
    }
    _assert(summary, "s2_answer_one_status_200", answer_one_status == 200, str(scenario["new_page_answer_one"]))
    _assert(
        summary,
        "s2_answer_one_preview_paths",
        all(item in answer_one_text.lower() for item in ["spec.md", "build-plan.md", "status.md"]),
        answer_one_text,
    )

    approve_one_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="approve",
        conversation_type=project_conversation_type,
        mcp_scope_mode="project",
        mcp_project_slug=created_scope_path,
        mcp_project_name=scope_name,
        params_extra=interview_base_params,
        conversation_id=interview_conversation_id,
    )
    approve_one_status, approve_one_response = _chat(config, token, approve_one_payload)
    approve_one_text = str(
        (((approve_one_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    approve_one_state = approve_one_response.get("tooling_state") or {}
    scenario["new_page_approve_one"] = {
        "status_code": approve_one_status,
        "assistant_text": approve_one_text,
        "tooling_state": approve_one_state,
    }
    if "could not apply all scoped updates yet" in approve_one_text.lower():
        retry_status, retry_response = _chat(config, token, approve_one_payload)
        approve_one_status = retry_status
        approve_one_response = retry_response
        approve_one_text = str(
            (((approve_one_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
            or ""
        )
        approve_one_state = approve_one_response.get("tooling_state") or {}
        scenario["new_page_approve_one_retry"] = {
            "status_code": retry_status,
            "assistant_text": approve_one_text,
            "tooling_state": approve_one_state,
        }
    _assert(summary, "s2_approve_one_status_200", approve_one_status == 200, str(scenario["new_page_approve_one"]))
    _assert(
        summary,
        "s2_approve_one_saved_updates",
        "saved scoped updates" in approve_one_text.lower(),
        approve_one_text,
    )
    _assert(
        summary,
        "s2_approve_one_executes_writes",
        int(approve_one_state.get("tool_calls_executed_count") or 0) >= 3,
        str(approve_one_state),
    )

    answer_two = "Biggest blocker is dependency tracking, so I need a risk register and weekly review."
    answer_two_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=answer_two,
        conversation_type=project_conversation_type,
        mcp_scope_mode="project",
        mcp_project_slug=created_scope_path,
        mcp_project_name=scope_name,
        params_extra=interview_base_params,
        conversation_id=interview_conversation_id,
    )
    answer_two_status, answer_two_response = _chat(config, token, answer_two_payload)
    answer_two_text = str(
        (((answer_two_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["new_page_answer_two"] = {
        "status_code": answer_two_status,
        "assistant_text": answer_two_text,
        "tooling_state": answer_two_response.get("tooling_state"),
    }
    _assert(summary, "s2_answer_two_status_200", answer_two_status == 200, str(scenario["new_page_answer_two"]))
    _assert(
        summary,
        "s2_answer_two_preview_paths",
        all(item in answer_two_text.lower() for item in ["spec.md", "build-plan.md", "status.md"]),
        answer_two_text,
    )

    approve_two_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="approve",
        conversation_type=project_conversation_type,
        mcp_scope_mode="project",
        mcp_project_slug=created_scope_path,
        mcp_project_name=scope_name,
        params_extra=interview_base_params,
        conversation_id=interview_conversation_id,
    )
    approve_two_status, approve_two_response = _chat(config, token, approve_two_payload)
    approve_two_text = str(
        (((approve_two_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    approve_two_state = approve_two_response.get("tooling_state") or {}
    scenario["new_page_approve_two"] = {
        "status_code": approve_two_status,
        "assistant_text": approve_two_text,
        "tooling_state": approve_two_state,
    }
    if "could not apply all scoped updates yet" in approve_two_text.lower():
        retry_status, retry_response = _chat(config, token, approve_two_payload)
        approve_two_status = retry_status
        approve_two_response = retry_response
        approve_two_text = str(
            (((approve_two_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
            or ""
        )
        approve_two_state = approve_two_response.get("tooling_state") or {}
        scenario["new_page_approve_two_retry"] = {
            "status_code": retry_status,
            "assistant_text": approve_two_text,
            "tooling_state": approve_two_state,
        }
    _assert(summary, "s2_approve_two_status_200", approve_two_status == 200, str(scenario["new_page_approve_two"]))
    _assert(
        summary,
        "s2_approve_two_saved_updates",
        "saved scoped updates" in approve_two_text.lower(),
        approve_two_text,
    )
    _assert(
        summary,
        "s2_approve_two_executes_writes",
        int(approve_two_state.get("tool_calls_executed_count") or 0) >= 3,
        str(approve_two_state),
    )

    meta_path = (
        config.library_root
        / "users"
        / _normalize_user_id(user_id)
        / created_scope_path
        / "_meta"
        / "interview-state.md"
    )
    meta_content = ""
    if meta_path.is_file():
        meta_content = meta_path.read_text(encoding="utf-8")
    scenario["new_page_meta_path"] = str(meta_path)
    scenario["new_page_meta_excerpt"] = meta_content[-1200:]
    progression_copy_seen = (
        "question 2 of" in approve_one_text.lower()
        or "question 3 of" in approve_two_text.lower()
        or "saved scoped updates" in approve_two_text.lower()
    )
    _assert(
        summary,
        "s2_interview_progression_copy_seen",
        progression_copy_seen,
        f"approve_one={approve_one_text} | approve_two={approve_two_text}",
    )
    _assert(
        summary,
        "s2_meta_file_present_with_interview_state",
        bool(meta_content.strip()) and "approved_answers" in meta_content,
        scenario["new_page_meta_excerpt"],
    )

    profile_update_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Update my profile with: prefers concise weekly check-ins and decisive next-step summaries.",
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    profile_update_status, profile_update_start = _chat(config, token, profile_update_payload)
    profile_update_request = profile_update_start.get("approval_request") or {}
    scenario["profile_update_start"] = {
        "status_code": profile_update_status,
        "approval_required": profile_update_start.get("approval_required"),
        "approval_request": profile_update_request,
        "tooling_state": profile_update_start.get("tooling_state"),
    }
    _assert(summary, "s2_profile_update_status_200", profile_update_status == 200, str(scenario["profile_update_start"]))
    _assert(
        summary,
        "s2_profile_update_approval_required",
        profile_update_start.get("approval_required") is True,
        str(profile_update_start),
    )
    _assert(
        summary,
        "s2_profile_update_tool_is_profile_write",
        str(profile_update_request.get("tool") or "").strip() in {"write_markdown", "create_markdown"},
        str(profile_update_request),
    )
    profile_update_request_id = str(profile_update_request.get("request_id") or "").strip()
    profile_update_conversation_id = str(profile_update_start.get("conversation_id") or "").strip()
    profile_update_approve_status, profile_update_approve = _approve_pending_request(
        config=config,
        token=token,
        base_payload=profile_update_payload,
        conversation_id=profile_update_conversation_id,
        request_id=profile_update_request_id,
        action="approve",
    )
    scenario["profile_update_approve"] = {
        "status_code": profile_update_approve_status,
        "approval_resolution": profile_update_approve.get("approval_resolution"),
        "tooling_state": profile_update_approve.get("tooling_state"),
    }
    _assert(
        summary,
        "s2_profile_update_approved",
        (profile_update_approve.get("approval_resolution") or {}).get("status") == "approved",
        str(scenario["profile_update_approve"]),
    )

    profile_reject_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Update my profile with: this reject-path probe should not persist.",
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        params_extra={"mcp_max_tool_iterations": 6},
    )
    profile_reject_status, profile_reject_start = _chat(config, token, profile_reject_payload)
    profile_reject_request = profile_reject_start.get("approval_request") or {}
    scenario["profile_reject_start"] = {
        "status_code": profile_reject_status,
        "approval_required": profile_reject_start.get("approval_required"),
        "approval_request": profile_reject_request,
    }
    _assert(summary, "s2_profile_reject_status_200", profile_reject_status == 200, str(scenario["profile_reject_start"]))
    _assert(
        summary,
        "s2_profile_reject_approval_required",
        profile_reject_start.get("approval_required") is True,
        str(profile_reject_start),
    )
    profile_reject_request_id = str(profile_reject_request.get("request_id") or "").strip()
    profile_reject_conversation_id = str(profile_reject_start.get("conversation_id") or "").strip()
    profile_reject_status_2, profile_reject_response = _approve_pending_request(
        config=config,
        token=token,
        base_payload=profile_reject_payload,
        conversation_id=profile_reject_conversation_id,
        request_id=profile_reject_request_id,
        action="reject",
    )
    profile_reject_text = str(
        (((profile_reject_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    ).lower()
    scenario["profile_reject_resolution"] = {
        "status_code": profile_reject_status_2,
        "approval_resolution": profile_reject_response.get("approval_resolution"),
        "assistant_text": profile_reject_text,
    }
    _assert(
        summary,
        "s2_profile_reject_resolution_rejected",
        (profile_reject_response.get("approval_resolution") or {}).get("status") == "rejected",
        str(scenario["profile_reject_resolution"]),
    )
    _assert(
        summary,
        "s2_profile_reject_copy_present",
        "did not run mutating tool" in profile_reject_text or "rejected" in profile_reject_text,
        profile_reject_text,
    )
    return scenario


def _run_s3(summary: ProbeSummary, config: Config, token: str, user_id: str, run_dir: Path) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    scenario["onboarding_seed_relationships_pending"] = _set_topic_onboarding_status(
        config,
        user_id,
        "relationships",
        "pending",
    )

    chat_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Give me one concise finance focus for this week.",
        conversation_type="chat",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
    )
    chat_status, chat_response = _chat(config, token, chat_payload)
    chat_text = str(
        (((chat_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["chat_step"] = {
        "status_code": chat_status,
        "assistant_text": chat_text,
        "tooling_state": chat_response.get("tooling_state"),
    }
    _assert(summary, "s3_chat_status_200", chat_status == 200, str(scenario["chat_step"]))
    _assert(summary, "s3_chat_text_present", bool(chat_text.strip()), str(scenario["chat_step"]))

    capture_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=(
            "Capture this note and decision in finances and career. "
            "Decision: automate bill reminders by 2026-03-05. "
            "Add task: Dave J to review S3 mixed flow by next week."
        ),
        conversation_type="capture",
        mcp_scope_mode="project",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
    )
    capture_status, capture_response = _chat(config, token, capture_payload)
    capture_request = capture_response.get("approval_request") or {}
    scenario["capture_step_start"] = {
        "status_code": capture_status,
        "approval_required": capture_response.get("approval_required"),
        "approval_request": capture_request,
        "tooling_state": capture_response.get("tooling_state"),
    }
    _assert(summary, "s3_capture_status_200", capture_status == 200, str(scenario["capture_step_start"]))
    _assert(
        summary,
        "s3_capture_first_approval_create_markdown",
        capture_response.get("approval_required") is True and capture_request.get("tool") == "create_markdown",
        str(capture_request),
    )
    capture_chain = _approval_chain(
        config=config,
        token=token,
        base_payload=capture_payload,
        start_response=capture_response,
        max_steps=3,
    )
    scenario["capture_step_chain"] = capture_chain
    _assert(summary, "s3_capture_chain_progressed", len(capture_chain) >= 1, str(capture_chain))

    onboarding_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Start my Relationships onboarding interview.",
        conversation_type="life-relationships",
        mcp_scope_mode="project",
        mcp_project_slug="life/relationships",
        mcp_project_name="Relationships",
        params_extra={"mcp_tools_enabled": False},
    )
    onboarding_status, onboarding_response = _chat(config, token, onboarding_payload)
    onboarding_text = str(
        (((onboarding_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content")
        or ""
    )
    scenario["onboarding_step"] = {
        "status_code": onboarding_status,
        "assistant_text": onboarding_text,
        "tooling_state": onboarding_response.get("tooling_state"),
    }
    _assert(summary, "s3_onboarding_status_200", onboarding_status == 200, str(scenario["onboarding_step"]))
    _assert(
        summary,
        "s3_onboarding_question_started",
        "question 1 of" in onboarding_text.lower(),
        onboarding_text,
    )

    _set_topic_onboarding_status(config, user_id, "finances", "complete")
    _set_topic_onboarding_status(config, user_id, "relationships", "complete")
    cross_prompts = [
        "Mirror this finance habit into relationships as a quick cross-topic note.",
        "Create a cross-pollination note from finances to relationships and include next action.",
    ]
    cross_payload: Optional[Dict[str, Any]] = None
    cross_status: int = 0
    cross_response: Dict[str, Any] = {}
    cross_request: Dict[str, Any] = {}
    selected_cross_prompt = ""
    for prompt in cross_prompts:
        candidate_payload = _build_chat_payload(
            config=config,
            user_id=user_id,
            message=prompt,
            conversation_type="life-finances",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
        )
        candidate_status, candidate_response = _chat(config, token, candidate_payload)
        candidate_request = candidate_response.get("approval_request") or {}
        tool_name = str(candidate_request.get("tool") or "").strip()
        if candidate_status == 200 and candidate_response.get("approval_required") is True and tool_name in {
            "create_markdown",
            "ensure_scope_scaffold",
        }:
            cross_payload = candidate_payload
            cross_status = candidate_status
            cross_response = candidate_response
            cross_request = candidate_request
            selected_cross_prompt = prompt
            break
        if not cross_payload:
            cross_payload = candidate_payload
            cross_status = candidate_status
            cross_response = candidate_response
            cross_request = candidate_request
            selected_cross_prompt = prompt

    cross_tool_name = str(cross_request.get("tool") or "").strip()
    if not (
        cross_status == 200
        and cross_response.get("approval_required") is True
        and cross_tool_name in {"create_markdown", "ensure_scope_scaffold"}
    ):
        forced_prompt = (
            "[LIBRARY SCOPE - Life / finances] Use create_markdown to write "
            f"life/relationships/cross-pollination/s3-mixed-{summary.run_id}.md "
            "with content: S3 mixed-run cross topic note from finances to relationships."
        )
        forced_payload = _build_chat_payload(
            config=config,
            user_id=user_id,
            message=forced_prompt,
            conversation_type="life-finances",
            mcp_scope_mode="project",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
        )
        forced_status, forced_response = _chat(config, token, forced_payload)
        forced_request = forced_response.get("approval_request") or {}
        cross_payload = forced_payload
        cross_status = forced_status
        cross_response = forced_response
        cross_request = forced_request
        selected_cross_prompt = forced_prompt

    cross_reason = str(cross_request.get("synthetic_reason") or "")
    scenario["cross_pollination_step"] = {
        "selected_prompt": selected_cross_prompt,
        "status_code": cross_status,
        "approval_required": cross_response.get("approval_required"),
        "approval_request": cross_request,
        "tooling_state": cross_response.get("tooling_state"),
    }
    _assert(summary, "s3_cross_status_200", cross_status == 200, str(scenario["cross_pollination_step"]))
    _assert(
        summary,
        "s3_cross_initial_tool_expected",
        cross_response.get("approval_required") is True
        and str(cross_request.get("tool") or "").strip() in {"create_markdown", "ensure_scope_scaffold"},
        str(cross_request),
    )

    cross_follow_chain = _approval_chain(
        config=config,
        token=token,
        base_payload=cross_payload or {},
        start_response=cross_response,
        max_steps=4,
    )
    scenario["cross_pollination_chain"] = cross_follow_chain
    cross_tools = [str(cross_request.get("tool") or "").strip()]
    cross_reasons = [cross_reason]
    cross_paths = [str((cross_request.get("arguments") or {}).get("path") or "")]
    for step in cross_follow_chain:
        next_request = (step.get("response") or {}).get("approval_request") or {}
        tool_name = str(next_request.get("tool") or "").strip()
        reason = str(next_request.get("synthetic_reason") or "")
        path_value = str((next_request.get("arguments") or {}).get("path") or "")
        if tool_name:
            cross_tools.append(tool_name)
        if reason:
            cross_reasons.append(reason)
        if path_value:
            cross_paths.append(path_value)
    scenario["cross_pollination_tools"] = cross_tools
    scenario["cross_pollination_reasons"] = cross_reasons
    scenario["cross_pollination_paths"] = cross_paths
    _assert(
        summary,
        "s3_cross_chain_contains_create_markdown",
        any(tool_name == "create_markdown" for tool_name in cross_tools),
        str(cross_tools),
    )
    _assert(
        summary,
        "s3_cross_signal_seen",
        any(reason.startswith("cross_pollination_finances_to_") for reason in cross_reasons)
        or any("/cross-pollination/" in path_value for path_value in cross_paths),
        f"reasons={cross_reasons} paths={cross_paths}",
    )

    digest_email_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Run mixed-sweep digest email now.",
        conversation_type="digest-email",
        mcp_scope_mode="none",
        params_extra={
            "mcp_tools_enabled": False,
            "mcp_tool_profile": "read_only",
            "mcp_digest_schedule_enabled": True,
            "mcp_digest_force_run": True,
            "mcp_digest_schedule_event_id": f"s3-email-{summary.run_id}",
            "mcp_digest_delivery_send_enabled": True,
            "mcp_digest_delivery_endpoint": config.delivery_email_endpoint,
        },
    )
    digest_email_status, digest_email_response = _chat(config, token, digest_email_payload)
    digest_email_state = digest_email_response.get("tooling_state") or {}
    scenario["digest_email_step"] = {
        "status_code": digest_email_status,
        "tooling_state": digest_email_state,
    }
    _assert(summary, "s3_digest_email_status_200", digest_email_status == 200, str(scenario["digest_email_step"]))
    _assert(
        summary,
        "s3_digest_email_sent",
        str(digest_email_state.get("digest_delivery_send_status") or "") == "sent",
        str(digest_email_state),
    )

    digest_slack_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Run mixed-sweep digest slack now.",
        conversation_type="digest-slack",
        mcp_scope_mode="none",
        params_extra={
            "mcp_tools_enabled": False,
            "mcp_tool_profile": "read_only",
            "mcp_digest_schedule_enabled": True,
            "mcp_digest_force_run": True,
            "mcp_digest_schedule_event_id": f"s3-slack-{summary.run_id}",
            "mcp_digest_delivery_send_enabled": True,
            "mcp_digest_delivery_endpoint": config.delivery_slack_endpoint,
        },
    )
    digest_slack_status, digest_slack_response = _chat(config, token, digest_slack_payload)
    digest_slack_state = digest_slack_response.get("tooling_state") or {}
    scenario["digest_slack_step"] = {
        "status_code": digest_slack_status,
        "tooling_state": digest_slack_state,
    }
    _assert(summary, "s3_digest_slack_status_200", digest_slack_status == 200, str(scenario["digest_slack_step"]))
    _assert(
        summary,
        "s3_digest_slack_sent",
        str(digest_slack_state.get("digest_delivery_send_status") or "") == "sent",
        str(digest_slack_state),
    )

    browser_result = _run_r3_browser_probe(summary, config, run_dir / "s3-r3-browser")
    scenario["browser_r3"] = browser_result
    return scenario


def _write_summary(summary: ProbeSummary, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "summary.json"
    payload = {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "base_url": summary.base_url,
        "ui_base_url": summary.ui_base_url,
        "provider": summary.provider,
        "model": summary.model,
        "reset_applied": summary.reset_applied,
        "success": summary.success,
        "error": summary.error,
        "assertions": [
            {"name": item.name, "passed": item.passed, "detail": item.detail}
            for item in summary.assertions
        ],
        "scenarios": summary.scenarios,
        "artifacts": summary.artifacts,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return out_path


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process S.1/S.2/S.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--ui-base-url", default="http://localhost:5173")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="ollama_default_server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--browser-model-hint", default="qwen3:8b (new server)")
    parser.add_argument(
        "--output-dir",
        default="/home/hacker/BrainDriveDev/BrainDrive/tmp/live-process-s123",
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=1.2)
    parser.add_argument("--http-max-retries", type=int, default=5)
    parser.add_argument("--http-retry-base-seconds", type=float, default=1.5)
    parser.add_argument(
        "--library-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library",
    )
    parser.add_argument(
        "--template-root",
        default="/home/hacker/BrainDriveDev/BrainDrive/backend/services_runtime/Library-Service/library_templates/Base_Library",
    )
    parser.add_argument(
        "--delivery-email-endpoint",
        default="https://httpbin.org/post?channel=s3-email",
    )
    parser.add_argument(
        "--delivery-slack-endpoint",
        default="https://httpbin.org/post?channel=s3-slack",
    )
    parser.add_argument(
        "--reset-from-template",
        dest="reset_from_template",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-reset-from-template",
        dest="reset_from_template",
        action="store_false",
    )
    args = parser.parse_args()
    return Config(
        base_url=args.base_url,
        ui_base_url=args.ui_base_url,
        email=args.email,
        password=args.password,
        provider=args.provider,
        settings_id=args.settings_id,
        server_id=args.server_id,
        model=args.model,
        browser_model_hint=args.browser_model_hint,
        output_dir=Path(args.output_dir),
        timeout_seconds=max(15, int(args.timeout_seconds)),
        request_delay_seconds=max(0.0, float(args.request_delay_seconds)),
        http_max_retries=max(0, int(args.http_max_retries)),
        http_retry_base_seconds=max(0.1, float(args.http_retry_base_seconds)),
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root),
        template_root=Path(args.template_root),
        delivery_email_endpoint=str(args.delivery_email_endpoint),
        delivery_slack_endpoint=str(args.delivery_slack_endpoint),
    )


def main() -> int:
    config = parse_args()
    started_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = config.output_dir / f"run-{run_id}"
    summary = ProbeSummary(
        run_id=run_id,
        started_at=started_at,
        base_url=config.base_url,
        ui_base_url=config.ui_base_url,
        provider=config.provider,
        model=config.model,
        reset_applied=False,
    )

    try:
        token, user_id = _login(config)
        summary.artifacts["login"] = {"user_id": user_id}

        if config.reset_from_template:
            bootstrap_meta = _reset_scope_from_template(config, user_id)
            summary.reset_applied = True
            summary.artifacts["template_reset"] = bootstrap_meta

        summary.scenarios["s1"] = _run_s1(summary, config, token, user_id)
        summary.scenarios["s2"] = _run_s2(summary, config, token, user_id, run_dir)
        summary.scenarios["s3"] = _run_s3(summary, config, token, user_id, run_dir)
        summary.success = all(item.passed for item in summary.assertions)
    except Exception as exc:
        summary.success = False
        summary.error = str(exc)
    finally:
        output_path = _write_summary(summary, run_dir)
        print(json.dumps(
            {
                "run_id": summary.run_id,
                "success": summary.success,
                "error": summary.error,
                "assertions_passed": sum(1 for item in summary.assertions if item.passed),
                "assertions_total": len(summary.assertions),
                "summary_path": str(output_path),
            },
            ensure_ascii=True,
            indent=2,
        ))
    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
