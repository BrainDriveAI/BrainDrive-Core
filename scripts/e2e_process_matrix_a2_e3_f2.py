#!/usr/bin/env python3
"""Live probe harness for Process A.2, E.3, and F.2.

Runs against the real backend API and records a deterministic summary artifact.
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
                time.sleep(min(backoff + jitter, 120.0))
                attempt += 1
                continue
            raise RuntimeError(f"HTTP {exc.code} for {url}: {details}") from exc
        except urllib_error.URLError as exc:
            if attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 120.0))
                attempt += 1
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _login(config: Config) -> Tuple[str, str]:
    payload = {"email": config.email, "password": config.password}
    response = _http_post(
        url=f"{config.base_url.rstrip('/')}/api/v1/auth/login",
        payload=payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=max(config.request_delay_seconds, 1.0),
        max_retries=max(config.http_max_retries, 6),
        retry_base_seconds=max(config.http_retry_base_seconds, 5.0),
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


def _bootstrap_user_scope(config: Config, user_id: str) -> Dict[str, Any]:
    normalized = _normalize_user_id(user_id)
    user_scope = config.library_root / "users" / normalized
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
        normalized,
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
    mcp_project_slug: str,
    mcp_project_name: str,
    params_extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "mcp_tools_enabled": True,
        "mcp_scope_mode": "project",
        "mcp_project_slug": mcp_project_slug,
        "mcp_project_name": mcp_project_name,
        "mcp_project_source": "ui",
        "mcp_sync_on_request": False,
        "mcp_auto_approve_mutating": False,
        "mcp_max_tool_iterations": 4,
        "mcp_provider_timeout_seconds": 30,
    }
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


def _run_a2(config: Config, summary: ProbeSummary, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {}

    fanout_start_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="Capture this: paid gym membership and add a task to review budget for Dave J by next week.",
        conversation_type="capture",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
    )
    fanout_start = _chat(config, token, fanout_start_payload)
    first_request = fanout_start.get("approval_request") or {}
    conversation_id = fanout_start.get("conversation_id")
    scenario["fanout_start"] = {
        "approval_required": fanout_start.get("approval_required"),
        "approval_request": first_request,
        "conversation_id": conversation_id,
    }

    _assert(summary, "a2_fanout_first_approval_required", fanout_start.get("approval_required") is True, str(fanout_start))
    _assert(summary, "a2_fanout_first_tool_markdown", first_request.get("tool") == "create_markdown", str(first_request))
    _assert(
        summary,
        "a2_fanout_first_reason_inbox_persist",
        str(first_request.get("synthetic_reason") or "").strip() in {"", "capture_inbox_persist"},
        str(first_request),
    )
    _assert(summary, "a2_fanout_has_conversation_id", isinstance(conversation_id, str) and bool(conversation_id), str(conversation_id))

    fanout_resume_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="approve",
        conversation_type="capture",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        conversation_id=conversation_id,
        params_extra={
            "mcp_approval": {
                "action": "approve",
                "request_id": first_request.get("request_id"),
            }
        },
    )
    fanout_resume = _chat(config, token, fanout_resume_payload)
    second_request = fanout_resume.get("approval_request") or {}
    second_args = second_request.get("arguments") or {}
    scenario["fanout_resume"] = {
        "approval_required": fanout_resume.get("approval_required"),
        "approval_resolution": fanout_resume.get("approval_resolution"),
        "approval_request": second_request,
        "tooling_state": fanout_resume.get("tooling_state"),
    }
    _assert(summary, "a2_fanout_second_approval_required", fanout_resume.get("approval_required") is True, str(fanout_resume))
    _assert(summary, "a2_fanout_second_tool_create_task", second_request.get("tool") == "create_task", str(second_request))
    _assert(
        summary,
        "a2_fanout_second_reason_task_create",
        str(second_request.get("synthetic_reason") or "").strip() in {"", "capture_new_task_create"},
        str(second_request),
    )
    _assert(
        summary,
        "a2_fanout_second_has_owner_scope_project_due",
        bool(second_args.get("owner"))
        and second_args.get("scope") == "life/finances"
        and second_args.get("project") == "finances"
        and bool(second_args.get("due")),
        str(second_args),
    )

    # Optional follow-on probes for visibility (no hard assertions due live-model variance).
    fanout_finalize_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="approve",
        conversation_type="capture",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
        conversation_id=conversation_id,
        params_extra={
            "mcp_approval": {
                "action": "approve",
                "request_id": second_request.get("request_id"),
            }
        },
    )
    fanout_finalize = _chat(config, token, fanout_finalize_payload)
    scenario["fanout_finalize_optional"] = {
        "approval_required": fanout_finalize.get("approval_required"),
        "approval_request": fanout_finalize.get("approval_request"),
        "approval_resolution": fanout_finalize.get("approval_resolution"),
        "tooling_state": fanout_finalize.get("tooling_state"),
    }

    edit_payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=(
            "Update task 'Review budget for Dave J': owner: Sarah, "
            "due next Friday, high priority."
        ),
        conversation_type="capture",
        mcp_project_slug="life/finances",
        mcp_project_name="Finances",
    )
    edit_response = _chat(config, token, edit_payload)
    scenario["ambiguous_edit_optional"] = {
        "approval_required": edit_response.get("approval_required"),
        "approval_request": edit_response.get("approval_request"),
        "tooling_state": edit_response.get("tooling_state"),
    }
    return scenario


def _run_e3(config: Config, summary: ProbeSummary, token: str, user_id: str) -> Dict[str, Any]:
    payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message="My spouse and I keep arguing about debt and monthly budget stress.",
        conversation_type="chat",
        mcp_project_slug="life/relationships",
        mcp_project_name="Relationships",
    )
    response = _chat(config, token, payload)
    approval_request = response.get("approval_request") or {}
    tooling_state = response.get("tooling_state") or {}
    scenario = {
        "approval_required": response.get("approval_required"),
        "approval_request": approval_request,
        "tooling_state": tooling_state,
    }

    _assert(summary, "e3_cross_pollination_approval_required", response.get("approval_required") is True, str(response))

    tool_name = str(approval_request.get("tool") or "")
    approval_args = approval_request.get("arguments") or {}
    allow_cross_pollination_write = (
        tool_name == "create_markdown"
        and approval_request.get("synthetic_reason") == "cross_pollination_relationships_to_finances"
    )
    allow_onboarding_kickoff = (
        tool_name == "start_topic_onboarding"
        and str(approval_args.get("topic") or "").strip().lower() == "finances"
    )
    _assert(
        summary,
        "e3_cross_pollination_expected_action",
        allow_cross_pollination_write or allow_onboarding_kickoff,
        str(approval_request),
    )
    _assert(
        summary,
        "e3_dual_path_compat_routing_state",
        tooling_state.get("tool_routing_mode") == "dual_path_fallback"
        and tooling_state.get("tool_profile") == "full"
        and tooling_state.get("tool_profile_source") == "routing_scope_policy",
        str(tooling_state),
    )
    return scenario


def _run_f2(config: Config, summary: ProbeSummary, token: str, user_id: str) -> Dict[str, Any]:
    prompts = [
        "Which files in life/finances contain '(to be populated during onboarding)'? Use library tools before answering.",
        "What does my finances goals page currently say? Use library tools before answering.",
        "What does my current budget file say right now? Please ground your answer in library reads.",
        "What are the key points in my finances onboarding interview file?",
    ]
    attempts: List[Dict[str, Any]] = []
    found_citation_acceptance = False
    for prompt in prompts:
        payload = _build_chat_payload(
            config=config,
            user_id=user_id,
            message=prompt,
            conversation_type="chat",
            mcp_project_slug="life/finances",
            mcp_project_name="Finances",
        )
        response = _chat(config, token, payload)
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        tooling_state = response.get("tooling_state") or {}
        citations = tooling_state.get("response_citations") or []
        appended = bool(tooling_state.get("response_citations_appended"))
        has_sources_block = "Sources:" in str(content)
        normalized_content = str(content).lower()
        response_references_cited_path = any(
            isinstance(path, str) and path.strip() and path.lower() in normalized_content
            for path in citations
        )
        accepted = bool(citations) and (
            appended or has_sources_block or response_references_cited_path
        )
        attempts.append(
            {
                "prompt": prompt,
                "accepted": accepted,
                "response_excerpt": str(content)[:500],
                "response_has_sources_block": has_sources_block,
                "response_references_cited_path": response_references_cited_path,
                "response_citations": citations,
                "response_citations_appended": appended,
                "tooling_state": tooling_state,
            }
        )
        if accepted:
            found_citation_acceptance = True
            break

    _assert(
        summary,
        "f2_citation_acceptance_found",
        found_citation_acceptance,
        json.dumps(attempts, ensure_ascii=True),
    )
    return {"attempts": attempts}


def _parse_args() -> Config:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Live probe for Process A.2/E.3/F.2")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="ollama_default_server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-a2-e3-f2")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--request-delay-seconds", type=float, default=1.25)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=2.0)
    parser.add_argument("--reset-from-template", action="store_true")
    parser.add_argument(
        "--library-root",
        default=str(repo_root / "backend" / "services_runtime" / "Library-Service" / "library"),
    )
    parser.add_argument(
        "--template-root",
        default=str(repo_root / "backend" / "library_templates" / "Base_Library"),
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
        output_dir=Path(args.output_dir),
        timeout_seconds=args.timeout_seconds,
        request_delay_seconds=args.request_delay_seconds,
        http_max_retries=args.http_max_retries,
        http_retry_base_seconds=args.http_retry_base_seconds,
        reset_from_template=bool(args.reset_from_template),
        library_root=Path(args.library_root),
        template_root=Path(args.template_root),
    )


def main() -> int:
    config = _parse_args()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("live-process-a2-e3-f2-%Y%m%dT%H%M%SZ")
    run_dir = config.output_dir / run_id
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
        if config.reset_from_template:
            bootstrap_meta = _bootstrap_user_scope(config, user_id)
            summary.reset_applied = True
            summary.scenarios["bootstrap"] = bootstrap_meta

        summary.scenarios["process_a2"] = _run_a2(config, summary, token, user_id)
        summary.scenarios["process_e3"] = _run_e3(config, summary, token, user_id)
        summary.scenarios["process_f2"] = _run_f2(config, summary, token, user_id)
        summary.success = True
    except Exception as exc:  # pragma: no cover - runtime harness
        summary.error = str(exc)
        summary.success = False
    finally:
        output = {
            "run_id": summary.run_id,
            "started_at": summary.started_at,
            "base_url": summary.base_url,
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
        }
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"summary_path": str(summary_path.resolve()), "success": summary.success}, indent=2))

    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
