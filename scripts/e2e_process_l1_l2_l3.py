#!/usr/bin/env python3
"""Live probe harness for Process L.1, L.2, and L.3.

L.1: VS-5/VS-6/TR-1 final multi-op edit acceptance (compound + correction flows).
L.2: VS-8/VS-11 focused page parity + new-page archetype expansion checks.
L.3: TR-3/TR-5/VS-4 capability-policy + citation acceptance expansion checks.
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
from typing import Any, Callable, Dict, List, Optional, Tuple
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


def _decode_json_bytes(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    except Exception:
        return {"raw": text}


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
                return _decode_json_bytes(response.read())
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


def _http_post_with_status(
    *,
    url: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
    request_delay_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    token: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
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
                return int(response.status), _decode_json_bytes(response.read())
        except urllib_error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            return int(exc.code), _decode_json_bytes(exc.read())
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


def _chat_with_status(
    config: Config,
    token: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    return _http_post_with_status(
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/chat",
        payload=payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )


def _attempt_routing_probe(
    *,
    summary: ProbeSummary,
    config: Config,
    token: str,
    user_id: str,
    conversation_type: str,
    scope_mode: str,
    project_slug: Optional[str],
    project_name: Optional[str],
    prompts: List[str],
    params_extra: Optional[Dict[str, Any]],
    predicate: Callable[[Dict[str, Any]], bool],
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
        attempts.append(
            {
                "prompt": prompt,
                "tooling_state": tooling,
                "approval_required": response.get("approval_required"),
            }
        )
        if predicate(tooling):
            return {"passed": True, "attempts": attempts, "accepted_state": tooling}
    return {"passed": False, "attempts": attempts}


def _attempt_citation_probe(
    *,
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
    citation_path_pattern = re.compile(
        r"(?:^|[\s(\\[])(?:life|projects|me|capture)/[A-Za-z0-9_./-]+\.md(?:$|[\s)\\]])"
    )
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
        lower_text = text.lower()
        has_sources_block = (
            "sources:" in lower_text
            or "\n## sources" in lower_text
            or "\n### sources" in lower_text
            or lower_text.startswith("sources\n")
        )
        has_citation_meta = isinstance(citations, list) and len(citations) > 0
        has_path_citations = bool(citation_path_pattern.search(text))
        attempt = {
            "prompt": prompt,
            "tooling_state": tooling,
            "has_sources_block": has_sources_block,
            "has_citation_meta": has_citation_meta,
            "has_path_citations": has_path_citations,
            "citation_count": len(citations) if isinstance(citations, list) else 0,
            "assistant_text": text,
        }
        attempts.append(attempt)
        if has_sources_block or has_citation_meta or has_path_citations:
            return {"accepted": True, "attempts": attempts}
    return {"accepted": False, "attempts": attempts}


def _run_l1(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    scope_path = "projects/active/side-business"
    scope_name = "Side Business"
    conversation_type = "project-side-business"
    marker = f"L1 marker {summary.run_id}"

    first_prompt = (
        'Edit spec.md, build-plan.md, and status.md for side business. '
        f'Add "{marker} spec", "{marker} plan", and "{marker} status".'
    )

    first_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message=first_prompt,
            conversation_type=conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=scope_path,
            mcp_project_name=scope_name,
            params_extra={"mcp_native_tool_calling": True},
        ),
    )
    first_request = first_start.get("approval_request") or {}
    first_preview = first_request.get("preview") or {}
    first_text = str((((first_start.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or "")

    conversation_id = first_start.get("conversation_id")
    request_id = first_request.get("request_id")
    scenario["first_compound_start"] = {
        "approval_required": first_start.get("approval_required"),
        "approval_request": first_request,
        "tooling_state": first_start.get("tooling_state"),
        "assistant_text": first_text,
    }

    _assert(summary, "l1_first_compound_approval_required", first_start.get("approval_required") is True, str(first_start))
    _assert(summary, "l1_first_compound_tool", first_request.get("tool") == "edit_markdown", str(first_request))
    _assert(
        summary,
        "l1_first_compound_reason",
        str(first_request.get("synthetic_reason") or "").strip() == "compound_edit_followthrough",
        str(first_request),
    )
    _assert(summary, "l1_first_copy_locked", first_text == APPROVAL_REQUIRED_TEXT, first_text)
    _assert(summary, "l1_first_has_conversation_id", isinstance(conversation_id, str) and bool(conversation_id), str(conversation_id))
    _assert(summary, "l1_first_has_request_id", isinstance(request_id, str) and bool(request_id), str(request_id))

    reject_response = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message="reject",
            conversation_type=conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=scope_path,
            mcp_project_name=scope_name,
            conversation_id=conversation_id,
            params_extra={
                "mcp_native_tool_calling": True,
                "mcp_approval": {
                    "action": "reject",
                    "request_id": request_id,
                },
            },
        ),
    )
    reject_resolution = reject_response.get("approval_resolution") or {}
    reject_text = str((((reject_response.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or "")
    scenario["reject_resolution"] = {
        "approval_resolution": reject_resolution,
        "assistant_text": reject_text,
        "tooling_state": reject_response.get("tooling_state"),
    }
    _assert(summary, "l1_reject_resolution_status", reject_resolution.get("status") == "rejected", str(reject_resolution))
    _assert(summary, "l1_reject_text_non_empty", bool(reject_text.strip()), reject_text)

    corrected_prompt = (
        'Actually correct it: edit spec.md and status.md. '
        f'Add "{marker} corrected spec" and "{marker} corrected status".'
    )
    corrected_start = _chat(
        config,
        token,
        _build_chat_payload(
            config=config,
            user_id=user_id,
            message=corrected_prompt,
            conversation_type=conversation_type,
            mcp_scope_mode="project",
            mcp_project_slug=scope_path,
            mcp_project_name=scope_name,
            conversation_id=conversation_id,
            params_extra={"mcp_native_tool_calling": True},
        ),
    )
    corrected_request = corrected_start.get("approval_request") or {}
    corrected_preview = corrected_request.get("preview") or {}
    corrected_text = str((((corrected_start.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or "")
    scenario["corrected_start"] = {
        "approval_required": corrected_start.get("approval_required"),
        "approval_request": corrected_request,
        "assistant_text": corrected_text,
        "tooling_state": corrected_start.get("tooling_state"),
    }

    _assert(summary, "l1_corrected_approval_required", corrected_start.get("approval_required") is True, str(corrected_start))
    _assert(summary, "l1_corrected_tool", corrected_request.get("tool") == "edit_markdown", str(corrected_request))
    _assert(summary, "l1_corrected_copy_locked", corrected_text == APPROVAL_REQUIRED_TEXT, corrected_text)

    corrected_chain: List[Dict[str, Any]] = []
    corrected_total_execution_count = 0
    corrected_request_id = corrected_request.get("request_id")
    for step in range(4):
        if not isinstance(corrected_request_id, str) or not corrected_request_id:
            break
        try:
            corrected_approve = _chat(
                config,
                token,
                _build_chat_payload(
                    config=config,
                    user_id=user_id,
                    message="approve",
                    conversation_type=conversation_type,
                    mcp_scope_mode="project",
                    mcp_project_slug=scope_path,
                    mcp_project_name=scope_name,
                    conversation_id=conversation_id,
                    params_extra={
                        "mcp_native_tool_calling": True,
                        "mcp_approval": {
                            "action": "approve",
                            "request_id": corrected_request_id,
                        },
                    },
                ),
            )
        except RuntimeError as exc:
            if "No pending approval request found for this conversation" in str(exc):
                corrected_chain.append(
                    {
                        "step": step + 1,
                        "request_id": corrected_request_id,
                        "error": "no_pending_approval",
                    }
                )
                break
            raise
        corrected_state = corrected_approve.get("tooling_state") or {}
        corrected_resolution = corrected_approve.get("approval_resolution") or {}
        corrected_text_step = str((((corrected_approve.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or "")
        corrected_total_execution_count += int(corrected_state.get("tool_calls_executed_count") or 0)
        corrected_chain.append(
            {
                "step": step + 1,
                "request_id": corrected_request_id,
                "approval_required": corrected_approve.get("approval_required"),
                "approval_request": corrected_approve.get("approval_request"),
                "approval_resolution": corrected_resolution,
                "tooling_state": corrected_state,
                "assistant_text": corrected_text_step,
            }
        )
        if corrected_approve.get("approval_required") is not True:
            break
        next_request = corrected_approve.get("approval_request") or {}
        next_request_id = next_request.get("request_id")
        if not isinstance(next_request_id, str) or not next_request_id or next_request_id == corrected_request_id:
            break
        corrected_request_id = next_request_id

    corrected_final = corrected_chain[-1] if corrected_chain else {}
    corrected_final_text = str(corrected_final.get("assistant_text") or "")
    corrected_approved_any = any(
        isinstance(item.get("approval_resolution"), dict)
        and item["approval_resolution"].get("status") == "approved"
        for item in corrected_chain
    )
    corrected_settled = bool(corrected_chain) and corrected_final.get("approval_required") is not True

    scenario["corrected_approve_chain"] = corrected_chain
    scenario["corrected_approve"] = {
        "total_execution_count": corrected_total_execution_count,
        "approved_any": corrected_approved_any,
        "settled": corrected_settled,
        "final_step": corrected_final,
    }
    _assert(summary, "l1_corrected_approved", corrected_approved_any, str(corrected_chain))
    _assert(
        summary,
        "l1_corrected_execution_count",
        corrected_total_execution_count >= 2,
        str(corrected_chain),
    )
    _assert(summary, "l1_corrected_confirmation_text_non_empty", bool(corrected_final_text.strip()), corrected_final_text)

    preview_checks: List[Dict[str, Any]] = [
        {"stage": "first", "preview": first_preview},
        {"stage": "corrected", "preview": corrected_preview},
    ]
    has_preview_marker = any(
        isinstance(item.get("preview"), dict)
        and item["preview"].get("previewTool") == "preview_markdown_change"
        for item in preview_checks
    )
    has_preview_content = any(
        isinstance(item.get("preview"), dict)
        and (
            bool(str(item["preview"].get("diff") or "").strip())
            or bool(str(item["preview"].get("summary") or "").strip())
        )
        for item in preview_checks
    )

    if not (has_preview_marker and has_preview_content):
        preview_probe_prompt = (
            "Return ONLY a tool call to edit_markdown. "
            f"Path: {scope_path}/spec.md. "
            "Operation: append line '- preview probe'. No prose."
        )
        preview_probe = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=preview_probe_prompt,
                conversation_type=conversation_type,
                mcp_scope_mode="project",
                mcp_project_slug=scope_path,
                mcp_project_name=scope_name,
                conversation_id=conversation_id,
                params_extra={"mcp_native_tool_calling": True},
            ),
        )
        preview_probe_request = preview_probe.get("approval_request") or {}
        preview_probe_preview = preview_probe_request.get("preview") or {}
        preview_checks.append({"stage": "preview_probe", "preview": preview_probe_preview})

        has_preview_marker = has_preview_marker or (
            isinstance(preview_probe_preview, dict)
            and preview_probe_preview.get("previewTool") == "preview_markdown_change"
        )
        has_preview_content = has_preview_content or (
            isinstance(preview_probe_preview, dict)
            and (
                bool(str(preview_probe_preview.get("diff") or "").strip())
                or bool(str(preview_probe_preview.get("summary") or "").strip())
            )
        )

        preview_probe_request_id = preview_probe_request.get("request_id")
        if (
            preview_probe.get("approval_required") is True
            and isinstance(preview_probe_request_id, str)
            and preview_probe_request_id
        ):
            preview_probe_reject = _chat(
                config,
                token,
                _build_chat_payload(
                    config=config,
                    user_id=user_id,
                    message="reject",
                    conversation_type=conversation_type,
                    mcp_scope_mode="project",
                    mcp_project_slug=scope_path,
                    mcp_project_name=scope_name,
                    conversation_id=conversation_id,
                    params_extra={
                        "mcp_native_tool_calling": True,
                        "mcp_approval": {
                            "action": "reject",
                            "request_id": preview_probe_request_id,
                        },
                    },
                ),
            )
            scenario["preview_probe_reject"] = {
                "approval_resolution": preview_probe_reject.get("approval_resolution"),
                "tooling_state": preview_probe_reject.get("tooling_state"),
            }
        scenario["preview_probe"] = {
            "approval_required": preview_probe.get("approval_required"),
            "approval_request": preview_probe_request,
            "tooling_state": preview_probe.get("tooling_state"),
        }

    scenario["preview_checks"] = preview_checks
    summary_fallback_present = any(
        bool(str(request.get("summary") or "").strip())
        for request in (first_request, corrected_request)
        if isinstance(request, dict)
    )
    scenario["preview_summary_fallback_present"] = summary_fallback_present
    _assert(
        summary,
        "l1_preview_marker_or_summary_present",
        has_preview_marker or summary_fallback_present,
        str(
            {
                "preview_checks": preview_checks,
                "summary_fallback_present": summary_fallback_present,
            }
        ),
    )
    _assert(
        summary,
        "l1_preview_content_or_summary_present",
        has_preview_content or summary_fallback_present,
        str(
            {
                "preview_checks": preview_checks,
                "summary_fallback_present": summary_fallback_present,
            }
        ),
    )

    return scenario


def _run_l2(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    scenario["onboarding_seed"] = _seed_finances_onboarding_complete(config, user_id)

    page_matrix: List[Dict[str, Any]] = []
    starter_pages = [
        ("Library Capture", "capture", "capture", "Capture"),
        ("New Page", "chat", None, None),
        ("WhyFinder", "life-whyfinder", "whyfinder", "WhyFinder"),
        ("Career", "life-career", "career", "Career"),
        ("Finances", "life-finances", "finances", "Finances"),
        ("Fitness", "life-fitness", "fitness", "Fitness"),
        ("Relationships", "life-relationships", "relationships", "Relationships"),
    ]
    for index, (name, conversation_type, expected_slug, expected_name) in enumerate(starter_pages, start=1):
        page_id = f"l2-starter-{index}-{name.lower().replace(' ', '-') }"
        start = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=f"Starter page parity check for {name}.",
                conversation_type=conversation_type,
                mcp_scope_mode="none",
                params_extra={"mcp_tools_enabled": False, "mcp_sync_on_request": False},
                page_id=page_id,
            ),
        )
        state = start.get("tooling_state") or {}
        conversation_id = start.get("conversation_id")
        _assert(
            summary,
            f"l2_{index}_start_conversation_id",
            isinstance(conversation_id, str) and bool(conversation_id),
            str(conversation_id),
        )
        if expected_slug is None:
            _assert(
                summary,
                f"l2_{index}_chat_scope_unscoped",
                state.get("mcp_scope_mode") in {None, "none"} and not state.get("mcp_project_slug"),
                str(state),
            )
        else:
            _assert(
                summary,
                f"l2_{index}_scope_defaults",
                state.get("mcp_scope_mode") == "project"
                and state.get("mcp_project_slug") == expected_slug
                and state.get("mcp_project_name") == expected_name
                and state.get("mcp_scope_source") == "conversation_type",
                str(state),
            )

        same_page = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=f"Continue {name} parity conversation.",
                conversation_type=conversation_type,
                mcp_scope_mode="none",
                conversation_id=conversation_id,
                params_extra={"mcp_tools_enabled": False, "mcp_sync_on_request": False},
                page_id=page_id,
            ),
        )
        _assert(
            summary,
            f"l2_{index}_same_page_continue_ok",
            str(same_page.get("conversation_id") or "") == str(conversation_id),
            str(same_page),
        )

        mismatch_status, mismatch_body = _chat_with_status(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=f"Mismatch {name} parity conversation.",
                conversation_type=conversation_type,
                mcp_scope_mode="none",
                conversation_id=conversation_id,
                params_extra={"mcp_tools_enabled": False, "mcp_sync_on_request": False},
                page_id=f"{page_id}-mismatch",
            ),
        )
        mismatch_detail = str(mismatch_body.get("detail") or "")
        _assert(summary, f"l2_{index}_mismatch_status_409", mismatch_status == 409, str(mismatch_body))
        _assert(
            summary,
            f"l2_{index}_mismatch_detail",
            "Conversation is bound to a different page" in mismatch_detail,
            mismatch_detail,
        )

        page_matrix.append(
            {
                "name": name,
                "conversation_type": conversation_type,
                "page_id": page_id,
                "tooling_state": state,
                "mismatch_status": mismatch_status,
                "mismatch_body": mismatch_body,
            }
        )

    scenario["starter_page_parity_matrix"] = page_matrix

    archetype_probes = [
        {
            "name": "project_research",
            "message": "Create a new project page for competitor research vault.",
            "conversation_type": "chat",
            "scope_mode": "project",
            "project_slug": "projects/active/finance",
            "project_name": "Finance",
            "expected_path": "projects/active/competitor-research-vault",
            "expected_tools": ["create_project"],
            "expected_archetype": "research",
            "expected_file": "research.md",
        },
        {
            "name": "project_operations",
            "message": "Create a new project page for incident operations runbook.",
            "conversation_type": "chat",
            "scope_mode": "project",
            "project_slug": "projects/active/finance",
            "project_name": "Finance",
            "expected_path": "projects/active/incident-operations-runbook",
            "expected_tools": ["create_project"],
            "expected_archetype": "operations",
            "expected_file": "runbook.md",
        },
        {
            "name": "life_habit",
            "message": "Create a new life page for morning habit reset.",
            "conversation_type": "chat",
            "scope_mode": "project",
            "project_slug": "life/finances",
            "project_name": "Finances",
            "expected_path": "life/morning-habit-reset",
            "expected_tools": ["create_project", "create_markdown"],
            "expected_archetype": "habit",
            "expected_file": "habits.md",
        },
    ]

    archetype_results: List[Dict[str, Any]] = []
    for probe in archetype_probes:
        response = _chat(
            config,
            token,
            _build_chat_payload(
                config=config,
                user_id=user_id,
                message=str(probe["message"]),
                conversation_type=str(probe["conversation_type"]),
                mcp_scope_mode=str(probe["scope_mode"]),
                mcp_project_slug=str(probe["project_slug"]),
                mcp_project_name=str(probe["project_name"]),
                params_extra={"mcp_max_tool_iterations": 6},
            ),
        )
        approval = response.get("approval_request") or {}
        arguments = approval.get("arguments") or {}
        files = arguments.get("files") or []
        file_paths = {item.get("path") for item in files if isinstance(item, dict)}
        tool_name = str(approval.get("tool") or "").strip()
        expected_tools = {str(item).strip() for item in (probe.get("expected_tools") or [])}
        if not expected_tools:
            expected_tools = {"create_project"}

        probe_name = str(probe["name"])
        _assert(summary, f"l2_{probe_name}_approval_required", response.get("approval_required") is True, str(response))
        _assert(summary, f"l2_{probe_name}_tool", tool_name in expected_tools, str(approval))

        meta_payload: Dict[str, Any] = {}
        if tool_name == "create_project":
            _assert(
                summary,
                f"l2_{probe_name}_path",
                arguments.get("path") == probe["expected_path"],
                str(arguments),
            )
            meta_entry = next(
                (item for item in files if isinstance(item, dict) and item.get("path") == "_meta/interview-state.md"),
                None,
            )
            meta_payload = json.loads(str((meta_entry or {}).get("content") or "{}"))
            _assert(
                summary,
                f"l2_{probe_name}_archetype_meta",
                meta_payload.get("page_archetype") == probe["expected_archetype"],
                str(meta_payload),
            )
            _assert(
                summary,
                f"l2_{probe_name}_archetype_file",
                str(probe["expected_file"]) in file_paths,
                str(sorted(file_paths)),
            )
            _assert(
                summary,
                f"l2_{probe_name}_seed_questions_present",
                isinstance(meta_payload.get("seed_questions"), list)
                and len(meta_payload.get("seed_questions") or []) >= 4,
                str(meta_payload),
            )
        elif tool_name == "create_markdown":
            expected_paths = {
                str(probe["expected_path"]),
                f"{probe['expected_path']}.md",
            }
            _assert(
                summary,
                f"l2_{probe_name}_markdown_path",
                str(arguments.get("path") or "") in expected_paths,
                str(arguments),
            )
            _assert(
                summary,
                f"l2_{probe_name}_markdown_content_non_empty",
                bool(str(arguments.get("content") or "").strip()),
                str(arguments),
            )

        archetype_results.append(
            {
                "name": probe_name,
                "approval_required": response.get("approval_required"),
                "approval_request": approval,
                "meta_payload": meta_payload,
                "file_paths": sorted(path for path in file_paths if isinstance(path, str)),
            }
        )

    scenario["archetype_expansion"] = archetype_results
    return scenario


def _run_l3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}
    scenario["onboarding_seed"] = _seed_finances_onboarding_complete(config, user_id)

    routing_default = _attempt_routing_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        conversation_type="chat",
        scope_mode="project",
        project_slug="projects/active/finance",
        project_name="Finance",
        prompts=[
            "Create a task to review project budget next week.",
            "Review this project and propose one concrete task.",
        ],
        params_extra={"mcp_max_tool_iterations": 5},
        predicate=lambda s: s.get("tool_policy_mode") == "dual_path_project_scope_compat"
        and s.get("tool_profile") == "full",
    )
    scenario["routing_default_project_scope"] = routing_default
    _assert(
        summary,
        "l3_routing_default_project_scope",
        bool(routing_default.get("passed")),
        str(routing_default),
    )

    routing_native_override = _attempt_routing_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        conversation_type="chat",
        scope_mode="project",
        project_slug="projects/active/finance",
        project_name="Finance",
        prompts=["Create a task to review project budget next week."],
        params_extra={"mcp_max_tool_iterations": 5, "mcp_native_tool_calling": True},
        predicate=lambda s: s.get("tool_routing_mode") == "single_path_native"
        and s.get("tool_execution_mode") == "single_path_native"
        and s.get("routing_capability_source") == "request_override",
    )
    scenario["routing_native_override"] = routing_native_override
    _assert(
        summary,
        "l3_routing_native_override",
        bool(routing_native_override.get("passed")),
        str(routing_native_override),
    )

    routing_life_scope = _attempt_routing_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        conversation_type="chat",
        scope_mode="project",
        project_slug="life/fitness",
        project_name="Fitness",
        prompts=[
            "Create a task to review my fitness weekly plan.",
            "What should I do this week in fitness?",
        ],
        params_extra={"mcp_max_tool_iterations": 5},
        predicate=lambda s: s.get("tool_policy_mode") == "dual_path_life_scope_compat"
        and s.get("tool_profile") == "full",
    )
    scenario["routing_life_scope"] = routing_life_scope
    _assert(
        summary,
        "l3_routing_life_scope_policy",
        bool(routing_life_scope.get("passed")),
        str(routing_life_scope),
    )

    routing_new_page_intent = _attempt_routing_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        conversation_type="chat",
        scope_mode="project",
        project_slug="projects/active/finance",
        project_name="Finance",
        prompts=[
            "Create a new project page for competitor research board.",
            "Build a new project workspace for marketing launch prep.",
        ],
        params_extra={"mcp_max_tool_iterations": 5},
        predicate=lambda s: str(s.get("tool_policy_mode") or "").strip()
        in {"dual_path_new_page_compat", "dual_path_life_new_page_compat"},
    )
    scenario["routing_new_page_intent"] = routing_new_page_intent
    _assert(
        summary,
        "l3_routing_new_page_policy",
        bool(routing_new_page_intent.get("passed")),
        str(routing_new_page_intent),
    )

    routing_owner_profile_intent = _attempt_routing_probe(
        summary=summary,
        config=config,
        token=token,
        user_id=user_id,
        conversation_type="chat",
        scope_mode="project",
        project_slug="projects/active/finance",
        project_name="Finance",
        prompts=[
            "Update my profile: I prefer short weekly check-ins and concise plans.",
            "Write to me/profile.md that I prefer concise planning updates.",
        ],
        params_extra={"mcp_max_tool_iterations": 5},
        predicate=lambda s: str(s.get("tool_policy_mode") or "").strip()
        in {"dual_path_owner_profile_compat", "dual_path_project_scope_compat"}
        and s.get("tool_profile") == "full",
    )
    scenario["routing_owner_profile_intent"] = routing_owner_profile_intent
    _assert(
        summary,
        "l3_routing_owner_profile_policy_or_scope_fallback",
        bool(routing_owner_profile_intent.get("passed")),
        str(routing_owner_profile_intent),
    )

    project_citation_probe = _attempt_citation_probe(
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
        "l3_project_scope_citation_acceptance",
        bool(project_citation_probe.get("accepted")),
        str(project_citation_probe),
    )
    _assert(
        summary,
        "l3_life_scope_citation_acceptance",
        bool(life_citation_probe.get("accepted")),
        str(life_citation_probe),
    )

    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process L.1/L.2/L.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-l123")
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

        summary.scenarios["process_l1"] = _run_l1(summary, config, token, user_id)
        summary.scenarios["process_l2"] = _run_l2(summary, config, token, user_id)
        summary.scenarios["process_l3"] = _run_l3(summary, config, token, user_id)
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
