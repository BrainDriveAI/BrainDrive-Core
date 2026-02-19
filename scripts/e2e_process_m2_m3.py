#!/usr/bin/env python3
"""Live probe harness for Process M.2 and M.3.

M.2: provider/model capability matrix with runtime evidence and skip reasons.
M.3: longer mixed-traffic soak (chat + capture + digest + delivery) stability checks.
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
from urllib import parse as urllib_parse
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
    soak_iterations: int


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
    data: Optional[bytes] = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    attempt = 0
    while True:
        request_obj = urllib_request.Request(url=url, data=data, headers=headers, method=method.upper())
        try:
            _throttle(request_delay_seconds)
            with urllib_request.urlopen(request_obj, timeout=timeout_seconds) as response:
                return int(response.status), _decode_json_bytes(response.read())
        except urllib_error.HTTPError as exc:
            body = _decode_json_bytes(exc.read())
            if exc.code == 429 and attempt < max_retries:
                backoff = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter = random.uniform(0.0, min(1.0, backoff * 0.25))
                time.sleep(min(backoff + jitter, 90.0))
                attempt += 1
                continue
            return int(exc.code), body
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


def _run_m2(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {"live_matrix": [], "skipped": []}
    status, catalog = _http_json(
        method="GET",
        url=f"{config.base_url.rstrip('/')}/api/v1/ai/providers/catalog?user_id=current",
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
        token=token,
    )
    _assert(summary, "m2_catalog_status_200", status == 200, str(catalog))
    providers = catalog.get("providers") if isinstance(catalog, dict) else None
    _assert(summary, "m2_catalog_has_providers", isinstance(providers, list) and len(providers) > 0, str(catalog))

    configured = [
        p for p in providers
        if isinstance(p, dict) and bool(p.get("enabled")) and bool(p.get("configured"))
    ]
    _assert(summary, "m2_has_configured_provider", len(configured) > 0, str(providers))

    for provider_entry in providers:
        if not isinstance(provider_entry, dict):
            continue
        provider_id = str(provider_entry.get("id") or "").strip()
        if not provider_id:
            continue
        if provider_entry not in configured:
            scenario["skipped"].append(
                {
                    "provider": provider_id,
                    "reason": "not_configured_or_disabled",
                    "configured": bool(provider_entry.get("configured")),
                    "enabled": bool(provider_entry.get("enabled")),
                }
            )
            continue

        matrix_entry: Dict[str, Any] = {"provider": provider_id, "models": []}
        settings_id = str(provider_entry.get("settings_id") or "")
        default_server_id = str(provider_entry.get("default_server_id") or "")
        if provider_id == "ollama":
            settings_id = config.settings_id
            default_server_id = config.server_id

        model_candidates: List[str] = [config.model]
        if provider_id == "ollama":
            model_candidates = [config.model, "hf.co/mradermacher/granite-4.0-micro-GGUF:Q8_0"]

        seen_models: set[str] = set()
        for model_name in model_candidates:
            model_name = str(model_name or "").strip()
            if not model_name or model_name in seen_models:
                continue
            seen_models.add(model_name)

            route_status, route_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=provider_id,
                    settings_id=settings_id,
                    server_id=default_server_id,
                    model=model_name,
                    user_id=user_id,
                    message="Create a task to review budget next week.",
                    conversation_type="chat",
                    mcp_scope_mode="project",
                    mcp_project_slug="projects/active/finance",
                    mcp_project_name="Finance",
                    params_extra={"mcp_max_tool_iterations": 5},
                ),
            )
            tooling = route_response.get("tooling_state") if isinstance(route_response, dict) else {}
            route_ok = (
                route_status == 200
                and isinstance(tooling, dict)
                and bool(str(tooling.get("tool_routing_mode") or "").strip())
                and bool(str(tooling.get("tool_execution_mode") or "").strip())
            )

            citation_status, citation_response = _chat(
                config,
                token,
                _build_chat_payload(
                    provider=provider_id,
                    settings_id=settings_id,
                    server_id=default_server_id,
                    model=model_name,
                    user_id=user_id,
                    message="Read project finance markdown context and answer with Sources.",
                    conversation_type="chat",
                    mcp_scope_mode="project",
                    mcp_project_slug="projects/active/finance",
                    mcp_project_name="Finance",
                    params_extra={"mcp_max_tool_iterations": 6, "mcp_tool_profile": "read_only"},
                ),
            )
            citation_text = str(
                ((citation_response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ) if isinstance(citation_response, dict) else ""
            citation_tooling = citation_response.get("tooling_state") if isinstance(citation_response, dict) else {}
            citation_meta = citation_tooling.get("response_citations") if isinstance(citation_tooling, dict) else None
            citation_ok = (
                citation_status == 200
                and (
                    "sources:" in citation_text.lower()
                    or (isinstance(citation_meta, list) and len(citation_meta) > 0)
                    or ".md" in citation_text
                )
            )

            matrix_entry["models"].append(
                {
                    "model": model_name,
                    "routing_probe": {
                        "status": route_status,
                        "ok": route_ok,
                        "tooling_state": tooling,
                        "approval_required": bool(route_response.get("approval_required")) if isinstance(route_response, dict) else None,
                    },
                    "citation_probe": {
                        "status": citation_status,
                        "ok": citation_ok,
                        "has_sources_block": "sources:" in citation_text.lower(),
                        "citation_count": len(citation_meta) if isinstance(citation_meta, list) else 0,
                    },
                }
            )

        scenario["live_matrix"].append(matrix_entry)

    matrix_has_pass = any(
        any(
            bool(model_entry.get("routing_probe", {}).get("ok"))
            and bool(model_entry.get("citation_probe", {}).get("ok"))
            for model_entry in provider_entry.get("models") or []
        )
        for provider_entry in scenario["live_matrix"]
    )
    _assert(summary, "m2_live_matrix_has_passing_entry", matrix_has_pass, str(scenario["live_matrix"]))

    pytest_cmd = [
        "/home/hacker/anaconda3/envs/BrainDriveDev/bin/python",
        "-m",
        "pytest",
        "-q",
        "tests/test_mcp_chat_gating.py",
        "-k",
        "other_native_providers_default_to_single_path or model_hint_promotes_unknown_provider_to_native_mode",
    ]
    completed = subprocess.run(
        pytest_cmd,
        cwd=str(Path(__file__).resolve().parents[1] / "backend"),
        capture_output=True,
        text=True,
        check=False,
    )
    scenario["policy_regression"] = {
        "command": " ".join(pytest_cmd),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-1200:],
    }
    _assert(summary, "m2_policy_regression_pass", completed.returncode == 0, completed.stdout[-1000:])
    return scenario


def _run_m3(summary: ProbeSummary, config: Config, token: str, user_id: str) -> Dict[str, Any]:
    scenario: Dict[str, Any] = {
        "iterations": config.soak_iterations,
        "digest_runs": [],
        "pre_compaction_runs": [],
        "capture_runs": [],
    }

    digest_conversation_id: Optional[str] = None
    chat_conversation_id: Optional[str] = None
    capture_conversation_id: Optional[str] = None

    for i in range(config.soak_iterations):
        digest_event_id = f"m3-digest-{i // 2}"
        digest_status, digest_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=config.provider,
                settings_id=config.settings_id,
                server_id=config.server_id,
                model=config.model,
                user_id=user_id,
                message=f"M3 digest check iteration {i + 1}.",
                conversation_type="digest-email",
                mcp_scope_mode="project",
                mcp_project_slug="projects/active/finance",
                mcp_project_name="Finance",
                params_extra={
                    "mcp_max_tool_iterations": 8,
                    "mcp_digest_schedule_enabled": True,
                    "mcp_digest_schedule_due_now": True,
                    "mcp_digest_schedule_event_id": digest_event_id,
                    "mcp_digest_reply_to_capture_enabled": True,
                    "mcp_digest_delivery_send_enabled": True,
                    "mcp_native_tool_calling": True,
                },
                conversation_id=digest_conversation_id,
            ),
        )
        if isinstance(digest_response, dict) and isinstance(digest_response.get("conversation_id"), str):
            digest_conversation_id = digest_response.get("conversation_id")
        digest_tooling = digest_response.get("tooling_state") if isinstance(digest_response, dict) else {}
        scenario["digest_runs"].append(
            {
                "iteration": i + 1,
                "event_id": digest_event_id,
                "status_code": digest_status,
                "digest_schedule_status": (digest_tooling or {}).get("digest_schedule_status"),
                "digest_schedule_duplicate_guard": (digest_tooling or {}).get("digest_schedule_duplicate_guard"),
                "delivery_send_status": (digest_tooling or {}).get("delivery_send_status"),
            }
        )

        pre_event_id = f"m3-pre-{i // 2}"
        long_prompt = (
            f"M3 pre-compaction iteration {i + 1}. "
            + ("Budget risks and mitigation details. " * 80)
        )
        pre_status, pre_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=config.provider,
                settings_id=config.settings_id,
                server_id=config.server_id,
                model=config.model,
                user_id=user_id,
                message=long_prompt,
                conversation_type="chat",
                mcp_scope_mode="project",
                mcp_project_slug="projects/active/finance",
                mcp_project_name="Finance",
                params_extra={
                    "mcp_max_tool_iterations": 6,
                    "mcp_pre_compaction_flush_enabled": True,
                    "mcp_context_window_tokens": 128,
                    "mcp_pre_compaction_flush_threshold": 0.0,
                    "mcp_pre_compaction_event_id": pre_event_id,
                },
                conversation_id=chat_conversation_id,
            ),
        )
        if isinstance(pre_response, dict) and isinstance(pre_response.get("conversation_id"), str):
            chat_conversation_id = pre_response.get("conversation_id")
        pre_tooling = pre_response.get("tooling_state") if isinstance(pre_response, dict) else {}
        scenario["pre_compaction_runs"].append(
            {
                "iteration": i + 1,
                "event_id": pre_event_id,
                "status_code": pre_status,
                "pre_compaction_flush_status": (pre_tooling or {}).get("pre_compaction_flush_status"),
                "pre_compaction_flush_duplicate_guard": (pre_tooling or {}).get("pre_compaction_flush_duplicate_guard"),
            }
        )

        capture_prompt = f"Capture M3 iteration {i + 1}: create a task to reconcile expenses by 2026-03-10."
        cap_status, cap_response = _chat(
            config,
            token,
            _build_chat_payload(
                provider=config.provider,
                settings_id=config.settings_id,
                server_id=config.server_id,
                model=config.model,
                user_id=user_id,
                message=capture_prompt,
                conversation_type="capture",
                mcp_scope_mode="project",
                mcp_project_slug="projects/active/finance",
                mcp_project_name="Finance",
                params_extra={"mcp_max_tool_iterations": 8},
                conversation_id=capture_conversation_id,
            ),
        )
        if isinstance(cap_response, dict) and isinstance(cap_response.get("conversation_id"), str):
            capture_conversation_id = cap_response.get("conversation_id")

        approval_required = bool(cap_response.get("approval_required")) if isinstance(cap_response, dict) else False
        cap_entry: Dict[str, Any] = {
            "iteration": i + 1,
            "status_code": cap_status,
            "approval_required": approval_required,
            "tooling_state": (cap_response.get("tooling_state") if isinstance(cap_response, dict) else {}),
        }
        if approval_required:
            request_id = ((cap_response.get("approval_request") or {}).get("request_id") if isinstance(cap_response, dict) else None)
            if isinstance(request_id, str) and request_id:
                action = "approve" if i % 2 == 0 else "reject"
                res_status, res_response = _chat(
                    config,
                    token,
                    _build_chat_payload(
                        provider=config.provider,
                        settings_id=config.settings_id,
                        server_id=config.server_id,
                        model=config.model,
                        user_id=user_id,
                        message=action,
                        conversation_type="capture",
                        mcp_scope_mode="project",
                        mcp_project_slug="projects/active/finance",
                        mcp_project_name="Finance",
                        params_extra={
                            "mcp_max_tool_iterations": 8,
                            "mcp_approval": {"action": action, "request_id": request_id},
                        },
                        conversation_id=capture_conversation_id,
                    ),
                )
                cap_entry["approval_action"] = action
                cap_entry["approval_resume_status_code"] = res_status
                cap_entry["approval_resolution"] = res_response.get("approval_resolution") if isinstance(res_response, dict) else None
        scenario["capture_runs"].append(cap_entry)

    digest_duplicate_guard_count = sum(
        1
        for item in scenario["digest_runs"]
        if str(item.get("digest_schedule_status") or "") == "duplicate_guard"
    )
    pre_duplicate_guard_count = sum(
        1
        for item in scenario["pre_compaction_runs"]
        if str(item.get("pre_compaction_flush_status") or "") == "duplicate_guard"
    )
    capture_resolved = sum(
        1
        for item in scenario["capture_runs"]
        if isinstance(item.get("approval_resolution"), dict)
        and str(item["approval_resolution"].get("status") or "").strip() in {"approved", "rejected"}
    )

    scenario["aggregate"] = {
        "digest_duplicate_guard_count": digest_duplicate_guard_count,
        "pre_compaction_duplicate_guard_count": pre_duplicate_guard_count,
        "capture_resolved_count": capture_resolved,
    }
    _assert(
        summary,
        "m3_digest_duplicate_guard_seen",
        digest_duplicate_guard_count >= 1,
        str(scenario["digest_runs"]),
    )
    _assert(
        summary,
        "m3_pre_compaction_duplicate_guard_seen",
        pre_duplicate_guard_count >= 1,
        str(scenario["pre_compaction_runs"]),
    )
    _assert(
        summary,
        "m3_capture_approval_resolved_seen",
        capture_resolved >= 1,
        str(scenario["capture_runs"]),
    )
    return scenario


def _parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Run live Process M.2/M.3 probes.")
    parser.add_argument("--base-url", default="http://localhost:8005")
    parser.add_argument("--email", default="cccc@gmail.com")
    parser.add_argument("--password", default="10012002")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--settings-id", default="ollama_settings")
    parser.add_argument("--server-id", default="qwen3-8b-new-server")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--output-dir", default="tmp/live-process-m123")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--request-delay-seconds", type=float, default=1.8)
    parser.add_argument("--http-max-retries", type=int, default=6)
    parser.add_argument("--http-retry-base-seconds", type=float, default=2.0)
    parser.add_argument("--soak-iterations", type=int, default=6)
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
        soak_iterations=max(2, int(args.soak_iterations)),
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

        summary.scenarios["process_m2"] = _run_m2(summary, config, token, user_id)
        summary.scenarios["process_m3"] = _run_m3(summary, config, token, user_id)
        summary.success = True
    except Exception as exc:  # pragma: no cover - runtime harness
        summary.error = str(exc)
        summary.success = False
    finally:
        output = _summary_to_json(summary)
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"summary_path": str(summary_path.resolve()), "success": summary.success},
                indent=2,
            )
        )
    return 0 if summary.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
