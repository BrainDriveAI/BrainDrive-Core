#!/usr/bin/env python3
"""Targeted parity probe: Library Capture vs Finances page task flow.

Flow per provider and per page:
1) Ask to create task: "I need to pay back my friend 20 dollars by Feb 20 2026."
2) Verify mutating approval appears and approve through pending approvals.
3) Verify create_task approval payload quality.
4) Ask as a user if the task exists and verify retrieval response.

Runs local (default qwen3:8b) and OpenRouter by default.
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


APPROVAL_REQUIRED_TEXT = (
    "Approval required before executing mutating tool call. "
    "Reply `approve` to continue or `reject` to cancel."
)
CREATE_PROMPT = "I need to pay back my friend 20 dollars by Feb 20 2026."
CREATE_CONFIRM_PROMPT = "Yes, please create that task now with the same details."
VERIFY_PROMPT = (
    "Use the list_tasks tool to check my open tasks. Do I already have any task "
    "for paying back my friend 20 dollars ($20) by 2026-02-20? "
    "Lookup only: do not create, update, or modify tasks. "
    "If yes, list each match with task id, title, due date, owner, and scope."
)
VERIFY_FALLBACK_PROMPT = (
    "Use the list_tasks tool and list my open tasks with task id, title, due date, "
    "owner, and scope. This is lookup only; do not create, update, or modify tasks."
)


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ProviderTarget:
    label: str
    provider: str
    settings_id: str
    server_id: str
    model: str


@dataclass
class Config:
    base_url: str
    email: str
    password: str
    local_provider: str
    local_settings_id: str
    local_server_id: str
    local_model: str
    openrouter_settings_id: str
    openrouter_server_id: str
    openrouter_model: str
    skip_local: bool
    skip_openrouter: bool
    output_dir: Path
    timeout_seconds: int
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float
    reset_from_template: bool
    library_root: Path
    template_root: Path


@dataclass
class ProbeSummary:
    run_id: str
    started_at: str
    base_url: str
    reset_applied: bool
    assertions: List[AssertionResult] = field(default_factory=list)
    scenarios: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error: Optional[str] = None


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
        req = urllib_request.Request(url=url, data=body, headers=headers, method=method.upper())
        try:
            _throttle(request_delay_seconds)
            with urllib_request.urlopen(req, timeout=timeout_seconds) as response:
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


def _assistant_text(response: Dict[str, Any]) -> str:
    try:
        return str(((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except Exception:
        return ""


def _build_chat_payload(
    *,
    target: ProviderTarget,
    user_id: str,
    message: str,
    conversation_type: str,
    page_id: str,
    mcp_scope_mode: str = "none",
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "mcp_tools_enabled": True,
        "mcp_scope_mode": mcp_scope_mode,
        "mcp_sync_on_request": False,
        "mcp_auto_approve_mutating": False,
        "mcp_max_tool_iterations": 8,
        "mcp_provider_timeout_seconds": 90,
        "temperature": 0,
    }
    payload: Dict[str, Any] = {
        "provider": target.provider,
        "settings_id": target.settings_id,
        "server_id": target.server_id,
        "model": target.model,
        "messages": [{"role": "user", "content": message}],
        "user_id": user_id,
        "conversation_type": conversation_type,
        "page_id": page_id,
        "params": params,
        "stream": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload


def _approve_pending_request(
    *,
    config: Config,
    token: str,
    base_payload: Dict[str, Any],
    conversation_id: str,
    request_id: str,
) -> Tuple[int, Dict[str, Any]]:
    payload = dict(base_payload)
    payload["conversation_id"] = conversation_id
    payload["messages"] = [{"role": "user", "content": "approve"}]
    params = dict(payload.get("params") or {})
    params["mcp_approval"] = {"action": "approve", "request_id": request_id}
    payload["params"] = params
    return _chat(config, token, payload)


def _compact_request(request_obj: Dict[str, Any]) -> Dict[str, Any]:
    args = request_obj.get("arguments") if isinstance(request_obj, dict) else {}
    compact_args: Dict[str, Any] = {}
    if isinstance(args, dict):
        for key in ("path", "title", "owner", "due", "scope", "id"):
            if key in args:
                compact_args[key] = args.get(key)
    return {
        "tool": request_obj.get("tool"),
        "request_id": request_obj.get("request_id"),
        "synthetic_reason": request_obj.get("synthetic_reason"),
        "summary": request_obj.get("summary"),
        "arguments": compact_args,
    }


def _run_page_flow(
    *,
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
    target: ProviderTarget,
    page_label: str,
    conversation_type: str,
    page_id: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "page_label": page_label,
        "conversation_type": conversation_type,
        "page_id": page_id,
    }

    create_payload = _build_chat_payload(
        target=target,
        user_id=user_id,
        message=CREATE_PROMPT,
        conversation_type=conversation_type,
        page_id=page_id,
    )
    start_status, start_response = _chat(config, token, create_payload)
    if start_status == 404 and str(target.provider or "").strip() == "ollama":
        detail_text = str((start_response or {}).get("detail") or "")
        server_match = re.search(r"ID:\s*([A-Za-z0-9_-]+)", detail_text)
        if server_match:
            target.server_id = server_match.group(1)
            create_payload["server_id"] = target.server_id
            start_status, start_response = _chat(config, token, create_payload)
    def _record_create_start(status_code: int, response_obj: Dict[str, Any], assistant_text: str) -> Dict[str, Any]:
        return {
            "status_code": status_code,
            "approval_required": response_obj.get("approval_required"),
            "approval_request": _compact_request(response_obj.get("approval_request") or {}),
            "assistant_text_excerpt": assistant_text[:400],
            "conversation_id": response_obj.get("conversation_id"),
            "tooling_state": response_obj.get("tooling_state"),
        }

    start_text = _assistant_text(start_response)
    initial_conversation_id = str(start_response.get("conversation_id") or "").strip()
    if (
        start_status == 200
        and start_response.get("approval_required") is not True
        and bool(initial_conversation_id)
    ):
        result["create_start_initial"] = _record_create_start(
            start_status,
            start_response,
            start_text,
        )
        create_confirm_payload = _build_chat_payload(
            target=target,
            user_id=user_id,
            message=CREATE_CONFIRM_PROMPT,
            conversation_type=conversation_type,
            page_id=page_id,
            conversation_id=initial_conversation_id,
        )
        confirm_status, confirm_response = _chat(config, token, create_confirm_payload)
        confirm_text = _assistant_text(confirm_response)
        result["create_start_confirmation"] = _record_create_start(
            confirm_status,
            confirm_response,
            confirm_text,
        )
        if confirm_status == 200:
            start_status = confirm_status
            start_response = confirm_response
            start_text = confirm_text

    result["create_start"] = _record_create_start(start_status, start_response, start_text)
    _assert(summary, f"{target.label}_{page_label}_create_status_200", start_status == 200, str(result["create_start"]))
    _assert(
        summary,
        f"{target.label}_{page_label}_create_approval_required",
        start_response.get("approval_required") is True,
        str(result["create_start"]),
    )
    _assert(
        summary,
        f"{target.label}_{page_label}_approval_copy_locked",
        start_text == APPROVAL_REQUIRED_TEXT,
        start_text,
    )

    conversation_id = str(start_response.get("conversation_id") or initial_conversation_id).strip()
    _assert(
        summary,
        f"{target.label}_{page_label}_conversation_id_present",
        bool(conversation_id),
        str(start_response),
    )

    approval_chain: List[Dict[str, Any]] = []
    seen_requests: List[Dict[str, Any]] = []
    if isinstance(start_response.get("approval_request"), dict):
        seen_requests.append(start_response["approval_request"])

    current_response = start_response
    for _ in range(12):
        current_request = current_response.get("approval_request")
        if not isinstance(current_request, dict):
            break
        request_id = str(current_request.get("request_id") or "").strip()
        if not request_id:
            break
        approve_status, approve_response = _approve_pending_request(
            config=config,
            token=token,
            base_payload=create_payload,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        approval_chain.append(
            {
                "status_code": approve_status,
                "request": _compact_request(current_request),
                "approval_resolution": approve_response.get("approval_resolution"),
                "approval_required_next": approve_response.get("approval_required"),
                "assistant_text_excerpt": _assistant_text(approve_response)[:300],
                "tooling_state": approve_response.get("tooling_state"),
            }
        )
        _assert(
            summary,
            f"{target.label}_{page_label}_approve_status_200_step_{len(approval_chain)}",
            approve_status == 200,
            str(approval_chain[-1]),
        )
        _assert(
            summary,
            f"{target.label}_{page_label}_approve_resolution_step_{len(approval_chain)}",
            str((approve_response.get("approval_resolution") or {}).get("status") or "") == "approved",
            str(approval_chain[-1]),
        )
        next_request = approve_response.get("approval_request")
        if isinstance(next_request, dict):
            seen_requests.append(next_request)
        current_response = approve_response
        if approve_response.get("approval_required") is not True:
            break

    result["approval_chain"] = approval_chain
    result["approval_request_sequence"] = [_compact_request(item) for item in seen_requests]

    create_task_request: Optional[Dict[str, Any]] = None
    for item in seen_requests:
        if str(item.get("tool") or "").strip() == "create_task":
            create_task_request = item
            break
    result["create_task_request"] = _compact_request(create_task_request or {})
    _assert(
        summary,
        f"{target.label}_{page_label}_create_task_request_seen",
        isinstance(create_task_request, dict),
        str(result["approval_request_sequence"]),
    )

    create_args = (create_task_request or {}).get("arguments") or {}
    title = str(create_args.get("title") or "")
    owner_raw = str(create_args.get("owner") or "").strip()
    due = str(create_args.get("due") or "").strip()
    scope_raw = str(create_args.get("scope") or "").strip()

    scope_normalized = scope_raw.lower()
    if scope_normalized in {"finance", "finances", "personal finance", "life/finance"}:
        scope_normalized = "life/finances"
    if not scope_normalized and page_label == "life_finances":
        scope_normalized = "life/finances"

    owner_effective = owner_raw
    if not owner_effective and page_label == "life_finances":
        owner_effective = "user"
    if owner_effective.lower() in {"me", "myself", "self", "user", "you"}:
        owner_effective = "user"

    result["create_task_args"] = {
        "title": title,
        "owner": owner_effective,
        "owner_raw": owner_raw,
        "due": due,
        "scope": scope_normalized,
        "scope_raw": scope_raw,
    }
    title_lower = title.lower()
    _assert(summary, f"{target.label}_{page_label}_create_due_exact", due == "2026-02-20", str(result["create_task_args"]))
    _assert(summary, f"{target.label}_{page_label}_create_scope_finances", scope_normalized == "life/finances", str(result["create_task_args"]))
    _assert(summary, f"{target.label}_{page_label}_create_owner_present", bool(owner_effective), str(result["create_task_args"]))
    _assert(
        summary,
        f"{target.label}_{page_label}_create_title_semantics",
        ("friend" in title_lower) and ("20" in title_lower) and ("pay" in title_lower),
        title,
    )

    verify_payload = _build_chat_payload(
        target=target,
        user_id=user_id,
        message=VERIFY_PROMPT,
        conversation_type=conversation_type,
        page_id=page_id,
        conversation_id=conversation_id,
    )
    verify_status, verify_response = _chat(config, token, verify_payload)
    verify_text = _assistant_text(verify_response)
    verify_state = verify_response.get("tooling_state") or {}
    result["verify"] = {
        "status_code": verify_status,
        "approval_required": verify_response.get("approval_required"),
        "assistant_text_excerpt": verify_text[:700],
        "tooling_state": verify_state,
    }
    _assert(summary, f"{target.label}_{page_label}_verify_status_200", verify_status == 200, str(result["verify"]))
    _assert(
        summary,
        f"{target.label}_{page_label}_verify_no_approval",
        verify_response.get("approval_required") is not True,
        str(result["verify"]),
    )

    verify_tool_calls_executed = int(verify_state.get("tool_calls_executed_count") or 0)
    verify_stop_reason = str(verify_state.get("tool_loop_stop_reason") or "").strip().lower()
    verify_timeout_count = int(verify_state.get("provider_timeout_count") or 0)

    if (
        verify_status == 200
        and verify_response.get("approval_required") is not True
        and verify_tool_calls_executed < 1
        and (verify_stop_reason == "provider_timeout" or verify_timeout_count > 0)
    ):
        retry_status, retry_response = _chat(config, token, verify_payload)
        retry_text = _assistant_text(retry_response)
        retry_state = retry_response.get("tooling_state") or {}
        result["verify_retry"] = {
            "status_code": retry_status,
            "approval_required": retry_response.get("approval_required"),
            "assistant_text_excerpt": retry_text[:700],
            "tooling_state": retry_state,
        }
        if retry_status == 200 and retry_response.get("approval_required") is not True:
            verify_status = retry_status
            verify_response = retry_response
            verify_text = retry_text
            verify_state = retry_state
            verify_tool_calls_executed = int(verify_state.get("tool_calls_executed_count") or 0)

    def _analyze_verify_text(candidate: str) -> Tuple[bool, bool, bool]:
        lowered = candidate.lower()
        due_flag = bool(
            ("2026-02-20" in candidate)
            or re.search(r"\bfeb(?:ruary)?\b", lowered)
            and re.search(r"\b20\b", lowered)
            and ("2026" in lowered)
        )
        semantics_flag = bool(
            (
                ("20" in lowered)
                and (
                    ("friend" in lowered)
                    or ("pay back" in lowered)
                    or ("paying back" in lowered)
                    or ("pay" in lowered and "back" in lowered)
                )
            )
            or ("matching task" in lowered)
            or ("matching tasks" in lowered)
            or ("task is titled" in lowered)
        )
        task_id_flag = bool(
            re.search(r"\bT-\d+\b", candidate)
            or re.search(r"\btask\s*id\b[^0-9]{0,12}\d+\b", candidate, flags=re.IGNORECASE)
            or re.search(r"\bID\s*[:#-]?\s*\d+\b", candidate, flags=re.IGNORECASE)
            or re.search(r"\btask\s+\d+\b", candidate, flags=re.IGNORECASE)
        )
        return due_flag, semantics_flag, task_id_flag

    verify_text_for_assert = verify_text
    due_mentioned, task_semantics_mentioned, task_id_mentioned = _analyze_verify_text(verify_text_for_assert)
    fallback_tool_calls_executed = 0

    if (
        verify_status == 200
        and verify_response.get("approval_required") is not True
        and (
            verify_tool_calls_executed < 1
            or not due_mentioned
            or not task_semantics_mentioned
            or not task_id_mentioned
        )
    ):
        fallback_payload = _build_chat_payload(
            target=target,
            user_id=user_id,
            message=VERIFY_FALLBACK_PROMPT,
            conversation_type=conversation_type,
            page_id=page_id,
            conversation_id=conversation_id,
        )
        fallback_status, fallback_response = _chat(config, token, fallback_payload)
        fallback_text = _assistant_text(fallback_response)
        fallback_state = fallback_response.get("tooling_state") or {}
        result["verify_fallback"] = {
            "status_code": fallback_status,
            "approval_required": fallback_response.get("approval_required"),
            "assistant_text_excerpt": fallback_text[:700],
            "tooling_state": fallback_state,
        }
        fallback_tool_calls_executed = int(fallback_state.get("tool_calls_executed_count") or 0)
        if fallback_status == 200 and fallback_response.get("approval_required") is not True:
            verify_text_for_assert = "\n".join(
                part for part in [verify_text_for_assert, fallback_text] if part
            ).strip()
            due_mentioned, task_semantics_mentioned, task_id_mentioned = _analyze_verify_text(
                verify_text_for_assert
            )

    _assert(
        summary,
        f"{target.label}_{page_label}_verify_tool_call_executed",
        max(verify_tool_calls_executed, fallback_tool_calls_executed) >= 1,
        str({
            "verify": verify_state,
            "verify_fallback": result.get("verify_fallback"),
        }),
    )

    _assert(
        summary,
        f"{target.label}_{page_label}_verify_mentions_due",
        due_mentioned,
        verify_text_for_assert,
    )
    _assert(
        summary,
        f"{target.label}_{page_label}_verify_mentions_task_semantics",
        task_semantics_mentioned,
        verify_text_for_assert,
    )
    _assert(
        summary,
        f"{target.label}_{page_label}_verify_mentions_task_id",
        task_id_mentioned,
        verify_text_for_assert,
    )

    return result


def _read_pulse_index(config: Config, user_id: str) -> str:
    normalized_user_id = _normalize_user_id(user_id)
    pulse_path = config.library_root / "users" / normalized_user_id / "pulse" / "index.md"
    if not pulse_path.is_file():
        return ""
    return pulse_path.read_text(encoding="utf-8")


def _run_provider_flow(
    *,
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
    target: ProviderTarget,
) -> Dict[str, Any]:
    provider_result: Dict[str, Any] = {
        "provider": target.provider,
        "settings_id": target.settings_id,
        "server_id": target.server_id,
        "model": target.model,
    }

    run_tag = summary.run_id.lower()
    page_results: Dict[str, Any] = {}
    for page_label, conversation_type in (
        ("capture", "capture"),
        ("life_finances", "life-finances"),
    ):
        page_id = f"task-parity-{target.label}-{page_label}-{run_tag}"
        page_results[page_label] = _run_page_flow(
            summary=summary,
            config=config,
            token=token,
            user_id=user_id,
            target=target,
            page_label=page_label,
            conversation_type=conversation_type,
            page_id=page_id,
        )
    provider_result["pages"] = page_results

    capture_args = page_results["capture"]["create_task_args"]
    finance_args = page_results["life_finances"]["create_task_args"]
    parity = {
        "due_match": capture_args.get("due") == finance_args.get("due"),
        "scope_match": capture_args.get("scope") == finance_args.get("scope"),
        "owner_match": capture_args.get("owner") == finance_args.get("owner"),
        "capture_args": capture_args,
        "life_finances_args": finance_args,
    }
    provider_result["parity"] = parity
    _assert(summary, f"{target.label}_parity_due_match", bool(parity["due_match"]), str(parity))
    _assert(summary, f"{target.label}_parity_scope_match", bool(parity["scope_match"]), str(parity))
    _assert(summary, f"{target.label}_parity_owner_match", bool(parity["owner_match"]), str(parity))

    pulse_content = _read_pulse_index(config, user_id)
    provider_result["pulse_index_excerpt"] = pulse_content[-1800:]
    pulse_lower = pulse_content.lower()
    _assert(
        summary,
        f"{target.label}_pulse_has_payback_task",
        ("pay" in pulse_lower) and ("friend" in pulse_lower) and ("20" in pulse_lower) and ("due:2026-02-20" in pulse_lower),
        pulse_content[-1200:],
    )

    return provider_result


def _serialize_summary(summary: ProbeSummary) -> Dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "base_url": summary.base_url,
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
    parser = argparse.ArgumentParser(description="Capture vs finances task approval parity probe")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)

    parser.add_argument("--local-provider", default="ollama")
    parser.add_argument("--local-settings-id", default="ollama_servers_settings")
    parser.add_argument("--local-server-id", default="ollama_default_server")
    parser.add_argument("--local-model", default="qwen3:8b")

    parser.add_argument("--openrouter-settings-id", default="openrouter_api_keys_settings")
    parser.add_argument("--openrouter-server-id", default="openrouter_default_server")
    parser.add_argument("--openrouter-model", default="openai/gpt-4o-mini-2024-11-20")

    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--skip-openrouter", action="store_true")

    parser.add_argument("--output-dir", default="tmp/live-task-parity")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=0.8)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=1.5)
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
        local_provider=str(args.local_provider),
        local_settings_id=str(args.local_settings_id),
        local_server_id=str(args.local_server_id),
        local_model=str(args.local_model),
        openrouter_settings_id=str(args.openrouter_settings_id),
        openrouter_server_id=str(args.openrouter_server_id),
        openrouter_model=str(args.openrouter_model),
        skip_local=bool(args.skip_local),
        skip_openrouter=bool(args.skip_openrouter),
        output_dir=Path(args.output_dir).resolve(),
        timeout_seconds=max(10, int(args.timeout_seconds)),
        request_delay_seconds=max(0.0, float(args.request_delay_seconds)),
        http_max_retries=max(0, int(args.http_max_retries)),
        http_retry_base_seconds=max(0.1, float(args.http_retry_base_seconds)),
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root).resolve(),
        template_root=Path(args.template_root).resolve(),
    )


def main(argv: Optional[List[str]] = None) -> int:
    config = _parse_args(argv)
    if config.skip_local and config.skip_openrouter:
        raise SystemExit("At least one provider target must run (remove one skip flag).")

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    run_dir = config.output_dir / f"run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = ProbeSummary(
        run_id=run_id,
        started_at=started_at,
        base_url=config.base_url,
        reset_applied=False,
    )

    targets: List[ProviderTarget] = []
    if not config.skip_local:
        targets.append(
            ProviderTarget(
                label="local",
                provider=config.local_provider,
                settings_id=config.local_settings_id,
                server_id=config.local_server_id,
                model=config.local_model,
            )
        )
    if not config.skip_openrouter:
        targets.append(
            ProviderTarget(
                label="openrouter",
                provider="openrouter",
                settings_id=config.openrouter_settings_id,
                server_id=config.openrouter_server_id,
                model=config.openrouter_model,
            )
        )

    try:
        token, user_id = _login(config)
        summary.scenarios["auth"] = {"user_id": user_id}

        if config.reset_from_template:
            bootstrap = _reset_scope_from_template(config, user_id)
            summary.reset_applied = True
            summary.scenarios["bootstrap"] = bootstrap

        summary.scenarios["finances_onboarding_seed"] = _seed_finances_onboarding_complete(config, user_id)

        provider_results: Dict[str, Any] = {}
        for target in targets:
            try:
                provider_results[target.label] = _run_provider_flow(
                    summary=summary,
                    config=config,
                    token=token,
                    user_id=user_id,
                    target=target,
                )
            except Exception as exc:
                provider_results[target.label] = {
                    "provider": target.provider,
                    "settings_id": target.settings_id,
                    "server_id": target.server_id,
                    "model": target.model,
                    "error": str(exc),
                }
                summary.assertions.append(
                    AssertionResult(
                        name=f"{target.label}_provider_flow",
                        passed=False,
                        detail=str(exc),
                    )
                )
        summary.scenarios["providers"] = provider_results
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
