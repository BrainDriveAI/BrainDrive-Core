#!/usr/bin/env python3
"""Repeatable RNG onboarding interview test harness.

This script is designed for rapid dev loops:
1) run interview scenarios,
2) inspect artifacts,
3) adjust code/template,
4) rerun.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

QUESTION_PATTERN = re.compile(r"Question\s+(\d+)\s+of\s+(\d+):", re.IGNORECASE)

KICKOFF_VARIANTS = [
    "Start my Finance onboarding interview.",
    "Begin my finances interview.",
    "Let's start finance onboarding.",
]

APPROVE_VARIANTS = ["approve", "yes approve", "looks good", "go ahead"]
REJECT_VARIANTS = ["reject", "no, revise", "change it"]

ANSWER_VARIANTS = {
    "short": [
        "I want clear spending limits and less debt stress.",
        "I need better monthly planning and a savings habit.",
    ],
    "narrative": [
        "I keep up with bills but overspend in a few categories, and I want a cleaner monthly plan.",
        "I feel like my income is okay but my spending rhythm is inconsistent, so I want structure.",
    ],
    "bullets": [
        "- Goal: reduce credit card balance\n- Goal: build emergency fund\n- Constraint: variable expenses",
        "- Keep spending visible\n- Cut non-essential expenses\n- Build weekly review habit",
    ],
}

GOALS_TASKS_ABSOLUTE_VARIANTS = [
    "Goal: Save $600 by 2026-03-15. Task: weekly budget review every Monday.",
    "Goal: Pay $400 toward card balance by 2026-03-01. Task: track spending nightly.",
]

GOALS_TASKS_RELATIVE_VARIANTS = [
    "Goal: Save $150 by next Friday. Task: review spending by next week.",
    "Goal: Put $200 into emergency fund by month end. Task: adjust categories tomorrow.",
]

_ACTIVE_RUN: Optional["RunResult"] = None
_LAST_REQUEST_SENT_MONOTONIC: Optional[float] = None


class HarnessError(RuntimeError):
    """User-facing deterministic harness failure."""


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RunResult:
    run_id: str
    seed: int
    topic: str
    kickoff_phrase: str
    question_total: int = 0
    goals_branch: str = "unknown"
    used_relative_dates: bool = False
    rejected_opening_turns: int = 0
    rejected_goals_turns: int = 0
    expected_followup_due_date: str = ""
    followup_task_persisted: bool = False
    conversation_id: Optional[str] = None
    success: bool = False
    assertions: List[AssertionResult] = field(default_factory=list)
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class HarnessConfig:
    base_url: str
    email: str
    password: str
    provider: str
    settings_id: str
    server_id: str
    model: str
    topic: str
    runs: int
    seed: int
    output_dir: Path
    reset_from_template: bool
    library_root: Optional[Path]
    template_root: Optional[Path]
    reject_prob: float
    goals_yes_prob: float
    relative_date_prob: float
    timeout_seconds: int
    allow_legacy_completion_without_goals_prompt: bool
    request_delay_seconds: float
    http_max_retries: int
    http_retry_base_seconds: float


def _http_post(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout_seconds: int,
    token: Optional[str] = None,
    request_delay_seconds: float = 0.0,
    max_retries: int = 0,
    retry_base_seconds: float = 1.0,
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
                retry_after_header = exc.headers.get("Retry-After")
                retry_after_seconds: Optional[float] = None
                if retry_after_header:
                    try:
                        retry_after_seconds = float(retry_after_header.strip())
                    except ValueError:
                        retry_after_seconds = None
                backoff_seconds = retry_after_seconds or (
                    max(0.1, retry_base_seconds) * (2 ** attempt)
                )
                jitter_seconds = random.uniform(0.0, min(1.0, backoff_seconds * 0.25))
                time.sleep(min(backoff_seconds + jitter_seconds, 120.0))
                attempt += 1
                continue
            raise HarnessError(f"HTTP {exc.code} for {url}: {details}") from exc
        except urllib_error.URLError as exc:
            if attempt < max_retries:
                backoff_seconds = max(0.1, retry_base_seconds) * (2 ** attempt)
                jitter_seconds = random.uniform(0.0, min(1.0, backoff_seconds * 0.25))
                time.sleep(min(backoff_seconds + jitter_seconds, 120.0))
                attempt += 1
                continue
            raise HarnessError(f"Request failed for {url}: {exc}") from exc


def _normalize_user_id(user_id: str) -> str:
    return str(user_id).replace("-", "").strip()


def _login(config: HarnessConfig) -> Tuple[str, str]:
    login_url = f"{config.base_url.rstrip('/')}/api/v1/auth/login"
    payload = {"email": config.email, "password": config.password}
    login_max_retries = max(config.http_max_retries, 6)
    login_retry_base_seconds = max(config.http_retry_base_seconds, 5.0)
    response = _http_post(
        login_url,
        payload,
        timeout_seconds=config.timeout_seconds,
        request_delay_seconds=max(config.request_delay_seconds, 1.0),
        max_retries=login_max_retries,
        retry_base_seconds=login_retry_base_seconds,
    )

    token = response.get("access_token")
    user_id = response.get("user_id")
    if not isinstance(token, str) or not token.strip():
        raise HarnessError("Login response did not include access_token")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HarnessError("Login response did not include user_id")
    return token, user_id


def _bootstrap_user_scope(config: HarnessConfig, user_id: str) -> Dict[str, Any]:
    if not config.library_root:
        raise HarnessError(
            "--reset-from-template requires --library-root or BRAINDRIVE_LIBRARY_PATH"
        )

    normalized = _normalize_user_id(user_id)
    user_scope = config.library_root / "users" / normalized
    if user_scope.exists():
        shutil.rmtree(user_scope)

    script_path = Path(__file__).resolve().parents[1] / "backend" / "scripts" / "bootstrap_library_user_scope.py"
    cmd = [
        sys.executable,
        str(script_path),
        "--library-root",
        str(config.library_root),
        "--user-id",
        normalized,
    ]
    if config.template_root:
        cmd.extend(["--template-root", str(config.template_root)])

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise HarnessError(
            "Template reset/bootstrap failed: "
            f"stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
        )

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"Bootstrap script returned non-JSON output: {completed.stdout.strip()}"
        ) from exc


def _extract_question_state(text: str) -> Tuple[int, int]:
    match = QUESTION_PATTERN.search(text or "")
    if not match:
        raise HarnessError(f"Expected question pattern in assistant response, got: {text}")
    return int(match.group(1)), int(match.group(2))


def _pick_answer(rng: random.Random) -> str:
    style = rng.choice(list(ANSWER_VARIANTS.keys()))
    return rng.choice(ANSWER_VARIANTS[style])


def _build_chat_payload(
    *,
    config: HarnessConfig,
    user_id: str,
    message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "provider": config.provider,
        "settings_id": config.settings_id,
        "server_id": config.server_id,
        "model": config.model,
        "messages": [{"role": "user", "content": message}],
        "user_id": user_id,
        "conversation_type": f"life-{config.topic}",
        "params": {
            "mcp_tools_enabled": False,
            "mcp_scope_mode": "none",
            "mcp_sync_on_request": False,
        },
        "stream": False,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload


def _send_chat(
    *,
    config: HarnessConfig,
    token: str,
    user_id: str,
    message: str,
    conversation_id: Optional[str],
    run: RunResult,
) -> Dict[str, Any]:
    run.transcript.append({"role": "user", "content": message, "at": dt.datetime.now(dt.timezone.utc).isoformat()})

    chat_url = f"{config.base_url.rstrip('/')}/api/v1/ai/providers/chat"
    payload = _build_chat_payload(
        config=config,
        user_id=user_id,
        message=message,
        conversation_id=conversation_id,
    )
    response = _http_post(
        chat_url,
        payload,
        timeout_seconds=config.timeout_seconds,
        token=token,
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.http_max_retries,
        retry_base_seconds=config.http_retry_base_seconds,
    )

    assistant = (
        response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    run.transcript.append({"role": "assistant", "content": assistant, "at": dt.datetime.now(dt.timezone.utc).isoformat()})
    if not run.conversation_id:
        candidate_id = response.get("conversation_id")
        if isinstance(candidate_id, str) and candidate_id:
            run.conversation_id = candidate_id
    return response


def _assert(run: RunResult, name: str, condition: bool, detail: str = "") -> None:
    run.assertions.append(AssertionResult(name=name, passed=bool(condition), detail=detail))
    if not condition:
        raise HarnessError(f"Assertion failed: {name}. {detail}".strip())


def _as_assistant_text(response: Dict[str, Any]) -> str:
    return (
        response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )


def _expected_followup_due_date_iso() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).date().isoformat()


def _assert_followup_completion_text(run: RunResult, text: str, expected_due: str) -> None:
    lowered = (text or "").lower()
    _assert(run, "followup_task_mentioned", "follow-up task" in lowered or "followup task" in lowered, text)
    _assert(run, "followup_task_not_failed", "could not queue" not in lowered, text)
    _assert(run, "followup_due_date_is_plus_3_days", expected_due in (text or ""), text)


def _assert_followup_task_persisted(
    *,
    config: HarnessConfig,
    user_id: str,
    expected_due: str,
    run: RunResult,
) -> None:
    if not config.library_root:
        _assert(run, "followup_task_persisted_skipped_no_library_root", True, "")
        return

    normalized = _normalize_user_id(user_id)
    pulse_path = config.library_root / "users" / normalized / "pulse" / "index.md"
    _assert(run, "followup_pulse_index_exists", pulse_path.is_file(), str(pulse_path))
    content = pulse_path.read_text(encoding="utf-8")
    task_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [")]
    _assert(run, "pulse_task_line_present", bool(task_lines), content)
    followup_lines = [line for line in task_lines if "follow-up interview check-in" in line.lower()]
    _assert(run, "followup_task_line_present", bool(followup_lines), content)
    followup_line = followup_lines[-1]
    lowered = followup_line.lower()
    _assert(run, "followup_task_scope_finances", f"scope:life/{config.topic}" in lowered, followup_line)
    _assert(run, "followup_task_due_plus_3_days", f"due:{expected_due}" in lowered, followup_line)
    _assert(run, "followup_task_title_present", "follow-up interview check-in" in lowered, followup_line)
    run.followup_task_persisted = True


def _run_single(
    *,
    config: HarnessConfig,
    token: str,
    user_id: str,
    run_index: int,
) -> RunResult:
    global _ACTIVE_RUN
    run_seed = config.seed + run_index
    rng = random.Random(run_seed)
    run_id = f"run-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{run_index:02d}"
    kickoff = rng.choice(KICKOFF_VARIANTS)

    run = RunResult(
        run_id=run_id,
        seed=run_seed,
        topic=config.topic,
        kickoff_phrase=kickoff,
        expected_followup_due_date=_expected_followup_due_date_iso(),
    )
    _ACTIVE_RUN = run

    # Kickoff
    kickoff_response = _send_chat(
        config=config,
        token=token,
        user_id=user_id,
        message=kickoff,
        conversation_id=None,
        run=run,
    )
    conversation_id = run.conversation_id
    _assert(run, "conversation_id_created", isinstance(conversation_id, str) and bool(conversation_id))

    kickoff_text = _as_assistant_text(kickoff_response)
    q_index, q_total = _extract_question_state(kickoff_text)
    run.question_total = q_total
    _assert(run, "opening_question_index_starts_at_1", q_index == 1, kickoff_text)
    _assert(run, "opening_question_cap_le_6", q_total <= 6, kickoff_text)

    current_question_text = kickoff_text
    for expected_index in range(1, q_total + 1):
        _assert(
            run,
            f"question_{expected_index}_present",
            f"Question {expected_index} of {q_total}" in current_question_text,
            current_question_text,
        )

        answer = _pick_answer(rng)
        answer_response = _send_chat(
            config=config,
            token=token,
            user_id=user_id,
            message=answer,
            conversation_id=conversation_id,
            run=run,
        )
        answer_text = _as_assistant_text(answer_response)
        _assert(run, f"question_{expected_index}_asks_for_approval", "approve" in answer_text.lower(), answer_text)

        # Optional reject/revise branch for opening answers
        if rng.random() < config.reject_prob:
            run.rejected_opening_turns += 1
            reject_msg = rng.choice(REJECT_VARIANTS)
            reject_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message=reject_msg,
                conversation_id=conversation_id,
                run=run,
            )
            reject_text = _as_assistant_text(reject_response)
            _assert(
                run,
                f"question_{expected_index}_reject_keeps_turn_open",
                "question" in reject_text.lower() or "revise" in reject_text.lower(),
                reject_text,
            )

            revised_answer = _pick_answer(rng)
            revised_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message=revised_answer,
                conversation_id=conversation_id,
                run=run,
            )
            revised_text = _as_assistant_text(revised_response)
            _assert(
                run,
                f"question_{expected_index}_revised_asks_for_approval",
                "approve" in revised_text.lower(),
                revised_text,
            )

        approve_msg = rng.choice(APPROVE_VARIANTS)
        approve_response = _send_chat(
            config=config,
            token=token,
            user_id=user_id,
            message=approve_msg,
            conversation_id=conversation_id,
            run=run,
        )
        current_question_text = _as_assistant_text(approve_response)

    # Post-opening branch
    if "initial goals" in current_question_text.lower() or "goals or tasks" in current_question_text.lower():
        choose_goals = rng.random() < config.goals_yes_prob
        if choose_goals:
            run.goals_branch = "yes"
            yes_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message="yes",
                conversation_id=conversation_id,
                run=run,
            )
            yes_text = _as_assistant_text(yes_response)
            _assert(run, "goals_yes_branch_prompts_details", "goal" in yes_text.lower() or "task" in yes_text.lower(), yes_text)

            use_relative = rng.random() < config.relative_date_prob
            run.used_relative_dates = use_relative
            goals_text = rng.choice(
                GOALS_TASKS_RELATIVE_VARIANTS if use_relative else GOALS_TASKS_ABSOLUTE_VARIANTS
            )
            goals_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message=goals_text,
                conversation_id=conversation_id,
                run=run,
            )
            goals_capture_text = _as_assistant_text(goals_response)
            _assert(run, "goals_capture_requests_approval", "approve" in goals_capture_text.lower(), goals_capture_text)
            if use_relative:
                _assert(
                    run,
                    "relative_dates_are_resolved",
                    "resolved dates:" in goals_capture_text.lower(),
                    goals_capture_text,
                )

            if rng.random() < config.reject_prob:
                run.rejected_goals_turns += 1
                reject_goals = _send_chat(
                    config=config,
                    token=token,
                    user_id=user_id,
                    message=rng.choice(REJECT_VARIANTS),
                    conversation_id=conversation_id,
                    run=run,
                )
                reject_goals_text = _as_assistant_text(reject_goals)
                _assert(
                    run,
                    "goals_reject_requests_reentry",
                    "share" in reject_goals_text.lower() or "revise" in reject_goals_text.lower(),
                    reject_goals_text,
                )
                # Re-enter with absolute date to keep deterministic completion
                goals_retry = _send_chat(
                    config=config,
                    token=token,
                    user_id=user_id,
                    message=rng.choice(GOALS_TASKS_ABSOLUTE_VARIANTS),
                    conversation_id=conversation_id,
                    run=run,
                )
                retry_text = _as_assistant_text(goals_retry)
                _assert(run, "goals_retry_requests_approval", "approve" in retry_text.lower(), retry_text)

            finish_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message=rng.choice(APPROVE_VARIANTS),
                conversation_id=conversation_id,
                run=run,
            )
            finish_text = _as_assistant_text(finish_response)
            _assert(run, "goals_yes_branch_completes", "onboarding is complete" in finish_text.lower(), finish_text)
            _assert_followup_completion_text(run, finish_text, run.expected_followup_due_date)
            _assert_followup_task_persisted(
                config=config,
                user_id=user_id,
                expected_due=run.expected_followup_due_date,
                run=run,
            )
        else:
            run.goals_branch = "no"
            no_response = _send_chat(
                config=config,
                token=token,
                user_id=user_id,
                message="no",
                conversation_id=conversation_id,
                run=run,
            )
            no_text = _as_assistant_text(no_response)
            _assert(run, "goals_no_branch_completes", "onboarding is complete" in no_text.lower(), no_text)
            _assert_followup_completion_text(run, no_text, run.expected_followup_due_date)
            _assert_followup_task_persisted(
                config=config,
                user_id=user_id,
                expected_due=run.expected_followup_due_date,
                run=run,
            )
    else:
        run.goals_branch = "skipped"
        if config.allow_legacy_completion_without_goals_prompt:
            _assert(
                run,
                "opening_completion_prompt_present_or_legacy_completion",
                "onboarding is complete" in current_question_text.lower() or "goals" in current_question_text.lower(),
                current_question_text,
            )
        else:
            _assert(
                run,
                "opening_goals_prompt_present",
                "initial goals" in current_question_text.lower() or "goals or tasks" in current_question_text.lower(),
                current_question_text,
            )

    run.success = all(assertion.passed for assertion in run.assertions)
    _ACTIVE_RUN = None
    return run


def _write_run_artifacts(config: HarnessConfig, run: RunResult) -> Path:
    run_dir = config.output_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run.run_id,
        "seed": run.seed,
        "topic": run.topic,
        "success": run.success,
        "kickoff_phrase": run.kickoff_phrase,
        "question_total": run.question_total,
        "goals_branch": run.goals_branch,
        "used_relative_dates": run.used_relative_dates,
        "rejected_opening_turns": run.rejected_opening_turns,
        "rejected_goals_turns": run.rejected_goals_turns,
        "expected_followup_due_date": run.expected_followup_due_date,
        "followup_task_persisted": run.followup_task_persisted,
        "conversation_id": run.conversation_id,
        "error": run.error,
        "assertions": [
            {"name": item.name, "passed": item.passed, "detail": item.detail}
            for item in run.assertions
        ],
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (run_dir / "transcript.json").write_text(json.dumps(run.transcript, indent=2) + "\n", encoding="utf-8")

    transcript_lines = [f"# Transcript {run.run_id}", ""]
    for item in run.transcript:
        role = item.get("role", "unknown")
        at = item.get("at", "")
        content = item.get("content", "")
        transcript_lines.append(f"## {role} @ {at}")
        transcript_lines.append("")
        transcript_lines.append(str(content))
        transcript_lines.append("")
    (run_dir / "transcript.md").write_text("\n".join(transcript_lines) + "\n", encoding="utf-8")

    latest_pointer = {
        "latest_run_id": run.run_id,
        "summary_path": str((run_dir / "summary.json").resolve()),
        "updated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (config.output_dir / "latest-run.json").write_text(
        json.dumps(latest_pointer, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _parse_args() -> HarnessConfig:
    parser = argparse.ArgumentParser(description="RNG onboarding interview test harness")
    parser.add_argument("--base-url", default=os.getenv("BRAINDRIVE_E2E_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--email", default=os.getenv("BRAINDRIVE_E2E_EMAIL", ""))
    parser.add_argument("--password", default=os.getenv("BRAINDRIVE_E2E_PASSWORD", ""))
    parser.add_argument("--provider", default=os.getenv("BRAINDRIVE_E2E_PROVIDER", "ollama"))
    parser.add_argument("--settings-id", default=os.getenv("BRAINDRIVE_E2E_SETTINGS_ID", "ollama_settings"))
    parser.add_argument("--server-id", default=os.getenv("BRAINDRIVE_E2E_SERVER_ID", "ollama_default_server"))
    parser.add_argument("--model", default=os.getenv("BRAINDRIVE_E2E_MODEL", "qwen3:8b"))
    parser.add_argument("--topic", default="finances")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="tmp/onboarding-test-runs")
    parser.add_argument("--reset-from-template", action="store_true")
    parser.add_argument("--library-root", default=os.getenv("BRAINDRIVE_LIBRARY_PATH", ""))
    parser.add_argument("--template-root", default="")
    parser.add_argument("--reject-prob", type=float, default=0.25)
    parser.add_argument("--goals-yes-prob", type=float, default=0.6)
    parser.add_argument("--relative-date-prob", type=float, default=0.6)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=1.25,
        help="Minimum delay between HTTP requests to avoid hitting chat rate limits.",
    )
    parser.add_argument(
        "--http-max-retries",
        type=int,
        default=6,
        help="Maximum retries for HTTP 429 responses.",
    )
    parser.add_argument(
        "--http-retry-base-seconds",
        type=float,
        default=2.0,
        help="Base exponential backoff seconds for HTTP 429 retries.",
    )
    parser.add_argument(
        "--allow-legacy-completion-without-goals-prompt",
        action="store_true",
        help="Allow old completion behavior that skips goals/tasks prompt and follow-up assertions.",
    )
    parser.add_argument("--headful", action="store_true", help="Compatibility flag (no-op for API harness).")

    args = parser.parse_args()

    if not args.email or not args.password:
        raise SystemExit("Email/password are required. Use --email/--password or BRAINDRIVE_E2E_EMAIL/BRAINDRIVE_E2E_PASSWORD.")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    if args.topic != "finances":
        raise SystemExit("Initial harness currently supports --topic finances only.")

    library_root = Path(args.library_root).expanduser().resolve() if args.library_root else None
    template_root = Path(args.template_root).expanduser().resolve() if args.template_root else None

    return HarnessConfig(
        base_url=args.base_url,
        email=args.email,
        password=args.password,
        provider=args.provider,
        settings_id=args.settings_id,
        server_id=args.server_id,
        model=args.model,
        topic=args.topic,
        runs=args.runs,
        seed=args.seed,
        output_dir=Path(args.output_dir).expanduser().resolve(),
        reset_from_template=bool(args.reset_from_template),
        library_root=library_root,
        template_root=template_root,
        reject_prob=max(0.0, min(1.0, args.reject_prob)),
        goals_yes_prob=max(0.0, min(1.0, args.goals_yes_prob)),
        relative_date_prob=max(0.0, min(1.0, args.relative_date_prob)),
        timeout_seconds=max(10, args.timeout_seconds),
        allow_legacy_completion_without_goals_prompt=bool(
            args.allow_legacy_completion_without_goals_prompt
        ),
        request_delay_seconds=max(0.0, args.request_delay_seconds),
        http_max_retries=max(0, args.http_max_retries),
        http_retry_base_seconds=max(0.1, args.http_retry_base_seconds),
    )


def main() -> int:
    global _ACTIVE_RUN
    config = _parse_args()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    if config.reset_from_template and not config.library_root:
        raise SystemExit("--reset-from-template requires --library-root (or BRAINDRIVE_LIBRARY_PATH).")

    token, user_id = _login(config)
    print(f"Logged in user_id={user_id}")
    print(
        "Using response model configuration: "
        f"provider={config.provider}, settings_id={config.settings_id}, "
        f"server_id={config.server_id}, model={config.model}"
    )
    print(
        "Expected orchestration behavior: Granite handles tool-calling while the "
        "selected model handles natural-language response."
    )
    print(
        "Rate-limit pacing: "
        f"request_delay_seconds={config.request_delay_seconds}, "
        f"http_max_retries={config.http_max_retries}, "
        f"http_retry_base_seconds={config.http_retry_base_seconds}"
    )
    print(
        "Backend limits to respect: auth/login is 5 requests per 300s (IP), "
        "chat is 100 requests per 60s (user)."
    )
    print(
        "Login retry policy: "
        f"max_retries={max(config.http_max_retries, 6)}, "
        f"retry_base_seconds={max(config.http_retry_base_seconds, 5.0)}"
    )

    bootstrap_payload: Optional[Dict[str, Any]] = None
    if config.reset_from_template:
        bootstrap_payload = _bootstrap_user_scope(config, user_id)
        print("Template reset/bootstrap complete")

    aggregate: List[Dict[str, Any]] = []
    failed = 0

    for run_index in range(config.runs):
        if config.reset_from_template and run_index > 0:
            bootstrap_payload = _bootstrap_user_scope(config, user_id)

        run: Optional[RunResult] = None
        run_failed_via_exception = False
        try:
            run = _run_single(config=config, token=token, user_id=user_id, run_index=run_index)
        except Exception as exc:  # pragma: no cover - failure path capture
            if _ACTIVE_RUN is not None:
                run = _ACTIVE_RUN
                run.error = f"{exc}\n{traceback.format_exc()}"
                run.success = False
                _ACTIVE_RUN = None
            else:
                run_id = f"run-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{run_index:02d}"
                run = RunResult(
                    run_id=run_id,
                    seed=config.seed + run_index,
                    topic=config.topic,
                    kickoff_phrase="n/a",
                    success=False,
                    error=f"{exc}\n{traceback.format_exc()}",
                )
            run_failed_via_exception = True
            failed += 1

        if run and not run.success and not run_failed_via_exception:
            failed += 1

        run_dir = _write_run_artifacts(config, run)
        summary_path = run_dir / "summary.json"
        aggregate.append(
            {
                "run_id": run.run_id,
                "success": run.success,
                "seed": run.seed,
                "summary_path": str(summary_path),
            }
        )
        print(f"[{run.run_id}] success={run.success} summary={summary_path}")

    aggregate_payload = {
        "topic": config.topic,
        "runs": config.runs,
        "seed": config.seed,
        "provider": config.provider,
        "settings_id": config.settings_id,
        "server_id": config.server_id,
        "model": config.model,
        "reset_from_template": config.reset_from_template,
        "allow_legacy_completion_without_goals_prompt": config.allow_legacy_completion_without_goals_prompt,
        "bootstrap": bootstrap_payload,
        "results": aggregate,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    aggregate_path = config.output_dir / "aggregate-results.json"
    aggregate_path.write_text(json.dumps(aggregate_payload, indent=2) + "\n", encoding="utf-8")

    print(f"Aggregate results: {aggregate_path}")
    if failed:
        print(f"Completed with failures: {failed}")
        return 1
    print("All runs passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
