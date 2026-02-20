"""
API endpoints for AI providers.
"""
import os
import json
import copy
import time
import asyncio
import logging
import re
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit
from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from sqlalchemy.orm.attributes import flag_modified
from app.core.database import get_db
from app.core.auth_deps import require_user, optional_user
from app.core.auth_context import AuthContext
from app.core.rate_limit_deps import rate_limit_user
from app.models.settings import SettingDefinition, SettingScope, SettingInstance
from app.models.user import User
from app.ai_providers.registry import provider_registry
from app.ai_providers.ollama import OllamaProvider
from app.utils.json_parsing import safe_encrypted_json_parse, validate_ollama_settings_format, create_default_ollama_settings
from app.core.encryption import encryption_service, EncryptionError
from app.core.user_initializer.library_template import resolve_library_root_path
from app.schemas.ai_providers import (
    TextGenerationRequest,
    ChatCompletionRequest,
    ValidationRequest,
)
from app.utils.persona_utils import apply_persona_prompt_and_params
from app.services.mcp_registry_service import MCPRegistryService, infer_safety_class

# Flag to enable/disable test routes (set to False in production)
TEST_ROUTES_ENABLED = os.getenv("ENABLE_TEST_ROUTES", "True").lower() == "true"

router = APIRouter()
MODULE_LOGGER = logging.getLogger(__name__)

DEFAULT_AUTO_CONTINUE_PROMPT = "Continue exactly where you left off. Do not repeat prior text."
DEFAULT_AUTO_CONTINUE_MAX_PASSES = 2
DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS = 1
LIFE_ONBOARDING_TOPICS: Dict[str, str] = {
    "finances": "Finances",
    "fitness": "Fitness",
    "relationships": "Relationships",
    "career": "Career",
    "whyfinder": "WhyFinder",
}
DEFAULT_LIBRARY_MCP_PLUGIN_SLUG = "BrainDriveLibraryPlugin"
MAX_LIFE_ONBOARDING_OPENING_QUESTIONS = 6

LIFE_ONBOARDING_DEFAULT_QUESTIONS: Dict[str, List[str]] = {
    "finances": [
        "What matters most to you in finances over the next 90 days?",
        "What currently feels most stressful or unclear about your money situation?",
        "What income, expense, or debt patterns are affecting your progress the most?",
        "What savings or safety buffer are you aiming for first?",
        "If you could improve one financial habit this month, what would it be?",
        "What would make the next 30 days feel like a financial win?",
    ],
    "fitness": [
        "What fitness outcome matters most to you over the next 90 days?",
        "What does your current weekly routine look like?",
        "What gets in the way of consistency for you right now?",
        "What does success in the next 30 days look like for your health?",
    ],
    "relationships": [
        "What relationship area do you want to improve first?",
        "Where are things currently going well, and where do you feel tension?",
        "What boundaries or communication patterns need the most attention?",
        "What would meaningful progress look like in the next 30 days?",
    ],
    "career": [
        "What career outcome matters most to you over the next 90 days?",
        "Where do you feel stuck in your current work direction?",
        "What skills, opportunities, or constraints are shaping your next move?",
        "What would progress in the next 30 days look like?",
    ],
    "whyfinder": [
        "What values feel most important to you right now?",
        "Which life areas feel most out of alignment with those values?",
        "What priorities do you want to protect over the next 90 days?",
        "What would a meaningful next month look like if you stayed aligned?",
    ],
}

LIFE_ONBOARDING_KEYWORD_QUESTION_MAP: Dict[str, Dict[str, str]] = {
    "finances": {
        "income": "How stable is your current income, and what changes do you expect soon?",
        "budget": "How are you currently managing your budget, and where does it break down?",
        "spending": "Which spending categories are hardest to control right now?",
        "debt": "Which debts or liabilities feel most urgent to address first?",
        "save": "What savings target feels both meaningful and realistic right now?",
        "emergency": "Do you have an emergency cushion today, and what target would help you feel safer?",
        "invest": "How are you currently handling investing, if at all?",
        "retire": "How important is retirement planning in your current priorities?",
        "cash flow": "How predictable is your monthly cash flow right now?",
        "goal": "Which financial goal would create the biggest immediate momentum?",
    }
}

CAPTURE_LIFE_TOPIC_ALIASES: Dict[str, str] = {
    "finance": "finances",
    "finances": "finances",
    "fitness": "fitness",
    "relationship": "relationships",
    "relationships": "relationships",
    "career": "career",
    "whyfinder": "whyfinder",
    "why finder": "whyfinder",
}
CROSS_POLLINATION_TOPIC_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "relationships": (
        "kid",
        "kids",
        "child",
        "children",
        "daughter",
        "son",
        "spouse",
        "partner",
        "wife",
        "husband",
        "family",
        "parent",
    ),
    "fitness": (
        "workout",
        "exercise",
        "injury",
        "knee",
        "run",
        "running",
        "gym",
        "sleep",
        "nutrition",
        "diet",
        "steps",
    ),
    "finances": (
        "budget",
        "spending",
        "debt",
        "loan",
        "credit",
        "savings",
        "cash flow",
        "income",
        "expense",
        "mortgage",
        "retirement",
    ),
    "career": (
        "career",
        "job",
        "promotion",
        "manager",
        "interview",
        "resume",
        "salary",
        "work transition",
        "team lead",
    ),
    "whyfinder": (
        "values",
        "purpose",
        "mission",
        "meaning",
        "identity",
        "priority",
        "priorities",
    ),
}
CROSS_POLLINATION_CONTEXT_SOURCE_FILE = "AGENT.md"
CROSS_POLLINATION_CONTEXT_MAX_CHARS = 800

APPROVAL_MODE_POLICY = "explicit_approval_required"
MUTATION_CONTEXT_REMEDIATION_TOOLS = {
    "bootstrap_user_library",
    "create_project_scaffold",
    "create_project",
    "ensure_scope_scaffold",
}
ONBOARDING_CONTEXT_REQUIRED_TOOLS = {
    "start_topic_onboarding",
    "save_topic_onboarding_context",
}
OWNER_PROFILE_RELATIVE_PATH = "me/profile.md"
OWNER_PROFILE_MAX_CHARS = 4000
TOOL_PROFILE_FULL = "full"
TOOL_PROFILE_READ_ONLY = "read_only"
TOOL_PROFILE_DIGEST = "digest"
TOOL_PROFILE_NONE = "none"
NATIVE_TOOL_CALLING_PROVIDERS = {"openai", "claude", "openrouter", "groq"}
NATIVE_TOOL_MODEL_HINTS = (
    "gpt",
    "o1",
    "o3",
    "claude",
    "gemini",
    "qwen",
    "granite",
    "llama3",
    "llama-3",
    "mistral",
    "mixtral",
    "phi",
    "command-r",
    "deepseek",
)
DIGEST_TOOL_ALLOWLIST = {
    "digest_snapshot",
    "score_digest_tasks",
    "rollup_digest_period",
    "read_activity_log",
    "list_tasks",
}
DEFAULT_DIGEST_SECTIONS = (
    "top_priorities",
    "yesterday_wins",
    "needs_attention",
    "library_improvement",
)
ALLOWED_DIGEST_SECTIONS = set(DEFAULT_DIGEST_SECTIONS)
CAPTURE_PRIORITY_TOOL_NAMES = (
    "rollup_digest_period",
    "digest_snapshot",
    "score_digest_tasks",
    "create_markdown",
    "write_markdown",
    "create_task",
    "list_tasks",
    "update_task",
    "complete_task",
    "reopen_task",
)
DIGEST_PRIORITY_TOOL_NAMES = (
    "rollup_digest_period",
    "digest_snapshot",
    "score_digest_tasks",
    "read_activity_log",
    "list_tasks",
)
DUAL_PATH_LIFE_SCOPE_COMPAT_TOOL_ALLOWLIST = {
    "read_markdown",
    "search_markdown",
    "read_file_metadata",
    "list_tasks",
    "read_activity_log",
    "get_onboarding_state",
    "start_topic_onboarding",
    "save_topic_onboarding_context",
    "create_markdown",
    "write_markdown",
    "create_task",
    "update_task",
    "complete_task",
    "reopen_task",
    "ensure_scope_scaffold",
}
DUAL_PATH_PROJECT_SCOPE_COMPAT_TOOL_ALLOWLIST = {
    "read_markdown",
    "search_markdown",
    "read_file_metadata",
    "list_tasks",
    "read_activity_log",
    "create_markdown",
    "write_markdown",
    "edit_markdown",
    "create_task",
    "update_task",
    "complete_task",
    "reopen_task",
    "preview_markdown_change",
    "ensure_scope_scaffold",
}
DUAL_PATH_CAPTURE_SCOPE_COMPAT_TOOL_ALLOWLIST = {
    "read_markdown",
    "search_markdown",
    "read_file_metadata",
    "list_tasks",
    "read_activity_log",
    "create_markdown",
    "write_markdown",
    "create_task",
    "update_task",
    "complete_task",
    "reopen_task",
    "digest_snapshot",
    "score_digest_tasks",
    "rollup_digest_period",
    "ensure_scope_scaffold",
}
DUAL_PATH_NEW_PAGE_COMPAT_TOOL_ALLOWLIST = {
    "read_file_metadata",
    "read_markdown",
    "search_markdown",
    "project_exists",
    "create_project",
    "create_project_scaffold",
    "ensure_scope_scaffold",
    "create_markdown",
    "write_markdown",
    "preview_markdown_change",
}
DUAL_PATH_NEW_PAGE_INTERVIEW_COMPAT_TOOL_ALLOWLIST = {
    "read_file_metadata",
    "read_markdown",
    "search_markdown",
    "preview_markdown_change",
    "edit_markdown",
    "write_markdown",
}
DUAL_PATH_OWNER_PROFILE_COMPAT_TOOL_ALLOWLIST = {
    "read_markdown",
    "search_markdown",
    "read_file_metadata",
    "preview_markdown_change",
    "create_markdown",
    "write_markdown",
}
GROUNDING_SOURCE_TOOL_NAMES = {"read_markdown", "search_markdown"}
GROUNDING_PATH_KEYS = {"path", "file_path", "source_path"}
QUESTION_INTENT_PREFIXES = (
    "what",
    "when",
    "where",
    "who",
    "why",
    "how",
    "which",
    "did",
    "do",
    "does",
    "is",
    "are",
    "was",
    "were",
    "can",
    "could",
    "should",
    "would",
    "tell me",
    "show me",
    "find",
    "search",
)
MAX_RESPONSE_CITATIONS = 6
APPROVAL_PREVIEW_MAX_DIFF_CHARS = 6_000
APPROVAL_PREVIEW_TRUNCATED_NOTICE = "Diff truncated for approval preview."
APPROVAL_REQUIRED_RESPONSE_TEXT = (
    "Approval required before executing mutating tool call. "
    "Reply `approve` to continue or `reject` to cancel."
)
NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS = 6
NEW_PAGE_ENGINE_FIRST_FOLLOWUP_DAYS = 3
NEW_PAGE_INTERVIEW_STATE_KEY = "new_page_interview_deterministic"
NEW_PAGE_INTERVIEW_MAX_QUESTIONS = 6
NEW_PAGE_INTERVIEW_PREVIEW_MAX_ITEMS = 3
NEW_PAGE_INTERVIEW_APPROVAL_TEXT = (
    "Reply `approve` to apply these scoped updates, or `reject` to revise your answer."
)
COMPOUND_EDIT_SYNTHETIC_REASON = "compound_edit_followthrough"
COMPOUND_EDIT_MAX_OPERATIONS = 4
DIGEST_DELIVERY_HANDOFF_MAX_CHARS = 12_000
ENV_DIGEST_DELIVERY_OUTBOX_PATH = "BRAINDRIVE_DIGEST_DELIVERY_OUTBOX_PATH"
DIGEST_DELIVERY_SEND_TIMEOUT_SECONDS = 8.0
DIGEST_DELIVERY_SEND_MAX_ERROR_CHARS = 1_200
ENV_DIGEST_DELIVERY_SEND_ENABLED = "BRAINDRIVE_DIGEST_DELIVERY_SEND_ENABLED"
ENV_DIGEST_DELIVERY_ENDPOINT = "BRAINDRIVE_DIGEST_DELIVERY_ENDPOINT"
ENV_DIGEST_DELIVERY_TIMEOUT_SECONDS = "BRAINDRIVE_DIGEST_DELIVERY_TIMEOUT_SECONDS"
ENV_DIGEST_DELIVERY_AUTH_TOKEN = "BRAINDRIVE_DIGEST_DELIVERY_AUTH_TOKEN"
ENV_DIGEST_DELIVERY_HEADERS_JSON = "BRAINDRIVE_DIGEST_DELIVERY_HEADERS_JSON"
OWNER_PROFILE_UPDATE_WRITE_REASON = "owner_profile_update_write"
OWNER_PROFILE_UPDATE_CREATE_REASON = "owner_profile_update_create"
OWNER_PROFILE_UPDATE_REASONS = {
    OWNER_PROFILE_UPDATE_WRITE_REASON,
    OWNER_PROFILE_UPDATE_CREATE_REASON,
}
CAPTURE_SCOPE_FANOUT_MAX_TARGETS = 3
NEW_PAGE_ENGINE_LIFE_KEYWORDS = (
    "finance",
    "finances",
    "fitness",
    "health",
    "relationship",
    "relationships",
    "career",
    "whyfinder",
    "habit",
    "family",
    "wellness",
    "budget",
    "debt",
    "relationship",
    "values",
)
PRE_COMPACTION_FLUSH_PROMPT_TEMPLATE = (
    "[pre_compaction_flush_event:{event_id}] Context usage is near the configured limit. "
    "Before any summarization/truncation step, run one silent persistence sweep: identify any "
    "important unsaved context and propose minimal library writes via tool calls. If nothing "
    "new needs persistence, continue without tool calls. Do not run this flush twice for the same event."
)
DIGEST_SCHEDULE_PROMPT_TEMPLATE = (
    "[digest_schedule_event:{event_id}] Scheduled digest run is due. "
    "Sections requested: {sections}. "
    "Produce a concise digest summary for this run, including one actionable library-improvement suggestion. "
    "Use tools to ground the output: call digest_snapshot first, then score_digest_tasks using snapshot tasks, "
    "then rollup_digest_period for week when writes/checkpoints are needed. "
    "If no persistence is needed, continue without mutating calls."
)


def _normalize_page_id(page_id: Any) -> Optional[str]:
    if not isinstance(page_id, str):
        return None
    normalized = page_id.strip()
    return normalized if normalized else None


def _normalize_library_user_id(raw_user_id: Any) -> Optional[str]:
    if raw_user_id is None:
        return None
    normalized = str(raw_user_id).strip().replace("-", "")
    if not normalized:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_]{3,128}", normalized):
        return None
    return normalized


def _build_owner_profile_system_message(
    user_id: Optional[str],
) -> tuple[Optional[Dict[str, str]], Dict[str, Any]]:
    metadata: Dict[str, Any] = {
        "owner_profile_loaded": False,
        "owner_profile_path": OWNER_PROFILE_RELATIVE_PATH,
    }

    normalized_user_id = _normalize_library_user_id(user_id)
    if not normalized_user_id:
        metadata["owner_profile_status"] = "invalid_user_id"
        return None, metadata

    try:
        library_root = resolve_library_root_path()
    except Exception:
        metadata["owner_profile_status"] = "library_root_unavailable"
        return None, metadata

    profile_path: Path = library_root / "users" / normalized_user_id / OWNER_PROFILE_RELATIVE_PATH
    if not profile_path.is_file():
        metadata["owner_profile_status"] = "missing"
        return None, metadata

    try:
        raw_content = profile_path.read_text(encoding="utf-8")
    except Exception:
        metadata["owner_profile_status"] = "read_error"
        return None, metadata

    content = raw_content.strip()
    if not content:
        metadata["owner_profile_status"] = "empty"
        return None, metadata

    truncated = False
    if len(content) > OWNER_PROFILE_MAX_CHARS:
        content = content[:OWNER_PROFILE_MAX_CHARS].rstrip()
        content = f"{content}\n\n[Profile context truncated for prompt budget.]"
        truncated = True

    metadata.update(
        {
            "owner_profile_loaded": True,
            "owner_profile_status": "loaded",
            "owner_profile_chars": len(content),
            "owner_profile_truncated": truncated,
        }
    )

    system_message = {
        "role": "system",
        "content": (
            "Owner profile context (from me/profile.md). "
            "Use this as concise background context when relevant.\n\n"
            f"{content}"
        ),
    }
    return system_message, metadata


def extract_finish_reason(chunk: Dict[str, Any]) -> Optional[str]:
    """Extract finish_reason from provider chunk variants."""
    if not isinstance(chunk, dict):
        return None

    finish_reason = chunk.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason.strip():
        return finish_reason.strip()

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason.strip():
            return finish_reason.strip()

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        done_reason = metadata.get("done_reason")
        if isinstance(done_reason, str) and done_reason.strip():
            return done_reason.strip()

    return None


def extract_chunk_content(chunk: Dict[str, Any]) -> str:
    """Extract text content from provider chunk variants."""
    if not isinstance(chunk, dict):
        return ""

    text = chunk.get("text")
    if isinstance(text, str):
        return text

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content

        choice_text = choice.get("text")
        if isinstance(choice_text, str):
            return choice_text

    return ""


def extract_response_content(result: Dict[str, Any]) -> str:
    """Extract assistant text from a non-stream provider response."""
    if not isinstance(result, dict):
        return ""

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        text_value = first_choice.get("text")
        if isinstance(text_value, str):
            return text_value

    direct_content = result.get("content")
    if isinstance(direct_content, str):
        return direct_content

    direct_text = result.get("text")
    if isinstance(direct_text, str):
        return direct_text

    message = result.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content

    return ""


def _set_result_primary_content(result: Dict[str, Any], content: str) -> Dict[str, Any]:
    if not isinstance(result, dict):
        result = {}

    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        choices = [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]
        result["choices"] = choices
        return result

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
    message["role"] = message.get("role") or "assistant"
    message["content"] = content
    first_choice["message"] = message
    choices[0] = first_choice
    result["choices"] = choices
    return result


def _is_likely_question_request(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False
    normalized = message_text.strip().lower()
    if not normalized:
        return False
    if "?" in normalized:
        return True
    return any(normalized.startswith(prefix + " ") for prefix in QUESTION_INTENT_PREFIXES)


def _normalize_grounding_path(
    value: Any,
    *,
    default_scope_path: Optional[str] = None,
) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        normalized = normalized.lstrip("/")
    if re.match(r"^(life|projects|capture|digest|me)/", normalized):
        return normalized

    scope_path = _normalize_project_scope_path(default_scope_path)
    if not isinstance(scope_path, str) or not scope_path.strip():
        return None
    scoped = _normalize_scoped_tool_path(normalized, scope_path)
    if not isinstance(scoped, str):
        return None
    scoped_normalized = scoped.strip().replace("\\", "/")
    scoped_normalized = re.sub(r"^\./+", "", scoped_normalized)
    if scoped_normalized.startswith("/"):
        scoped_normalized = scoped_normalized.lstrip("/")
    if not re.match(r"^(life|projects|capture|digest|me)/", scoped_normalized):
        return None
    return scoped_normalized


def _extract_grounding_paths_from_payload(
    payload: Any,
    *,
    default_scope_path: Optional[str] = None,
) -> List[str]:
    extracted: List[str] = []

    def _walk(value: Any) -> None:
        if len(extracted) >= 64:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and key.strip().lower() in GROUNDING_PATH_KEYS:
                    normalized_path = _normalize_grounding_path(
                        item,
                        default_scope_path=default_scope_path,
                    )
                    if normalized_path:
                        extracted.append(normalized_path)
                if isinstance(item, (dict, list)):
                    _walk(item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    _walk(item)

    _walk(payload)
    return extracted


def _collect_grounding_source_paths(
    executed_tool_calls: List[Dict[str, Any]],
    *,
    default_scope_path: Optional[str] = None,
) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()

    for call in executed_tool_calls:
        if not isinstance(call, dict):
            continue
        if str(call.get("status") or "").strip().lower() != "success":
            continue
        tool_name = str(call.get("name") or "").strip()
        if tool_name not in GROUNDING_SOURCE_TOOL_NAMES:
            continue

        call_paths = _extract_grounding_paths_from_payload(
            call.get("arguments"),
            default_scope_path=default_scope_path,
        )
        call_paths.extend(
            _extract_grounding_paths_from_payload(
                call.get("result"),
                default_scope_path=default_scope_path,
            )
        )
        for path in call_paths:
            lowered = path.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(path)

    return deduped


def _response_already_references_grounding_path(
    response_content: str,
    grounded_paths: List[str],
) -> bool:
    if not isinstance(response_content, str) or not response_content.strip():
        return False
    lowered = response_content.lower()
    return any(path.lower() in lowered for path in grounded_paths)


def _build_grounding_citation_suffix(grounded_paths: List[str]) -> Optional[str]:
    if not grounded_paths:
        return None
    lines = ["Sources:"]
    for path in grounded_paths[:MAX_RESPONSE_CITATIONS]:
        lines.append(f"- `{path}`")
    return "\n\n" + "\n".join(lines)


def _apply_grounding_citations_if_needed(
    *,
    response_content: str,
    executed_tool_calls: List[Dict[str, Any]],
    latest_user_message: Optional[str],
    tool_loop_stop_reason: str,
    default_scope_path: Optional[str] = None,
) -> tuple[str, Optional[str], List[str], bool]:
    if (
        not isinstance(response_content, str)
        or not response_content.strip()
        or str(tool_loop_stop_reason or "").strip() != "provider_final_response"
        or not _is_likely_question_request(latest_user_message)
    ):
        return response_content, None, [], False

    grounded_paths = _collect_grounding_source_paths(
        executed_tool_calls,
        default_scope_path=default_scope_path,
    )
    if not grounded_paths:
        return response_content, None, [], False

    if _response_already_references_grounding_path(response_content, grounded_paths):
        return response_content, None, grounded_paths, False

    citation_suffix = _build_grounding_citation_suffix(grounded_paths)
    if not citation_suffix:
        return response_content, None, grounded_paths, False

    merged = response_content.rstrip() + citation_suffix
    return merged, citation_suffix, grounded_paths, True


async def iter_provider_stream_with_timeout(
    stream_iterable: Any,
    *,
    timeout_seconds: float,
):
    """Iterate provider stream chunks with a hard per-pass timeout budget."""
    iterator = stream_iterable.__aiter__()
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        yield chunk


def _provider_timeout_fallback_response(conversation_type: str) -> str:
    normalized = str(conversation_type or "").strip().lower()
    if _is_capture_intake_conversation(normalized):
        return (
            "I hit a response timeout while processing this capture request. "
            "Please resend the same note or split it into shorter steps."
        )
    if _is_digest_conversation(normalized):
        return (
            "I hit a response timeout while preparing the digest flow. "
            "Please retry the digest request."
        )
    return (
        "I hit a response timeout while generating that reply. "
        "Please retry the request."
    )


def _build_provider_timing_metadata(
    *,
    provider_timeout_seconds: float,
    provider_call_latencies_ms: List[int],
    provider_timeout_count: int,
) -> Dict[str, Any]:
    call_count = len(provider_call_latencies_ms)
    total_latency_ms = int(sum(provider_call_latencies_ms))
    avg_latency_ms = round((total_latency_ms / call_count), 1) if call_count else 0.0
    max_latency_ms = int(max(provider_call_latencies_ms)) if provider_call_latencies_ms else 0
    last_latency_ms = int(provider_call_latencies_ms[-1]) if provider_call_latencies_ms else 0
    return {
        "provider_timeout_seconds": round(float(provider_timeout_seconds), 3),
        "provider_call_count": call_count,
        "provider_timeout_count": int(provider_timeout_count),
        "provider_latency_ms_total": total_latency_ms,
        "provider_latency_ms_avg": avg_latency_ms,
        "provider_latency_ms_max": max_latency_ms,
        "provider_latency_ms_last": last_latency_ms,
    }


def _decode_tool_arguments(raw_arguments: Any) -> Dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        raw = raw_arguments.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_response_tool_calls(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize tool call payloads from provider responses."""
    if not isinstance(result, dict):
        return []

    candidates: List[Dict[str, Any]] = []

    top_level = result.get("tool_calls")
    if isinstance(top_level, list):
        candidates.extend([item for item in top_level if isinstance(item, dict)])

    message = result.get("message")
    if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
        candidates.extend(
            [item for item in message.get("tool_calls", []) if isinstance(item, dict)]
        )

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        choice_message = first_choice.get("message")
        if isinstance(choice_message, dict) and isinstance(choice_message.get("tool_calls"), list):
            candidates.extend(
                [item for item in choice_message.get("tool_calls", []) if isinstance(item, dict)]
            )

    normalized_calls: List[Dict[str, Any]] = []
    for call in candidates:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        raw_arguments = function.get("arguments")
        if raw_arguments is None:
            raw_arguments = call.get("arguments")
        arguments = _decode_tool_arguments(raw_arguments)
        normalized_calls.append(
            {
                "id": call.get("id"),
                "name": name.strip(),
                "arguments": arguments,
                "raw_arguments": raw_arguments,
            }
        )

    return normalized_calls


def extract_chunk_tool_call_deltas(chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool-call deltas from a streaming chunk."""
    if not isinstance(chunk, dict):
        return []

    candidates: List[Dict[str, Any]] = []

    top_level = chunk.get("tool_calls")
    if isinstance(top_level, list):
        candidates.extend([item for item in top_level if isinstance(item, dict)])

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = first_choice.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("tool_calls"), list):
            candidates.extend(
                [item for item in delta.get("tool_calls", []) if isinstance(item, dict)]
            )
        message = first_choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
            candidates.extend(
                [item for item in message.get("tool_calls", []) if isinstance(item, dict)]
            )

    normalized: List[Dict[str, Any]] = []
    for item in candidates:
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        normalized.append(
            {
                "id": item.get("id"),
                "index": item.get("index"),
                "name": function.get("name") or item.get("name"),
                "arguments": function.get("arguments", item.get("arguments")),
            }
        )
    return normalized


def update_stream_tool_call_buffer(
    buffer: Dict[str, Dict[str, Any]],
    deltas: List[Dict[str, Any]],
) -> None:
    for delta in deltas:
        raw_key = delta.get("id")
        if not isinstance(raw_key, str) or not raw_key.strip():
            index_value = delta.get("index")
            if isinstance(index_value, int):
                raw_key = f"idx:{index_value}"
            else:
                raw_key = f"idx:{len(buffer)}"

        entry = buffer.get(raw_key)
        if entry is None:
            entry = {
                "id": delta.get("id"),
                "index": delta.get("index"),
                "name": None,
                "arguments_buffer": "",
                "arguments_object": None,
            }
            buffer[raw_key] = entry

        name = delta.get("name")
        if isinstance(name, str) and name.strip():
            entry["name"] = name.strip()

        arguments = delta.get("arguments")
        if isinstance(arguments, str):
            entry["arguments_buffer"] += arguments
        elif isinstance(arguments, dict):
            existing_object = entry.get("arguments_object")
            if isinstance(existing_object, dict):
                existing_object.update(arguments)
                entry["arguments_object"] = existing_object
            else:
                entry["arguments_object"] = dict(arguments)


def finalize_stream_tool_calls(buffer: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = list(buffer.values())
    records.sort(
        key=lambda item: (
            item.get("index") if isinstance(item.get("index"), int) else 10_000,
            item.get("id") or "",
        )
    )

    finalized: List[Dict[str, Any]] = []
    for item in records:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        arguments: Dict[str, Any] = {}
        buffer_text = item.get("arguments_buffer")
        if isinstance(buffer_text, str) and buffer_text.strip():
            parsed = _decode_tool_arguments(buffer_text)
            if isinstance(parsed, dict):
                arguments = parsed

        if not arguments and isinstance(item.get("arguments_object"), dict):
            arguments = dict(item["arguments_object"])

        finalized.append(
            {
                "id": item.get("id"),
                "name": name.strip(),
                "arguments": arguments,
                "raw_arguments": buffer_text or item.get("arguments_object"),
            }
        )

    return finalized


def is_truncation_finish_reason(
    reason: Optional[str],
    provider: Optional[str] = None,
    chunk: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when finish_reason indicates token-limit truncation."""
    if not reason:
        if not isinstance(chunk, dict):
            return False
        metadata = chunk.get("metadata")
        done_reason = metadata.get("done_reason") if isinstance(metadata, dict) else None
        reason = done_reason if isinstance(done_reason, str) else None
        if not reason:
            return False

    normalized = reason.strip().lower()
    provider_name = (provider or "").strip().lower()

    truncation_reasons = {
        "length",
        "max_tokens",
        "max_token",
        "max_output_tokens",
        "token_limit",
    }
    if provider_name == "claude":
        truncation_reasons.update({"max_tokens", "length"})
    if provider_name == "ollama":
        truncation_reasons.update({"length", "max_tokens"})

    if normalized in truncation_reasons:
        return True

    return "length" in normalized or "max_tokens" in normalized


def build_continuation_messages(
    base_messages: List[Dict[str, Any]],
    full_response: str,
    continuation_prompt: str = DEFAULT_AUTO_CONTINUE_PROMPT,
) -> List[Dict[str, Any]]:
    """Build a continuation prompt using the merged assistant response so far."""
    return [
        *base_messages,
        {"role": "assistant", "content": full_response},
        {"role": "user", "content": continuation_prompt},
    ]


def _as_bool(value: Any, default: bool) -> bool:
    """Parse permissive bool values from request params."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "off", ""}:
            return False
        if lowered in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


def _as_optional_bool(value: Any) -> Optional[bool]:
    """Parse optional bool values and preserve None when not provided/parsable."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "off"}:
            return False
        if lowered in {"true", "1", "yes", "on"}:
            return True
    return None


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse bounded int values from request params."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Parse bounded float values from request params."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_tool_profile(value: Any, *, default: str, allow_auto: bool = False) -> str:
    allowed = {
        TOOL_PROFILE_FULL,
        TOOL_PROFILE_READ_ONLY,
        TOOL_PROFILE_DIGEST,
        TOOL_PROFILE_NONE,
    }
    if allow_auto:
        allowed.add("auto")

    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in allowed:
            return normalized
    return default


def _is_model_likely_native_tool_capable(provider: Any, model: Any) -> tuple[bool, str]:
    provider_normalized = str(provider or "").strip().lower()
    model_normalized = str(model or "").strip().lower()

    if provider_normalized in NATIVE_TOOL_CALLING_PROVIDERS:
        return True, "provider_default"

    if provider_normalized == "ollama":
        # Default local Ollama models to compatibility mode unless explicitly overridden.
        return False, "provider_default_non_native"

    if any(hint in model_normalized for hint in NATIVE_TOOL_MODEL_HINTS):
        return True, "model_hint"

    return False, "unknown"


def _resolve_tool_routing_decision(
    *,
    provider: Any,
    model: Any,
    provider_params: Dict[str, Any],
) -> Dict[str, Any]:
    force_single_path = _as_bool(provider_params.pop("mcp_force_single_path", False), False)
    force_dual_path = _as_bool(provider_params.pop("mcp_force_dual_path", False), False)
    requested_tool_profile = _coerce_tool_profile(
        provider_params.pop("mcp_tool_profile", None),
        default="auto",
        allow_auto=True,
    )
    native_tool_override = _as_optional_bool(provider_params.pop("mcp_native_tool_calling", None))

    if native_tool_override is None:
        native_tool_calling, capability_source = _is_model_likely_native_tool_capable(
            provider,
            model,
        )
    else:
        native_tool_calling = native_tool_override
        capability_source = "request_override"

    dual_path_requested = False
    route_mode = "single_path_compat"
    if force_single_path and force_dual_path:
        force_dual_path = False
    if force_single_path:
        route_mode = "single_path_forced"
    elif force_dual_path:
        dual_path_requested = True
        route_mode = "dual_path_fallback"
    elif native_tool_calling:
        route_mode = "single_path_native"
    else:
        dual_path_requested = True
        route_mode = "dual_path_fallback"

    if requested_tool_profile != "auto":
        tool_profile = requested_tool_profile
        tool_profile_source = "request"
    elif route_mode == "single_path_native":
        tool_profile = TOOL_PROFILE_FULL
        tool_profile_source = "routing_default"
    elif route_mode == "single_path_forced":
        tool_profile = TOOL_PROFILE_FULL
        tool_profile_source = "routing_forced"
    else:
        tool_profile = TOOL_PROFILE_READ_ONLY
        tool_profile_source = "routing_default"

    allowed_safety_classes: Optional[List[str]] = None
    tool_name_allowlist: Optional[List[str]] = None
    disable_tools = False
    if tool_profile == TOOL_PROFILE_READ_ONLY:
        allowed_safety_classes = [TOOL_PROFILE_READ_ONLY]
    elif tool_profile == TOOL_PROFILE_DIGEST:
        allowed_safety_classes = [TOOL_PROFILE_READ_ONLY, "mutating"]
        tool_name_allowlist = sorted(DIGEST_TOOL_ALLOWLIST)
    elif tool_profile == TOOL_PROFILE_NONE:
        disable_tools = True

    dual_path_fallback_reason = None
    execution_mode = route_mode
    if route_mode == "dual_path_fallback":
        dual_path_fallback_reason = "dual_path_runtime_unavailable_using_single_path_compat"
        execution_mode = "single_path_compat"

    return {
        "route_mode": route_mode,
        "execution_mode": execution_mode,
        "dual_path_requested": dual_path_requested,
        "dual_path_fallback_reason": dual_path_fallback_reason,
        "native_tool_calling": native_tool_calling,
        "capability_source": capability_source,
        "tool_profile": tool_profile,
        "tool_profile_source": tool_profile_source,
        "disable_tools": disable_tools,
        "allowed_safety_classes": allowed_safety_classes,
        "tool_name_allowlist": tool_name_allowlist,
    }


def _resolve_priority_tool_names(
    *,
    conversation_type: str,
    tool_profile: str,
    tool_name_allowlist: Optional[List[str]],
) -> List[str]:
    ordered: List[str] = []
    seen = set()

    def _append(name: Any) -> None:
        normalized = str(name or "").strip()
        if not normalized or normalized in seen:
            return
        ordered.append(normalized)
        seen.add(normalized)

    if _is_capture_intake_conversation(conversation_type):
        for name in CAPTURE_PRIORITY_TOOL_NAMES:
            _append(name)
    if tool_profile == TOOL_PROFILE_DIGEST:
        for name in DIGEST_PRIORITY_TOOL_NAMES:
            _append(name)
    for name in (tool_name_allowlist or []):
        _append(name)
    return ordered


def _estimate_message_token_count(messages: List[Dict[str, Any]]) -> int:
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            try:
                total_chars += len(json.dumps(tool_calls, ensure_ascii=True))
            except Exception:
                total_chars += 0
    if total_chars <= 0:
        return 0
    # Rough conversion that is stable enough for threshold detection.
    return max(1, int(total_chars / 4))


def _extract_pre_compaction_flush_config(provider_params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = _as_bool(provider_params.pop("mcp_pre_compaction_flush_enabled", True), True)
    context_window_tokens = _as_int(
        provider_params.pop("mcp_context_window_tokens", 0),
        default=0,
        minimum=0,
        maximum=2_000_000,
    )
    threshold = _as_float(
        provider_params.pop("mcp_pre_compaction_flush_threshold", 0.9),
        default=0.9,
        minimum=0.5,
        maximum=0.99,
    )
    event_id_raw = provider_params.pop("mcp_pre_compaction_event_id", None)
    event_id = str(event_id_raw).strip() if isinstance(event_id_raw, str) else ""

    return {
        "enabled": enabled,
        "context_window_tokens": context_window_tokens,
        "threshold": threshold,
        "event_id": event_id,
    }


def _normalize_digest_section_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "priorities": "top_priorities",
        "top3": "top_priorities",
        "top_3": "top_priorities",
        "yesterday": "yesterday_wins",
        "wins": "yesterday_wins",
        "needs_attention": "needs_attention",
        "attention": "needs_attention",
        "blocked_overdue": "needs_attention",
        "library_improvement": "library_improvement",
        "improvement": "library_improvement",
    }
    normalized = alias_map.get(normalized, normalized)
    if normalized in ALLOWED_DIGEST_SECTIONS:
        return normalized
    return None


def _coerce_digest_sections(value: Any) -> List[str]:
    raw_values: List[Any]
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = [item for item in value.split(",")]
    else:
        raw_values = list(DEFAULT_DIGEST_SECTIONS)

    sections: List[str] = []
    seen = set()
    for raw in raw_values:
        normalized = _normalize_digest_section_name(raw)
        if not normalized or normalized in seen:
            continue
        sections.append(normalized)
        seen.add(normalized)
    return sections or list(DEFAULT_DIGEST_SECTIONS)


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _extract_digest_schedule_config(
    provider_params: Dict[str, Any],
    *,
    conversation_type: str,
) -> Dict[str, Any]:
    normalized_type = _normalize_conversation_type(conversation_type)
    digest_conversation = _is_digest_conversation(normalized_type) and not _is_digest_reply_conversation(
        normalized_type
    )
    enabled_default = True if digest_conversation else False
    enabled = _as_bool(provider_params.pop("mcp_digest_schedule_enabled", enabled_default), enabled_default)
    cadence_hours = _as_int(
        provider_params.pop("mcp_digest_cadence_hours", 24),
        default=24,
        minimum=1,
        maximum=168,
    )
    sections = _coerce_digest_sections(
        provider_params.pop("mcp_digest_sections", list(DEFAULT_DIGEST_SECTIONS))
    )
    force_run = _as_bool(provider_params.pop("mcp_digest_force_run", False), False)
    next_due_raw = provider_params.pop(
        "mcp_digest_next_due_at_utc",
        provider_params.pop("mcp_digest_due_at_utc", None),
    )
    next_due_dt = _parse_utc_datetime(next_due_raw)
    event_id_raw = provider_params.pop("mcp_digest_schedule_event_id", None)
    event_id = str(event_id_raw).strip() if isinstance(event_id_raw, str) else ""
    reply_to_capture_enabled = _as_bool(
        provider_params.pop("mcp_digest_reply_to_capture_enabled", True),
        True,
    )
    now_utc = datetime.now(timezone.utc)

    due_now = False
    if enabled:
        due_now = force_run or next_due_dt is None or next_due_dt <= now_utc

    projected_next_due = next_due_dt
    if enabled and due_now:
        projected_next_due = now_utc + timedelta(hours=cadence_hours)

    return {
        "enabled": enabled,
        "due_now": due_now,
        "force_run": force_run,
        "cadence_hours": cadence_hours,
        "sections": sections,
        "next_due_at_utc": projected_next_due.isoformat() if projected_next_due else None,
        "event_id": event_id,
        "reply_to_capture_enabled": reply_to_capture_enabled,
    }


def _sanitize_delivery_endpoint_for_metadata(raw_endpoint: Any) -> Optional[str]:
    if not isinstance(raw_endpoint, str):
        return None
    endpoint = raw_endpoint.strip()
    if not endpoint:
        return None
    try:
        parts = urlsplit(endpoint)
    except Exception:
        return endpoint
    if not parts.scheme or not parts.netloc:
        return endpoint
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
    else:
        netloc = parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _coerce_digest_delivery_headers(raw_value: Any) -> Dict[str, str]:
    if isinstance(raw_value, dict):
        source = raw_value
    elif isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = json.loads(raw_value)
        except Exception:
            parsed = None
        source = parsed if isinstance(parsed, dict) else {}
    else:
        source = {}

    headers: Dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str):
            continue
        key_normalized = key.strip()
        if not key_normalized:
            continue
        if isinstance(value, str):
            value_normalized = value.strip()
        else:
            value_normalized = str(value).strip() if value is not None else ""
        if not value_normalized:
            continue
        headers[key_normalized] = value_normalized
    return headers


def _extract_digest_delivery_send_config(provider_params: Dict[str, Any]) -> Dict[str, Any]:
    env_enabled_default = _as_bool(
        os.getenv(ENV_DIGEST_DELIVERY_SEND_ENABLED, "false"),
        False,
    )
    enabled = _as_bool(
        provider_params.pop("mcp_digest_delivery_send_enabled", env_enabled_default),
        env_enabled_default,
    )
    endpoint_value = provider_params.pop(
        "mcp_digest_delivery_endpoint",
        os.getenv(ENV_DIGEST_DELIVERY_ENDPOINT, ""),
    )
    endpoint = str(endpoint_value).strip() if isinstance(endpoint_value, str) else ""
    timeout_default = _as_float(
        os.getenv(
            ENV_DIGEST_DELIVERY_TIMEOUT_SECONDS,
            str(DIGEST_DELIVERY_SEND_TIMEOUT_SECONDS),
        ),
        default=DIGEST_DELIVERY_SEND_TIMEOUT_SECONDS,
        minimum=0.5,
        maximum=60.0,
    )
    timeout_seconds = _as_float(
        provider_params.pop("mcp_digest_delivery_timeout_seconds", timeout_default),
        default=timeout_default,
        minimum=0.5,
        maximum=60.0,
    )
    headers_raw = provider_params.pop(
        "mcp_digest_delivery_headers",
        os.getenv(ENV_DIGEST_DELIVERY_HEADERS_JSON, ""),
    )
    headers = _coerce_digest_delivery_headers(headers_raw)
    token_value = provider_params.pop(
        "mcp_digest_delivery_auth_token",
        os.getenv(ENV_DIGEST_DELIVERY_AUTH_TOKEN, ""),
    )
    token = str(token_value).strip() if isinstance(token_value, str) else ""
    lower_header_keys = {key.lower() for key in headers.keys()}
    if token and "authorization" not in lower_header_keys:
        auth_value = token if " " in token else f"Bearer {token}"
        headers["Authorization"] = auth_value

    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    if "Accept" not in headers:
        headers["Accept"] = "application/json"

    return {
        "enabled": enabled,
        "endpoint": endpoint,
        "endpoint_sanitized": _sanitize_delivery_endpoint_for_metadata(endpoint),
        "timeout_seconds": timeout_seconds,
        "headers": headers,
    }


def _collect_mcp_event_ids_from_history(
    messages: List[Any],
    *,
    event_key: str,
) -> set[str]:
    event_ids: set[str] = set()
    for message in messages:
        metadata = getattr(message, "message_metadata", None)
        if not isinstance(metadata, dict):
            continue
        mcp_meta = metadata.get("mcp")
        if not isinstance(mcp_meta, dict):
            continue
        event_id = str(mcp_meta.get(event_key) or "").strip()
        if event_id:
            event_ids.add(event_id)
    return event_ids


def _apply_digest_schedule_prompt(
    *,
    messages: List[Dict[str, Any]],
    config: Dict[str, Any],
    conversation_id: str,
    tooling_metadata: Dict[str, Any],
    seen_event_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    enabled = bool(config.get("enabled"))
    due_now = bool(config.get("due_now"))
    sections = config.get("sections") if isinstance(config.get("sections"), list) else list(DEFAULT_DIGEST_SECTIONS)
    cadence_hours = _as_int(config.get("cadence_hours"), default=24, minimum=1, maximum=168)

    tooling_metadata.update(
        {
            "digest_schedule_enabled": enabled,
            "digest_schedule_due_now": due_now,
            "digest_sections": sections,
            "digest_cadence_hours": cadence_hours,
            "digest_next_due_at_utc": config.get("next_due_at_utc"),
            "digest_reply_to_capture_enabled": bool(config.get("reply_to_capture_enabled")),
            "digest_schedule_triggered": False,
        }
    )

    if not enabled:
        tooling_metadata["digest_schedule_status"] = "disabled"
        return messages
    if not due_now:
        tooling_metadata["digest_schedule_status"] = "not_due"
        return messages

    event_id = str(config.get("event_id") or "").strip()
    if not event_id:
        event_id = f"{conversation_id}:digest:{datetime.now(timezone.utc).date().isoformat()}"
    marker = f"[digest_schedule_event:{event_id}]"

    if seen_event_ids and event_id in seen_event_ids:
        tooling_metadata["digest_schedule_event_id"] = event_id
        tooling_metadata["digest_schedule_triggered"] = False
        tooling_metadata["digest_schedule_duplicate_guard"] = "history_seen"
        tooling_metadata["digest_schedule_status"] = "duplicate_guard"
        config["due_now"] = False
        return messages

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and marker in content:
            tooling_metadata["digest_schedule_event_id"] = event_id
            tooling_metadata["digest_schedule_triggered"] = False
            tooling_metadata["digest_schedule_duplicate_guard"] = "already_present"
            tooling_metadata["digest_schedule_status"] = "duplicate_guard"
            config["due_now"] = False
            return messages

    section_text = ", ".join(sections)
    schedule_system_message = {
        "role": "system",
        "content": DIGEST_SCHEDULE_PROMPT_TEMPLATE.format(
            event_id=event_id,
            sections=section_text,
        ),
    }
    updated_messages = _insert_orchestration_system_message(messages, schedule_system_message)
    tooling_metadata["digest_schedule_event_id"] = event_id
    tooling_metadata["digest_schedule_triggered"] = True
    tooling_metadata["digest_schedule_status"] = "triggered"
    config["event_id"] = event_id
    return updated_messages


def _finalize_pre_compaction_flush_status(
    *,
    tooling_metadata: Dict[str, Any],
    tool_loop_stop_reason: str,
    tool_calls_executed_count: int,
) -> None:
    if not bool(tooling_metadata.get("pre_compaction_flush_enabled")):
        tooling_metadata["pre_compaction_flush_status"] = "disabled"
        return
    if tooling_metadata.get("pre_compaction_flush_duplicate_guard"):
        tooling_metadata["pre_compaction_flush_status"] = "duplicate_guard"
        return
    if not bool(tooling_metadata.get("pre_compaction_flush_triggered")):
        tooling_metadata.setdefault("pre_compaction_flush_status", "not_required")
        return
    if tool_loop_stop_reason == "approval_required":
        tooling_metadata["pre_compaction_flush_status"] = "awaiting_approval"
    elif tool_calls_executed_count > 0:
        tooling_metadata["pre_compaction_flush_status"] = "completed_tool_calls"
    else:
        tooling_metadata["pre_compaction_flush_status"] = "completed_noop"


def _finalize_digest_schedule_status(
    *,
    tooling_metadata: Dict[str, Any],
    tool_loop_stop_reason: str,
    tool_calls_executed_count: int,
) -> None:
    if not bool(tooling_metadata.get("digest_schedule_enabled")):
        tooling_metadata["digest_schedule_status"] = "disabled"
        return
    if tooling_metadata.get("digest_schedule_duplicate_guard"):
        tooling_metadata["digest_schedule_status"] = "duplicate_guard"
        return
    if not bool(tooling_metadata.get("digest_schedule_due_now")):
        tooling_metadata.setdefault("digest_schedule_status", "not_due")
        return
    if not bool(tooling_metadata.get("digest_schedule_triggered")):
        tooling_metadata.setdefault("digest_schedule_status", "not_triggered")
        return
    if tool_loop_stop_reason == "approval_required":
        tooling_metadata["digest_schedule_status"] = "awaiting_approval"
    elif tool_calls_executed_count > 0:
        tooling_metadata["digest_schedule_status"] = "completed_tool_calls"
    else:
        tooling_metadata["digest_schedule_status"] = "completed_noop"


def _apply_pre_compaction_flush_prompt(
    *,
    messages: List[Dict[str, Any]],
    config: Dict[str, Any],
    conversation_id: str,
    tooling_metadata: Dict[str, Any],
    seen_event_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    enabled = bool(config.get("enabled"))
    context_window_tokens = int(config.get("context_window_tokens") or 0)
    threshold = float(config.get("threshold") or 0.9)
    estimated_tokens = _estimate_message_token_count(messages)
    usage_ratio = (
        (estimated_tokens / context_window_tokens)
        if context_window_tokens > 0 and estimated_tokens > 0
        else 0.0
    )

    tooling_metadata.update(
        {
            "pre_compaction_flush_enabled": enabled,
            "pre_compaction_context_window_tokens": context_window_tokens,
            "pre_compaction_flush_threshold": round(threshold, 3),
            "pre_compaction_estimated_tokens": estimated_tokens,
            "pre_compaction_context_usage_ratio": round(usage_ratio, 4),
            "pre_compaction_flush_triggered": False,
            "pre_compaction_flush_status": "not_required",
        }
    )

    if not enabled or context_window_tokens <= 0 or usage_ratio < threshold:
        return messages

    event_id = str(config.get("event_id") or "").strip()
    if not event_id:
        event_id = f"{conversation_id}:preflush"
    marker = f"[pre_compaction_flush_event:{event_id}]"

    if seen_event_ids and event_id in seen_event_ids:
        tooling_metadata["pre_compaction_flush_event_id"] = event_id
        tooling_metadata["pre_compaction_flush_triggered"] = False
        tooling_metadata["pre_compaction_flush_duplicate_guard"] = "history_seen"
        tooling_metadata["pre_compaction_flush_status"] = "duplicate_guard"
        return messages

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and marker in content:
            tooling_metadata["pre_compaction_flush_event_id"] = event_id
            tooling_metadata["pre_compaction_flush_triggered"] = False
            tooling_metadata["pre_compaction_flush_duplicate_guard"] = "already_present"
            tooling_metadata["pre_compaction_flush_status"] = "duplicate_guard"
            return messages

    preflush_system_message = {
        "role": "system",
        "content": PRE_COMPACTION_FLUSH_PROMPT_TEMPLATE.format(event_id=event_id),
    }
    updated_messages = _insert_orchestration_system_message(messages, preflush_system_message)
    tooling_metadata["pre_compaction_flush_event_id"] = event_id
    tooling_metadata["pre_compaction_flush_triggered"] = True
    tooling_metadata["pre_compaction_flush_status"] = "triggered"
    return updated_messages


def _normalize_scope_mode(value: Any) -> str:
    if not isinstance(value, str):
        return "none"
    normalized = value.strip().lower()
    if normalized in {"none", "project"}:
        return normalized
    return "none"


def _extract_mcp_scope_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract MCP request-contract fields from provider params.
    These values are local orchestration controls and are not sent to model providers directly.
    """
    extracted: Dict[str, Any] = {
        "mcp_tools_enabled": _as_bool(params.pop("mcp_tools_enabled", False), False),
        "mcp_scope_mode": _normalize_scope_mode(params.pop("mcp_scope_mode", "none")),
        "mcp_project_slug": params.pop("mcp_project_slug", None),
        "mcp_project_name": params.pop("mcp_project_name", None),
        "mcp_project_lifecycle": params.pop("mcp_project_lifecycle", None),
        "mcp_project_source": params.pop("mcp_project_source", "ui"),
        "mcp_plugin_slug": params.pop("mcp_plugin_slug", None),
        "mcp_sync_on_request": _as_bool(params.pop("mcp_sync_on_request", True), True),
        "mcp_max_tools": _as_int(params.pop("mcp_max_tools", 32), default=32, minimum=1, maximum=128),
        "mcp_max_schema_bytes": _as_int(
            params.pop("mcp_max_schema_bytes", 128_000),
            default=128_000,
            minimum=2_048,
            maximum=500_000,
        ),
    }
    return extracted


def _normalize_conversation_type(value: Any) -> str:
    if not isinstance(value, str):
        return "chat"
    normalized = value.strip().lower()
    return normalized or "chat"


def _is_digest_reply_conversation(conversation_type: str) -> bool:
    normalized = _normalize_conversation_type(conversation_type)
    return normalized in {
        "digest-reply",
        "digest_reply",
        "digestreply",
        "digest_email_reply",
    }


def _is_digest_conversation(conversation_type: str) -> bool:
    normalized = _normalize_conversation_type(conversation_type)
    return normalized == "digest" or normalized.startswith("digest-")


def _extract_digest_delivery_channel(conversation_type: str) -> Optional[str]:
    normalized = _normalize_conversation_type(conversation_type)
    if _is_digest_reply_conversation(normalized):
        if "email" in normalized:
            return "email-reply"
        return "reply"
    if normalized == "digest":
        return "chat"
    if normalized.startswith("digest-"):
        channel = normalized[len("digest-") :].strip().lower()
        if not channel:
            return "chat"
        return channel.replace("_", "-")
    return None


def _build_digest_delivery_handoff_payload(
    *,
    conversation_type: str,
    response_content: str,
    conversation_id: str,
    provider: str,
    model: str,
    digest_schedule_config: Optional[Dict[str, Any]],
    tooling_metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    channel = _extract_digest_delivery_channel(conversation_type)
    if not channel or channel == "chat":
        return None

    normalized_content = " ".join(str(response_content or "").split())
    if not normalized_content:
        return None

    body = str(response_content or "").strip()
    truncated = False
    if len(body) > DIGEST_DELIVERY_HANDOFF_MAX_CHARS:
        body = body[:DIGEST_DELIVERY_HANDOFF_MAX_CHARS].rstrip() + "\n...[truncated]"
        truncated = True

    sections = (
        list(digest_schedule_config.get("sections") or [])
        if isinstance(digest_schedule_config, dict)
        else []
    )
    deduped_sections: List[str] = []
    for section in sections:
        if not isinstance(section, str):
            continue
        normalized = section.strip().lower()
        if not normalized or normalized in deduped_sections:
            continue
        deduped_sections.append(normalized)

    event_id = tooling_metadata.get("digest_schedule_event_id")
    event_id_value = str(event_id).strip() if isinstance(event_id, str) else None
    utc_today = datetime.now(timezone.utc).date().isoformat()
    return {
        "channel": channel,
        "conversation_type": _normalize_conversation_type(conversation_type),
        "conversation_id": conversation_id,
        "event_id": event_id_value,
        "generated_at_utc": _utc_timestamp(),
        "subject": f"BrainDrive Digest {utc_today}",
        "format": "markdown",
        "sections": deduped_sections,
        "body": body,
        "body_truncated": truncated,
        "provider": provider,
        "model": model,
    }


def _normalize_user_id_for_path(user_id: Any) -> str:
    normalized = str(user_id or "").replace("-", "").strip().lower()
    if not normalized:
        return "current"
    return re.sub(r"[^a-z0-9]+", "", normalized) or "current"


def _resolve_digest_delivery_outbox_root(user_id: Any) -> Optional[Path]:
    configured = str(os.getenv(ENV_DIGEST_DELIVERY_OUTBOX_PATH) or "").strip()
    normalized_user_id = _normalize_user_id_for_path(user_id)
    if configured:
        rendered = configured.replace("{user_id}", normalized_user_id)
        return Path(rendered).expanduser().resolve()

    try:
        library_root = resolve_library_root_path()
    except Exception:
        return None
    return (
        library_root
        / "users"
        / normalized_user_id
        / "delivery"
        / "outbox"
    )


def _persist_digest_delivery_handoff_payload(
    *,
    handoff_payload: Dict[str, Any],
    user_id: Any,
) -> Dict[str, Any]:
    if not isinstance(handoff_payload, dict):
        return {"status": "skipped_invalid_payload"}

    outbox_root = _resolve_digest_delivery_outbox_root(user_id)
    if not isinstance(outbox_root, Path):
        return {"status": "skipped_unconfigured"}

    channel_slug = _slugify_capture_fragment(
        str(handoff_payload.get("channel") or ""),
        fallback="channel",
        max_length=32,
    )
    event_token = _slugify_capture_fragment(
        str(
            handoff_payload.get("event_id")
            or handoff_payload.get("conversation_id")
            or "digest"
        ),
        fallback="digest",
        max_length=72,
    )
    generated_at = str(handoff_payload.get("generated_at_utc") or "")
    timestamp_token = re.sub(r"[^0-9]", "", generated_at)[:14]
    if not timestamp_token:
        timestamp_token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    day_token = timestamp_token[:8]
    file_name = f"{timestamp_token}-{event_token}.json"

    output_dir = outbox_root / channel_slug / day_token
    output_path = output_dir / file_name
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(handoff_payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {"status": "persisted", "path": str(output_path)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _dispatch_digest_delivery_handoff_sync(
    *,
    endpoint: str,
    timeout_seconds: float,
    headers: Dict[str, str],
    handoff_payload: Dict[str, Any],
) -> Dict[str, Any]:
    endpoint_sanitized = _sanitize_delivery_endpoint_for_metadata(endpoint)
    request_data = json.dumps(handoff_payload, ensure_ascii=True).encode("utf-8")
    request_obj = urllib_request.Request(
        url=endpoint,
        data=request_data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(request_obj, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
            raw_body = response.read().decode("utf-8", errors="replace").strip()
            result: Dict[str, Any] = {
                "status": "sent" if 200 <= status_code < 300 else "http_error",
                "http_status": status_code,
                "endpoint": endpoint_sanitized,
            }
            if raw_body:
                try:
                    parsed = json.loads(raw_body)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    for key in (
                        "ack_id",
                        "ackId",
                        "delivery_id",
                        "deliveryId",
                        "message_id",
                        "messageId",
                        "id",
                    ):
                        value = parsed.get(key)
                        if isinstance(value, str) and value.strip():
                            result["ack_id"] = value.strip()
                            break
                    if result["status"] != "sent":
                        result["error"] = raw_body[:DIGEST_DELIVERY_SEND_MAX_ERROR_CHARS]
                elif result["status"] != "sent":
                    result["error"] = raw_body[:DIGEST_DELIVERY_SEND_MAX_ERROR_CHARS]
            return result
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "status": "http_error",
            "http_status": int(exc.code),
            "error": (body or str(exc))[:DIGEST_DELIVERY_SEND_MAX_ERROR_CHARS],
            "endpoint": endpoint_sanitized,
        }
    except urllib_error.URLError as exc:
        return {
            "status": "network_error",
            "error": str(getattr(exc, "reason", exc)),
            "endpoint": endpoint_sanitized,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "endpoint": endpoint_sanitized,
        }


async def _send_digest_delivery_handoff(
    *,
    handoff_payload: Optional[Dict[str, Any]],
    send_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(handoff_payload, dict):
        return {"status": "skipped_invalid_payload"}
    if not isinstance(send_config, dict):
        return {"status": "skipped_unconfigured"}

    enabled = bool(send_config.get("enabled"))
    endpoint = str(send_config.get("endpoint") or "").strip()
    endpoint_sanitized = _sanitize_delivery_endpoint_for_metadata(endpoint)
    if not enabled:
        return {
            "status": "skipped_disabled",
            "endpoint": endpoint_sanitized,
        }
    if not endpoint:
        return {
            "status": "skipped_unconfigured_endpoint",
            "endpoint": endpoint_sanitized,
        }

    headers = send_config.get("headers") if isinstance(send_config.get("headers"), dict) else {}
    timeout_seconds = _as_float(
        send_config.get("timeout_seconds", DIGEST_DELIVERY_SEND_TIMEOUT_SECONDS),
        default=DIGEST_DELIVERY_SEND_TIMEOUT_SECONDS,
        minimum=0.5,
        maximum=60.0,
    )
    result = await asyncio.to_thread(
        _dispatch_digest_delivery_handoff_sync,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        headers={str(k): str(v) for k, v in headers.items() if str(k).strip()},
        handoff_payload=handoff_payload,
    )
    if "endpoint" not in result:
        result["endpoint"] = endpoint_sanitized
    return result


def _attach_digest_delivery_persistence(
    *,
    handoff_payload: Optional[Dict[str, Any]],
    user_id: Any,
    tooling_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(handoff_payload, dict):
        return handoff_payload

    persistence = _persist_digest_delivery_handoff_payload(
        handoff_payload=handoff_payload,
        user_id=user_id,
    )
    enriched = dict(handoff_payload)
    status = str(persistence.get("status") or "").strip() or "unknown"
    enriched["delivery_record_status"] = status
    if persistence.get("path"):
        enriched["delivery_record_path"] = str(persistence["path"])
    if persistence.get("error"):
        enriched["delivery_record_error"] = str(persistence["error"])

    if isinstance(tooling_metadata, dict):
        tooling_metadata["digest_delivery_outbox_status"] = status
        if persistence.get("path"):
            tooling_metadata["digest_delivery_outbox_path"] = str(persistence["path"])
        if persistence.get("error"):
            tooling_metadata["digest_delivery_outbox_error"] = str(persistence["error"])

    return enriched


async def _attach_digest_delivery_delivery_state(
    *,
    handoff_payload: Optional[Dict[str, Any]],
    user_id: Any,
    tooling_metadata: Optional[Dict[str, Any]] = None,
    send_config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    enriched = _attach_digest_delivery_persistence(
        handoff_payload=handoff_payload,
        user_id=user_id,
        tooling_metadata=tooling_metadata,
    )
    if not isinstance(enriched, dict):
        return enriched

    send_result = await _send_digest_delivery_handoff(
        handoff_payload=enriched,
        send_config=send_config,
    )
    send_status = str(send_result.get("status") or "").strip() or "unknown"
    enriched["delivery_send_status"] = send_status
    if send_result.get("endpoint"):
        enriched["delivery_send_endpoint"] = str(send_result["endpoint"])
    if isinstance(send_result.get("http_status"), int):
        enriched["delivery_send_http_status"] = int(send_result["http_status"])
    if send_result.get("ack_id"):
        enriched["delivery_send_ack_id"] = str(send_result["ack_id"])
    if send_result.get("error"):
        enriched["delivery_send_error"] = str(send_result["error"])

    if isinstance(tooling_metadata, dict):
        tooling_metadata["digest_delivery_send_status"] = send_status
        if send_result.get("endpoint"):
            tooling_metadata["digest_delivery_send_endpoint"] = str(send_result["endpoint"])
        if isinstance(send_result.get("http_status"), int):
            tooling_metadata["digest_delivery_send_http_status"] = int(send_result["http_status"])
        if send_result.get("ack_id"):
            tooling_metadata["digest_delivery_send_ack_id"] = str(send_result["ack_id"])
        if send_result.get("error"):
            tooling_metadata["digest_delivery_send_error"] = str(send_result["error"])

    return enriched


def _is_capture_intake_conversation(conversation_type: str) -> bool:
    normalized = _normalize_conversation_type(conversation_type)
    return normalized == "capture" or _is_digest_reply_conversation(normalized)


def _extract_life_topic(conversation_type: str) -> Optional[str]:
    if not conversation_type.startswith("life-"):
        return None
    topic = conversation_type[5:].strip().lower()
    if topic in LIFE_ONBOARDING_TOPICS:
        return topic
    return None


def _extract_life_topic_from_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None

    alias_map = {
        "finance": "finances",
        "finances": "finances",
        "fitness": "fitness",
        "relationship": "relationships",
        "relationships": "relationships",
        "career": "career",
        "whyfinder": "whyfinder",
        "why finder": "whyfinder",
    }

    for alias, topic in alias_map.items():
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return topic

    return None


def _coerce_page_context(page_context: Any) -> Dict[str, Any]:
    if isinstance(page_context, dict):
        return page_context
    if isinstance(page_context, str) and page_context.strip():
        try:
            parsed = json.loads(page_context)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _infer_life_topic_for_request(
    conversation_type: str,
    page_context: Any,
    latest_user_message: Optional[str],
) -> Optional[str]:
    explicit_topic = _extract_life_topic(conversation_type)
    if explicit_topic:
        return explicit_topic

    context = _coerce_page_context(page_context)
    for key in ("pageName", "pageRoute", "route", "name", "title"):
        topic = _extract_life_topic_from_text(context.get(key))
        if topic:
            return topic

    return _extract_life_topic_from_text(latest_user_message)


def _apply_conversation_scope_defaults(
    conversation_type: str,
    mcp_scope: Dict[str, Any],
) -> Optional[str]:
    """
    Ensure orchestration pages can resolve MCP tools even when the client omits scope.
    Returns the applied source marker when defaults are injected.
    """
    scope_mode = str(mcp_scope.get("mcp_scope_mode") or "none").strip().lower()
    project_slug = mcp_scope.get("mcp_project_slug")
    has_project_scope = scope_mode == "project" and isinstance(project_slug, str) and bool(project_slug.strip())

    if has_project_scope:
        if not bool(mcp_scope.get("mcp_tools_enabled")):
            mcp_scope["mcp_tools_enabled"] = True
            return "conversation_type"
        return None

    topic = _extract_life_topic(conversation_type)
    if topic:
        mcp_scope.update(
            {
                "mcp_tools_enabled": True,
                "mcp_scope_mode": "project",
                "mcp_project_slug": topic,
                "mcp_project_name": LIFE_ONBOARDING_TOPICS[topic],
                "mcp_project_lifecycle": "active",
                "mcp_project_source": "conversation_type",
                "mcp_plugin_slug": mcp_scope.get("mcp_plugin_slug")
                or DEFAULT_LIBRARY_MCP_PLUGIN_SLUG,
            }
        )
        return "conversation_type"

    if _is_capture_intake_conversation(conversation_type):
        mcp_scope.update(
            {
                "mcp_tools_enabled": True,
                "mcp_scope_mode": "project",
                "mcp_project_slug": "capture",
                "mcp_project_name": "Capture",
                "mcp_project_lifecycle": "active",
                "mcp_project_source": "conversation_type",
                "mcp_plugin_slug": mcp_scope.get("mcp_plugin_slug")
                or DEFAULT_LIBRARY_MCP_PLUGIN_SLUG,
            }
        )
        return "conversation_type"

    if _is_digest_conversation(conversation_type):
        mcp_scope.update(
            {
                "mcp_tools_enabled": True,
                "mcp_scope_mode": "project",
                "mcp_project_slug": "digest",
                "mcp_project_name": "Digest",
                "mcp_project_lifecycle": "active",
                "mcp_project_source": "conversation_type",
                "mcp_plugin_slug": mcp_scope.get("mcp_plugin_slug")
                or DEFAULT_LIBRARY_MCP_PLUGIN_SLUG,
            }
        )
        return "conversation_type"

    return None


def _extract_resolved_tool_names(resolved_tools: List[Dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for schema in resolved_tools:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


STRICT_NATIVE_TOOL_SCHEMA_PROVIDERS = {"openrouter", "openai"}
STRICT_NATIVE_TOP_LEVEL_DISALLOWED = {"oneOf", "anyOf", "allOf", "enum", "not"}


def _merge_top_level_object_variants(variants: Any) -> tuple[Dict[str, Any], List[str]]:
    merged_properties: Dict[str, Any] = {}
    merged_required: List[str] = []
    if not isinstance(variants, list):
        return merged_properties, merged_required

    for variant in variants:
        if not isinstance(variant, dict):
            continue
        if str(variant.get("type") or "object").strip().lower() != "object":
            continue
        properties = variant.get("properties")
        if isinstance(properties, dict):
            for key, value in properties.items():
                if isinstance(key, str) and key and key not in merged_properties:
                    merged_properties[key] = copy.deepcopy(value)
        required = variant.get("required")
        if isinstance(required, list):
            for entry in required:
                if isinstance(entry, str) and entry not in merged_required:
                    merged_required.append(entry)
    return merged_properties, merged_required


def _sanitize_native_tool_parameters(parameters: Any) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = (
        copy.deepcopy(parameters) if isinstance(parameters, dict) else {}
    )
    top_level_properties: Dict[str, Any] = (
        copy.deepcopy(sanitized.get("properties"))
        if isinstance(sanitized.get("properties"), dict)
        else {}
    )
    top_level_required: List[str] = (
        [entry for entry in sanitized.get("required") if isinstance(entry, str)]
        if isinstance(sanitized.get("required"), list)
        else []
    )

    for keyword in ("oneOf", "anyOf", "allOf"):
        merged_properties, merged_required = _merge_top_level_object_variants(
            sanitized.get(keyword)
        )
        for key, value in merged_properties.items():
            if key not in top_level_properties:
                top_level_properties[key] = value
        for entry in merged_required:
            if entry not in top_level_required:
                top_level_required.append(entry)
        sanitized.pop(keyword, None)

    for keyword in STRICT_NATIVE_TOP_LEVEL_DISALLOWED:
        sanitized.pop(keyword, None)

    sanitized["type"] = "object"
    sanitized["properties"] = top_level_properties
    if top_level_required:
        sanitized["required"] = top_level_required
    elif "required" in sanitized:
        sanitized.pop("required", None)
    if "additionalProperties" not in sanitized:
        sanitized["additionalProperties"] = True
    return sanitized


def _sanitize_tools_for_provider(
    resolved_tools: List[Dict[str, Any]],
    provider: Any,
) -> tuple[List[Dict[str, Any]], int]:
    provider_id = str(provider or "").strip().lower()
    if provider_id not in STRICT_NATIVE_TOOL_SCHEMA_PROVIDERS:
        return resolved_tools, 0

    sanitized_tools: List[Dict[str, Any]] = []
    sanitized_count = 0
    for tool_schema in resolved_tools:
        if not isinstance(tool_schema, dict):
            sanitized_tools.append(tool_schema)
            continue
        schema_copy = copy.deepcopy(tool_schema)
        function = schema_copy.get("function")
        if not isinstance(function, dict):
            sanitized_tools.append(schema_copy)
            continue
        parameters = function.get("parameters")
        sanitized_parameters = _sanitize_native_tool_parameters(parameters)
        if parameters != sanitized_parameters:
            function["parameters"] = sanitized_parameters
            sanitized_count += 1
        sanitized_tools.append(schema_copy)
    return sanitized_tools, sanitized_count


def _is_strict_native_provider(provider: Any) -> bool:
    return str(provider or "").strip().lower() in STRICT_NATIVE_TOOL_SCHEMA_PROVIDERS


def _build_tool_result_message(
    *,
    provider: Any,
    tool_name: Optional[str],
    tool_call_id: Optional[str],
    content: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "role": "tool",
        "content": content,
    }
    if isinstance(tool_call_id, str) and tool_call_id.strip():
        payload["tool_call_id"] = tool_call_id.strip()
    if not _is_strict_native_provider(provider):
        if isinstance(tool_name, str) and tool_name.strip():
            payload["name"] = tool_name.strip()
    return payload



def _extract_resolved_tool_safety(resolved_tools: List[Dict[str, Any]]) -> Dict[str, str]:
    safety: Dict[str, str] = {}
    for schema in resolved_tools:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        safety[name.strip()] = infer_safety_class(name, schema)
    return safety


def _infer_page_kind(conversation_type: str) -> str:
    normalized = _normalize_conversation_type(conversation_type)
    if _is_capture_intake_conversation(normalized):
        return "capture"
    if _is_digest_conversation(normalized):
        return "digest"
    if _extract_life_topic(normalized):
        return "life"
    return "project"


def _normalize_project_scope_path(project_slug: Any) -> Optional[str]:
    if not isinstance(project_slug, str):
        return None

    normalized = project_slug.strip().replace("\\", "/")
    if not normalized:
        return None

    if normalized in {"capture", "life"}:
        return normalized
    if normalized == "digest":
        return normalized

    if normalized.startswith("users/"):
        return normalized
    if normalized.startswith("projects/"):
        return normalized
    if normalized.startswith("life/"):
        return normalized
    return f"projects/active/{normalized}"


def _normalize_scoped_tool_path(path_value: Any, scope_path: Optional[str]) -> Optional[str]:
    if not isinstance(path_value, str):
        return None
    normalized = path_value.strip().replace("\\", "/")
    if not normalized:
        return None
    normalized = re.sub(r"^\./+", "", normalized)
    if normalized.startswith(("projects/", "life/", "capture/", "digest/", "users/")):
        return normalized
    if not isinstance(scope_path, str) or not scope_path.strip():
        return normalized
    return f"{scope_path.strip().rstrip('/')}/{normalized.lstrip('/')}"


def _is_new_page_interview_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False
    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False
    if "interview" in normalized or "onboarding" in normalized:
        return True
    start_markers = ("start", "resume", "continue", "next question", "question")
    if any(marker in normalized for marker in start_markers) and "page" in normalized:
        return True
    return False


def _apply_dual_path_scope_tool_policy(
    *,
    routing_decision: Dict[str, Any],
    conversation_type: str,
    mcp_scope: Dict[str, Any],
    latest_user_message: Optional[str] = None,
) -> Dict[str, Any]:
    updated = dict(routing_decision)
    if str(updated.get("route_mode") or "").strip() != "dual_path_fallback":
        return updated
    if str(updated.get("tool_profile") or "").strip() != TOOL_PROFILE_READ_ONLY:
        return updated
    if str(updated.get("tool_profile_source") or "").strip() != "routing_default":
        return updated

    normalized_scope = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    life_topic = _extract_life_topic(conversation_type)
    new_page_intent = _is_new_page_engine_intent(latest_user_message)
    new_page_interview_intent = _is_new_page_interview_intent(latest_user_message)
    owner_profile_intent = bool(_extract_owner_profile_update_text(latest_user_message))
    is_capture_scope = bool(
        (isinstance(normalized_scope, str) and normalized_scope == "capture")
        or _is_capture_intake_conversation(conversation_type)
    )
    is_life_scope = bool(
        (isinstance(normalized_scope, str) and normalized_scope.startswith("life/"))
        or life_topic
    )
    is_project_scope = bool(
        isinstance(normalized_scope, str)
        and normalized_scope.startswith("projects/")
    )
    if is_capture_scope:
        allowlist = set(DUAL_PATH_CAPTURE_SCOPE_COMPAT_TOOL_ALLOWLIST)
        policy_mode = "dual_path_capture_scope_compat"
        if new_page_intent:
            allowlist.update(DUAL_PATH_NEW_PAGE_COMPAT_TOOL_ALLOWLIST)
            policy_mode = "dual_path_capture_new_page_compat"
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_scope_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(allowlist),
                "policy_mode": policy_mode,
            }
        )
        return updated

    if is_life_scope:
        allowlist = set(DUAL_PATH_LIFE_SCOPE_COMPAT_TOOL_ALLOWLIST)
        policy_mode = "dual_path_life_scope_compat"
        if new_page_intent:
            allowlist.update(DUAL_PATH_NEW_PAGE_COMPAT_TOOL_ALLOWLIST)
            policy_mode = "dual_path_life_new_page_compat"
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_scope_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(allowlist),
                "policy_mode": policy_mode,
            }
        )
        return updated

    if is_project_scope and not new_page_intent and not new_page_interview_intent:
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_scope_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(DUAL_PATH_PROJECT_SCOPE_COMPAT_TOOL_ALLOWLIST),
                "policy_mode": "dual_path_project_scope_compat",
            }
        )
        return updated

    if owner_profile_intent and normalized_scope not in {"digest"}:
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_intent_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(DUAL_PATH_OWNER_PROFILE_COMPAT_TOOL_ALLOWLIST),
                "policy_mode": "dual_path_owner_profile_compat",
            }
        )
        return updated

    if new_page_intent:
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_intent_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(DUAL_PATH_NEW_PAGE_COMPAT_TOOL_ALLOWLIST),
                "policy_mode": "dual_path_new_page_compat",
            }
        )
        return updated

    if new_page_interview_intent and isinstance(normalized_scope, str) and normalized_scope not in {
        "capture",
        "digest",
    }:
        updated.update(
            {
                "tool_profile": TOOL_PROFILE_FULL,
                "tool_profile_source": "routing_intent_policy",
                "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
                "tool_name_allowlist": sorted(DUAL_PATH_NEW_PAGE_INTERVIEW_COMPAT_TOOL_ALLOWLIST),
                "policy_mode": "dual_path_new_page_interview_compat",
            }
        )
    return updated


def _is_owner_profile_resume_approval_context(
    approval_resume_context: Optional[Dict[str, Any]],
) -> bool:
    if not isinstance(approval_resume_context, dict):
        return False
    if str(approval_resume_context.get("action") or "").strip().lower() != "approve":
        return False

    synthetic_reason = str(approval_resume_context.get("synthetic_reason") or "").strip().lower()
    if synthetic_reason in OWNER_PROFILE_UPDATE_REASONS:
        return True

    tool_name = str(approval_resume_context.get("tool") or "").strip()
    if tool_name not in {"write_markdown", "create_markdown"}:
        return False

    summary_text = str(approval_resume_context.get("summary") or "").strip().lower()
    if "me/profile.md" in summary_text or "me/profile" in summary_text:
        return True

    arguments = approval_resume_context.get("arguments")
    if not isinstance(arguments, dict):
        return False
    raw_path = arguments.get("path")
    if not isinstance(raw_path, str):
        raw_path = arguments.get("file_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False

    normalized_path = raw_path.strip().replace("\\", "/")
    normalized_path = re.sub(r"^(?:\./)+", "", normalized_path)
    normalized_path = normalized_path.lstrip("/").lower()
    if normalized_path in {"profile", "profile.md", "me/profile", OWNER_PROFILE_RELATIVE_PATH.lower()}:
        return True
    if normalized_path.endswith("/me/profile.md"):
        return True
    return False


def _apply_approval_resume_tool_policy(
    *,
    routing_decision: Dict[str, Any],
    approval_resume_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    updated = dict(routing_decision)
    if str(updated.get("route_mode") or "").strip() != "dual_path_fallback":
        return updated
    if not _is_owner_profile_resume_approval_context(approval_resume_context):
        return updated

    updated.update(
        {
            "tool_profile": TOOL_PROFILE_FULL,
            "tool_profile_source": "routing_resume_policy",
            "allowed_safety_classes": [TOOL_PROFILE_READ_ONLY, "mutating"],
            "tool_name_allowlist": sorted(DUAL_PATH_OWNER_PROFILE_COMPAT_TOOL_ALLOWLIST),
            "policy_mode": "dual_path_owner_profile_resume_compat",
        }
    )
    return updated


def _derive_scope_root_and_path(
    conversation_type: str,
    mcp_scope: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    page_kind = _infer_page_kind(conversation_type)
    if page_kind == "capture":
        return "capture", "capture"
    if page_kind == "digest":
        return "digest", "digest"

    life_topic = _extract_life_topic(conversation_type)
    if page_kind == "life" and life_topic:
        return "life", f"life/{life_topic}"

    scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    if not scope_path:
        return "projects", None

    if scope_path.startswith("life/"):
        return "life", scope_path
    if scope_path.startswith("capture"):
        return "capture", "capture"
    if scope_path.startswith("projects/"):
        return "projects", scope_path
    return "projects", scope_path


def _required_scope_files_for_page_kind(page_kind: str) -> List[str]:
    normalized = str(page_kind or "").strip().lower()
    if normalized == "project":
        return ["AGENT.md", "spec.md", "build-plan.md", "decisions.md", "ideas.md"]
    if normalized == "life":
        return [
            "AGENT.md",
            "spec.md",
            "build-plan.md",
            "interview.md",
            "goals.md",
            "action-plan.md",
        ]
    if normalized == "capture":
        return ["AGENT.md"]
    if normalized == "digest":
        return ["AGENT.md", "_meta/rollup-state.json"]
    return ["AGENT.md", "spec.md", "build-plan.md"]


def _extract_nested_mcp_error_code(error: Dict[str, Any]) -> Optional[str]:
    if not isinstance(error, dict):
        return None

    details = error.get("details")
    if not isinstance(details, dict):
        return None

    nested_error = details.get("error")
    if not isinstance(nested_error, dict):
        return None

    nested_code = nested_error.get("code")
    if isinstance(nested_code, str) and nested_code.strip():
        return nested_code.strip()
    return None


def _extract_tool_execution_error_code(tool_call_payload: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tool_call_payload, dict):
        return None
    error = tool_call_payload.get("error")
    if not isinstance(error, dict):
        return None

    nested = _extract_nested_mcp_error_code(error)
    if isinstance(nested, str) and nested.strip():
        return nested.strip()

    direct = error.get("code")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    details = error.get("details")
    if isinstance(details, dict):
        nested_code = details.get("code")
        if isinstance(nested_code, str) and nested_code.strip():
            return nested_code.strip()

    return None


async def _check_scope_file_exists(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    plugin_slug_hint: Optional[str],
    path: str,
) -> tuple[Optional[bool], Optional[Dict[str, Any]]]:
    execution = await _execute_tool_with_resync_fallback(
        runtime_service=runtime_service,
        mcp_user_id=mcp_user_id,
        tool_name="read_file_metadata",
        arguments={"path": path},
        plugin_slug_hint=plugin_slug_hint,
    )
    if execution.get("ok"):
        return True, None

    error = execution.get("error") if isinstance(execution.get("error"), dict) else {}
    nested_code = _extract_nested_mcp_error_code(error)
    if nested_code == "FILE_NOT_FOUND":
        return False, None

    return None, error if error else {"code": "UNKNOWN_ERROR", "message": "Metadata lookup failed."}


async def _build_orchestration_context_payload(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    conversation_type: str,
    mcp_scope: Dict[str, Any],
    resolved_tools: List[Dict[str, Any]],
    plugin_slug_hint: Optional[str],
) -> Dict[str, Any]:
    page_kind = _infer_page_kind(conversation_type)
    scope_root, scope_path = _derive_scope_root_and_path(conversation_type, mcp_scope)
    if scope_root == "life" and page_kind != "capture":
        page_kind = "life"
    elif scope_root == "capture":
        page_kind = "capture"
    required_files = _required_scope_files_for_page_kind(page_kind)

    required_file_map: Dict[str, Dict[str, Any]] = {}
    required_files_missing: List[str] = []
    required_files_unverified: List[str] = []
    context_missing: List[str] = []
    resolved_tool_names = _extract_resolved_tool_names(resolved_tools)
    has_metadata_tool = "read_file_metadata" in resolved_tool_names
    has_onboarding_state_tool = "get_onboarding_state" in resolved_tool_names

    if not scope_path:
        context_missing.append("scope_path")
    elif not has_metadata_tool:
        for filename in required_files:
            target_path = f"{scope_path.rstrip('/')}/{filename}"
            required_file_map[target_path] = {
                "exists": None,
                "error": {
                    "code": "TOOL_NOT_ALLOWED",
                    "message": "read_file_metadata is not available in this tool set.",
                },
            }
            required_files_unverified.append(target_path)
    else:
        for filename in required_files:
            target_path = f"{scope_path.rstrip('/')}/{filename}"
            exists, error = await _check_scope_file_exists(
                runtime_service=runtime_service,
                mcp_user_id=mcp_user_id,
                plugin_slug_hint=plugin_slug_hint,
                path=target_path,
            )
            entry: Dict[str, Any] = {"exists": exists}
            if isinstance(error, dict) and error:
                entry["error"] = error

            required_file_map[target_path] = entry
            if exists is False:
                required_files_missing.append(target_path)
            elif exists is None:
                required_files_unverified.append(target_path)

    scope_scaffold_repair: Optional[Dict[str, Any]] = None
    if (
        scope_path
        and required_files_missing
        and has_metadata_tool
        and "ensure_scope_scaffold" in resolved_tool_names
    ):
        repair_result = await _execute_tool_with_resync_fallback(
            runtime_service=runtime_service,
            mcp_user_id=mcp_user_id,
            tool_name="ensure_scope_scaffold",
            arguments={"path": scope_path},
            plugin_slug_hint=plugin_slug_hint,
        )
        if repair_result.get("ok"):
            scope_scaffold_repair = {
                "attempted": True,
                "status": "success",
                "result": repair_result.get("data") if isinstance(repair_result.get("data"), dict) else {},
            }

            required_file_map = {}
            required_files_missing = []
            required_files_unverified = []
            for filename in required_files:
                target_path = f"{scope_path.rstrip('/')}/{filename}"
                exists, error = await _check_scope_file_exists(
                    runtime_service=runtime_service,
                    mcp_user_id=mcp_user_id,
                    plugin_slug_hint=plugin_slug_hint,
                    path=target_path,
                )
                entry: Dict[str, Any] = {"exists": exists}
                if isinstance(error, dict) and error:
                    entry["error"] = error

                required_file_map[target_path] = entry
                if exists is False:
                    required_files_missing.append(target_path)
                elif exists is None:
                    required_files_unverified.append(target_path)
        else:
            scope_scaffold_repair = {
                "attempted": True,
                "status": "failed",
                "error": repair_result.get("error") if isinstance(repair_result.get("error"), dict) else {},
            }

    onboarding_state: Optional[Dict[str, Any]] = None
    onboarding_state_error: Optional[Dict[str, Any]] = None
    onboarding_topic: Optional[str] = None
    onboarding_topic_status: Optional[str] = None
    if page_kind == "life":
        onboarding_topic = _extract_life_topic_from_scope_path(scope_path) or _extract_life_topic(
            _normalize_conversation_type(conversation_type)
        )
        if has_onboarding_state_tool:
            onboarding_result = await _execute_tool_with_resync_fallback(
                runtime_service=runtime_service,
                mcp_user_id=mcp_user_id,
                tool_name="get_onboarding_state",
                arguments={},
                plugin_slug_hint=plugin_slug_hint,
            )
            if onboarding_result.get("ok"):
                onboarding_payload = onboarding_result.get("data")
                if isinstance(onboarding_payload, dict):
                    onboarding_data = onboarding_payload.get("data")
                    if isinstance(onboarding_data, dict):
                        state = onboarding_data.get("state")
                        if isinstance(state, dict):
                            onboarding_state = state
                            starter_topics = state.get("starter_topics")
                            if (
                                onboarding_topic
                                and isinstance(starter_topics, dict)
                                and isinstance(starter_topics.get(onboarding_topic), str)
                            ):
                                onboarding_topic_status = str(starter_topics[onboarding_topic])
            else:
                onboarding_state_error = (
                    onboarding_result.get("error")
                    if isinstance(onboarding_result.get("error"), dict)
                    else {"code": "ONBOARDING_STATE_UNAVAILABLE"}
                )
        else:
            onboarding_state_error = {
                "code": "TOOL_NOT_ALLOWED",
                "message": "get_onboarding_state is not available in this tool set.",
            }

        if onboarding_state is None:
            context_missing.append("onboarding_state")
    context_ready = (
        not context_missing
        and not required_files_missing
        and not required_files_unverified
    )

    return {
        "conversation_type": _normalize_conversation_type(conversation_type),
        "page_kind": page_kind,
        "scope_root": scope_root,
        "scope_path": scope_path,
        "required_file_map": required_file_map,
        "required_files_missing": required_files_missing,
        "required_files_unverified": required_files_unverified,
        "scope_scaffold_repair": scope_scaffold_repair,
        "onboarding_state": onboarding_state,
        "onboarding_state_error": onboarding_state_error,
        "onboarding_topic": onboarding_topic,
        "onboarding_topic_status": onboarding_topic_status,
        "tool_safety_metadata": {
            "tool_classes": _extract_resolved_tool_safety(resolved_tools),
        },
        "approval_mode_policy": APPROVAL_MODE_POLICY,
        "context_missing": context_missing,
        "context_ready": context_ready,
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _build_orchestration_context_error_payload(
    *,
    tool_name: str,
    orchestration_context: Dict[str, Any],
) -> Dict[str, Any]:
    missing_fields = orchestration_context.get("context_missing")
    if not isinstance(missing_fields, list):
        missing_fields = []

    required_missing = orchestration_context.get("required_files_missing")
    if not isinstance(required_missing, list):
        required_missing = []

    required_unverified = orchestration_context.get("required_files_unverified")
    if not isinstance(required_unverified, list):
        required_unverified = []

    return {
        "code": "MISSING_ORCHESTRATION_CONTEXT",
        "message": (
            "Mutating tool execution blocked until canonical scope context is complete."
        ),
        "details": {
            "tool": tool_name,
            "missing_fields": missing_fields,
            "required_files_missing": required_missing,
            "required_files_unverified": required_unverified,
            "approval_mode_policy": APPROVAL_MODE_POLICY,
            "scope_root": orchestration_context.get("scope_root"),
            "scope_path": orchestration_context.get("scope_path"),
            "remediation": [
                "Run ensure_scope_scaffold (or bootstrap_user_library/create_project_scaffold) to repair missing canonical files.",
                "Resolve scope mapping and required-file checks before retrying mutating tools.",
                "Retry the request after orchestration context shows context_ready=true.",
            ],
        },
    }


def _validate_mutating_orchestration_context(
    *,
    tool_name: str,
    safety_class: str,
    orchestration_context: Optional[Dict[str, Any]],
) -> tuple[bool, Optional[Dict[str, Any]]]:
    if str(safety_class or "").strip().lower() != "mutating":
        return True, None

    if tool_name in MUTATION_CONTEXT_REMEDIATION_TOOLS:
        return True, None

    if not isinstance(orchestration_context, dict):
        fallback_context = {
            "context_missing": ["orchestration_context"],
            "required_files_missing": [],
            "required_files_unverified": [],
            "scope_root": None,
            "scope_path": None,
        }
        return False, _build_orchestration_context_error_payload(
            tool_name=tool_name,
            orchestration_context=fallback_context,
        )

    missing_fields = orchestration_context.get("context_missing")
    if not isinstance(missing_fields, list):
        missing_fields = []

    required_missing = orchestration_context.get("required_files_missing")
    if not isinstance(required_missing, list):
        required_missing = []

    required_unverified = orchestration_context.get("required_files_unverified")
    if not isinstance(required_unverified, list):
        required_unverified = []

    required_file_map = orchestration_context.get("required_file_map")
    if not isinstance(required_file_map, dict):
        required_file_map = {}

    effective_required_unverified = list(required_unverified)
    if effective_required_unverified:
        unresolved_paths = [
            path
            for path in effective_required_unverified
            if not (
                isinstance(path, str)
                and isinstance(required_file_map.get(path), dict)
                and isinstance(required_file_map[path].get("error"), dict)
                and str(required_file_map[path]["error"].get("code") or "").strip() == "TOOL_NOT_ALLOWED"
            )
        ]
        if not unresolved_paths:
            # If metadata checks are unavailable in the active tool profile,
            # keep the context signal for observability but do not block writes.
            effective_required_unverified = []

    effective_missing_fields = list(missing_fields)
    context_conversation_type = _normalize_conversation_type(
        str(orchestration_context.get("conversation_type") or "")
    )
    is_life_conversation = context_conversation_type.startswith("life-")
    if (
        "onboarding_state" in effective_missing_fields
        and tool_name not in ONBOARDING_CONTEXT_REQUIRED_TOOLS
        and not is_life_conversation
    ):
        effective_missing_fields = [
            item for item in effective_missing_fields if item != "onboarding_state"
        ]

    if not effective_missing_fields and not required_missing and not effective_required_unverified:
        return True, None

    effective_context = dict(orchestration_context)
    effective_context["context_missing"] = effective_missing_fields
    effective_context["required_files_missing"] = required_missing
    effective_context["required_files_unverified"] = effective_required_unverified

    return False, _build_orchestration_context_error_payload(
        tool_name=tool_name,
        orchestration_context=effective_context,
    )


def _build_conversation_orchestration_prompt(
    conversation_type: str,
    resolved_tools: List[Dict[str, Any]],
    *,
    digest_sections: Optional[List[str]] = None,
    digest_due_now: bool = False,
) -> tuple[Optional[str], Optional[Dict[str, str]]]:
    tool_names = _extract_resolved_tool_names(resolved_tools)
    life_topic = _extract_life_topic(conversation_type)
    if life_topic and {"get_onboarding_state", "start_topic_onboarding"}.issubset(tool_names):
        return (
            "life_onboarding",
            {
                "role": "system",
                "content": (
                    f"Life onboarding orchestration for topic '{life_topic}': "
                    "call get_onboarding_state at the start; if this topic is not complete, "
                    f"call start_topic_onboarding with topic='{life_topic}' to initialize interview context. "
                    "Use topic AGENT context first (fallback to interview seed when needed), then generate dynamic topic-appropriate opening questions. "
                    "Opening interview should be high-level and capped at 6 questions. "
                    "Ask one question per turn and wait for the user's answer before asking the next question. "
                    "This phase is discovery-only: do not provide coaching, recommendations, or solution plans unless the user explicitly asks. "
                    "Keep responses short (max two setup sentences plus one question) and never output a full questionnaire list. "
                    "Before any mutating write, summarize the proposed update and require explicit approval. "
                    "After approval, call save_topic_onboarding_context with topic, question, answer, context, and approved=true. "
                    "After opening questions finish, ask whether the user wants initial goals/tasks captured. "
                    "When due dates are relative, convert them to explicit dates before save. "
                    "When onboarding context is sufficient, call complete_topic_onboarding with a concise summary. "
                    "For task requests in this topic, always call list_tasks first to resolve task IDs before mutating operations. "
                    "If the user asks to complete or edit an existing task, do not call create_task; use update_task and/or complete_task for the matched task ID. "
                    "Only call create_task when the user explicitly requests a new task and no existing task match is intended."
                ),
            },
        )

    if _is_digest_conversation(conversation_type) and not _is_digest_reply_conversation(conversation_type):
        has_snapshot = "digest_snapshot" in tool_names
        has_score = "score_digest_tasks" in tool_names
        has_rollup = "rollup_digest_period" in tool_names
        has_capture_writer = "create_markdown" in tool_names or "write_markdown" in tool_names
        delivery_channel = _extract_digest_delivery_channel(conversation_type) or "chat"
        if has_snapshot or has_score or has_rollup:
            section_labels = ", ".join(digest_sections or list(DEFAULT_DIGEST_SECTIONS))
            lines = [
                "Digest heartbeat mode:",
                f"Target sections: {section_labels}.",
                "Keep digest output concise and scannable.",
                "Always include one actionable library-improvement suggestion.",
            ]
            if delivery_channel != "chat":
                lines.append(
                    f"Delivery channel: {delivery_channel}. "
                    "Format output as transport-friendly bullet points (no tables)."
                )
            if digest_due_now:
                lines.append("A scheduled digest run is due now.")
            if has_snapshot:
                lines.append("Call digest_snapshot first to gather tasks, completions, and recent activity.")
            if has_score:
                lines.append("Call score_digest_tasks using digest_snapshot task data before drafting priorities.")
            if has_rollup:
                lines.append("Use rollup_digest_period for weekly digest persistence/checkpoints when appropriate.")
            if has_capture_writer:
                lines.append("If the user replies with quick updates, persist them into capture/inbox.")
            lines.append("All mutating operations require explicit approval before execution.")
            return (
                "digest_heartbeat",
                {
                    "role": "system",
                    "content": " ".join(lines),
                },
            )

    if _is_capture_intake_conversation(conversation_type):
        has_inbox_writer = "create_markdown" in tool_names or "write_markdown" in tool_names
        has_rollup = "rollup_digest_period" in tool_names
        has_task_lookup = "list_tasks" in tool_names
        has_task_mutation = "update_task" in tool_names or "complete_task" in tool_names
        has_task_creator = "create_task" in tool_names
        if has_inbox_writer or has_rollup:
            lines = ["Capture orchestration mode:"]
            if _is_digest_reply_conversation(conversation_type):
                lines.append(
                    "This turn originated from a digest reply, so prioritize fast capture of notes/tasks."
                )
            if has_inbox_writer:
                lines.append(
                    "Persist raw captures to a markdown file under capture/inbox before any routing/categorization."
                )
                lines.append(
                    "If the user records a decision, also write a canonical decision entry to decisions.md in the selected scope."
                )
            lines.append(
                "When multiple distinct tasks or decisions are present, create separate entries so each can be tracked independently."
            )
            if has_task_lookup and has_task_mutation:
                lines.append(
                    "For requests to complete or edit an existing task, call list_tasks first to resolve task IDs, then use update_task and/or complete_task with the matched task ID."
                )
                if has_task_creator:
                    lines.append(
                        "Do not call create_task when the intent is to complete or edit an existing task; only call create_task for explicitly new tasks."
                    )
            elif has_task_creator:
                lines.append(
                    "Only call create_task when the user explicitly requests a new task."
                )
            if has_rollup:
                lines.append(
                    "After capture or daily-event writes, trigger rollup_digest_period (week, and month/year when relevant)."
                )
            lines.append(
                "Use canonical current UTC dates in capture writes (filenames, due fields, and relative-date content)."
            )
            lines.append("All mutating operations require explicit approval before execution.")
            return (
                "capture_intake",
                {
                    "role": "system",
                    "content": " ".join(lines),
                },
            )

    return (None, None)



def _insert_orchestration_system_message(
    combined_messages: List[Dict[str, Any]],
    orchestration_message: Optional[Dict[str, str]],
) -> List[Dict[str, Any]]:
    if not orchestration_message:
        return combined_messages

    insertion_index = 0
    while insertion_index < len(combined_messages):
        entry = combined_messages[insertion_index]
        role = entry.get("role") if isinstance(entry, dict) else None
        if role != "system":
            break
        insertion_index += 1

    return (
        combined_messages[:insertion_index]
        + [orchestration_message]
        + combined_messages[insertion_index:]
    )


def _latest_user_message_text(messages: List[Dict[str, Any]]) -> Optional[str]:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _latest_non_approval_user_message_text(messages: List[Dict[str, Any]]) -> Optional[str]:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        stripped = content.strip()
        if not stripped:
            continue
        if _parse_chat_approval_action(stripped):
            continue
        return stripped
    return None


def _is_life_onboarding_kickoff_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = message_text.strip().lower()
    if not normalized:
        return False

    has_onboarding_context = "onboarding" in normalized or "interview" in normalized
    has_start_intent = any(
        token in normalized
        for token in ("start", "begin", "kickoff", "kick off", "let us start", "lets start")
    )
    return has_onboarding_context and has_start_intent


def _is_life_onboarding_skip_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False

    skip_markers = (
        "skip",
        "skip this",
        "pass",
        "move on",
        "next question",
        "not now",
        "later",
    )
    return any(marker in normalized for marker in skip_markers)


def _is_life_onboarding_resume_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False

    resume_markers = (
        "resume",
        "continue",
        "pick up",
        "where we left off",
    )
    has_onboarding_context = "onboarding" in normalized or "interview" in normalized
    return any(marker in normalized for marker in resume_markers) and (
        has_onboarding_context or len(normalized.split()) <= 3
    )


def _is_life_onboarding_topic_complete(status: Optional[str]) -> bool:
    if not isinstance(status, str):
        return False
    normalized = status.strip().lower()
    return normalized in {"complete", "completed", "done", "finished"}


def _derive_life_onboarding_resume_index_from_context(
    *,
    orchestration_context: Optional[Dict[str, Any]],
    life_topic: str,
) -> Optional[int]:
    if not isinstance(orchestration_context, dict):
        return None

    onboarding_state = orchestration_context.get("onboarding_state")
    if not isinstance(onboarding_state, dict):
        return None

    topic_progress = onboarding_state.get("topic_progress")
    if not isinstance(topic_progress, dict):
        return None

    progress = topic_progress.get(life_topic)
    if not isinstance(progress, dict):
        return None

    question_total = _as_int(progress.get("question_total"), 0, 0, 10_000)
    question_index = _as_int(progress.get("question_index"), 0, 0, 10_000)
    if question_total <= 0:
        return None

    if question_index <= 0:
        return 1
    if question_index >= question_total:
        return question_total
    return question_index + 1


def _extract_seed_questions(interview_markdown: str) -> List[str]:
    if not isinstance(interview_markdown, str) or not interview_markdown.strip():
        return []

    questions: List[str] = []
    in_seed_section = False
    for raw_line in interview_markdown.splitlines():
        line = raw_line.strip()
        if not in_seed_section:
            if line.lower().startswith("## seed questions"):
                in_seed_section = True
            continue

        if line.startswith("## "):
            break

        match = re.match(r"^\d+\.\s+(.*\S)\s*$", line)
        if match:
            questions.append(match.group(1).strip())

    if questions:
        return questions

    for raw_line in interview_markdown.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\d+\.\s+(.*\S)\s*$", line)
        if match:
            questions.append(match.group(1).strip())

    return questions


def _dedupe_questions(questions: List[str]) -> List[str]:
    seen: set[str] = set()
    deduped: List[str] = []
    for question in questions:
        if not isinstance(question, str):
            continue
        normalized = " ".join(question.strip().split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _extract_agent_focus_lines(agent_markdown: str) -> List[str]:
    if not isinstance(agent_markdown, str) or not agent_markdown.strip():
        return []

    focus_lines: List[str] = []
    in_focus_section = False
    for raw_line in agent_markdown.splitlines():
        line = raw_line.strip()
        lowered = line.lower()

        if lowered.startswith("## ") and "focus" in lowered:
            in_focus_section = True
            continue
        if lowered.startswith("## ") and in_focus_section:
            break
        if not in_focus_section:
            continue

        bullet_match = re.match(r"^[-*]\s+(.*\S)\s*$", line)
        if bullet_match:
            focus_lines.append(bullet_match.group(1).strip())
            continue

        numbered_match = re.match(r"^\d+\.\s+(.*\S)\s*$", line)
        if numbered_match:
            focus_lines.append(numbered_match.group(1).strip())
            continue

    if focus_lines:
        return focus_lines

    fallback_lines: List[str] = []
    for raw_line in agent_markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.split()) < 3:
            continue
        fallback_lines.append(line)
        if len(fallback_lines) >= 5:
            break
    return fallback_lines


def _build_life_questions_from_agent_focus(
    *,
    life_topic: str,
    agent_markdown: Optional[str],
) -> List[str]:
    topic_key = str(life_topic or "").strip().lower()
    defaults = list(LIFE_ONBOARDING_DEFAULT_QUESTIONS.get(topic_key, []))
    focus_text = agent_markdown or ""
    focus_lower = focus_text.lower()

    keyword_map = LIFE_ONBOARDING_KEYWORD_QUESTION_MAP.get(topic_key, {})
    prioritized: List[str] = []
    for keyword, question in keyword_map.items():
        if keyword in focus_lower:
            prioritized.append(question)

    focus_lines = _extract_agent_focus_lines(focus_text)
    for line in focus_lines:
        cleaned = line.strip().rstrip(".")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered.startswith("use this folder for"):
            continue
        if lowered.startswith("help the user"):
            continue
        prioritized.append(
            f"What is your current situation with {cleaned.lower()}, and what would you like to improve first?"
        )

    combined = _dedupe_questions(prioritized + defaults)
    if not combined:
        combined = _dedupe_questions(
            [
                f"What matters most to you in {topic_key} right now?",
                f"What is currently working well in your {topic_key} area?",
                f"What constraints are making progress harder in {topic_key}?",
                "What would make the next 30 days feel successful?",
            ]
        )
    return combined[:MAX_LIFE_ONBOARDING_OPENING_QUESTIONS]


def _extract_opening_questions_from_state(state: Dict[str, Any]) -> List[str]:
    questions = state.get("questions")
    if not isinstance(questions, list):
        return []
    parsed: List[str] = []
    for item in questions:
        if isinstance(item, str) and item.strip():
            parsed.append(item.strip())
    deduped = _dedupe_questions(parsed)
    return deduped[:MAX_LIFE_ONBOARDING_OPENING_QUESTIONS]


def _parse_yes_no(message_text: Optional[str]) -> Optional[bool]:
    if not isinstance(message_text, str):
        return None
    normalized = message_text.strip().lower()
    if not normalized:
        return None
    yes_tokens = ("yes", "yep", "yeah", "sure", "ok", "okay", "do it")
    no_tokens = ("no", "nope", "not now", "skip", "later")
    if any(token in normalized for token in yes_tokens):
        return True
    if any(token in normalized for token in no_tokens):
        return False
    return None


def _next_weekday(reference_date: datetime, target_weekday: int) -> datetime:
    days_ahead = (target_weekday - reference_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return reference_date + timedelta(days=days_ahead)


def _month_end(reference_date: datetime) -> datetime:
    if reference_date.month == 12:
        next_month = reference_date.replace(year=reference_date.year + 1, month=1, day=1)
    else:
        next_month = reference_date.replace(month=reference_date.month + 1, day=1)
    return next_month - timedelta(days=1)


def _normalize_relative_dates_in_text(text: str) -> tuple[str, List[Dict[str, str]]]:
    if not isinstance(text, str) or not text.strip():
        return text, []

    now = datetime.now(timezone.utc)
    normalized_text = text
    replacements: List[Dict[str, str]] = []

    static_phrases = {
        "today": now.date(),
        "tomorrow": (now + timedelta(days=1)).date(),
        "next week": (now + timedelta(days=7)).date(),
        "month end": _month_end(now).date(),
    }
    for phrase, resolved_date in static_phrases.items():
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", flags=re.IGNORECASE)
        if not pattern.search(normalized_text):
            continue
        replacement = f"{phrase} ({resolved_date.isoformat()})"
        normalized_text = pattern.sub(replacement, normalized_text)
        replacements.append({"phrase": phrase, "resolved_date": resolved_date.isoformat()})

    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    weekday_pattern = re.compile(
        r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        flags=re.IGNORECASE,
    )
    for match in list(weekday_pattern.finditer(normalized_text)):
        weekday_name = match.group(1).lower()
        resolved = _next_weekday(now, weekday_map[weekday_name]).date().isoformat()
        phrase = match.group(0)
        normalized_text = normalized_text.replace(phrase, f"{phrase} ({resolved})", 1)
        replacements.append({"phrase": phrase.lower(), "resolved_date": resolved})

    return normalized_text, replacements


def _summarize_approved_turns_for_followup_task(
    approved_turns: Any,
    *,
    max_chars: int = 140,
) -> str:
    if not isinstance(approved_turns, list):
        return ""

    snippets: List[str] = []
    for turn in approved_turns[-3:]:
        if not isinstance(turn, dict):
            continue
        answer = turn.get("answer")
        if not isinstance(answer, str):
            continue
        cleaned = " ".join(answer.split())
        if not cleaned:
            continue
        snippets.append(cleaned)

    if not snippets:
        return ""

    combined = "; ".join(snippets)
    if len(combined) <= max_chars:
        return combined
    return combined[: max(0, max_chars - 3)].rstrip() + "..."


def _trim_onboarding_task_title(value: str, *, max_chars: int = 140) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def _extract_due_date_from_segment(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if not match:
        return None
    return match.group(0)


def _clean_goal_task_segment(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip(" .;,-")
    if not cleaned:
        return ""
    # Remove explicit due-date tokens from title body when present.
    cleaned = re.sub(r"\b(by|due|on)\s+\d{4}-\d{2}-\d{2}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(by|due|on)\s+(today|tomorrow|next week|month end|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b(?:\s*\(\s*\d{4}-\d{2}-\d{2}\s*\))?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:priority|owner|tags?|scope|project)\s*:\s*[^,;.\n\r]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:assigned?\s+to)\s+[A-Za-z][A-Za-z0-9 .'\-]{1,60}\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:in|for|under)\s+(?:life/)?(?:finance|finances|fitness|relationship|relationships|career|whyfinder|why finder)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\(\s*\d{4}-\d{2}-\d{2}\s*\)", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .;,-")
    return cleaned


def _split_labeled_goal_task_segments(text: str) -> List[Dict[str, str]]:
    if not isinstance(text, str) or not text.strip():
        return []

    labeled_pattern = re.compile(r"\b(goal|task)\s*:\s*", flags=re.IGNORECASE)
    matches = list(labeled_pattern.finditer(text))
    if not matches:
        return []

    segments: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        kind = match.group(1).lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[start:end].strip(" \n\r\t.;,-")
        if not segment:
            continue
        segments.append({"kind": kind, "text": segment})
    return segments


def _fallback_goal_task_segments(text: str) -> List[Dict[str, str]]:
    if not isinstance(text, str) or not text.strip():
        return []

    segments: List[Dict[str, str]] = []
    for raw_line in text.splitlines():
        cleaned_line = raw_line.strip()
        if not cleaned_line:
            continue
        cleaned_line = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned_line).strip()
        if not cleaned_line:
            continue
        parts = [part.strip() for part in re.split(r"\s*;\s*", cleaned_line) if part.strip()]
        for part in parts:
            lower = part.lower()
            kind = "goal" if lower.startswith("goal ") or "goal" in lower[:16] else "task"
            segments.append({"kind": kind, "text": part})

    if segments:
        return segments
    return [{"kind": "task", "text": text.strip()}]


def _extract_onboarding_goal_task_segments(text: str) -> List[Dict[str, str]]:
    labeled = _split_labeled_goal_task_segments(text)
    raw_segments = labeled if labeled else _fallback_goal_task_segments(text)

    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for segment in raw_segments:
        kind = segment.get("kind", "task").strip().lower()
        raw_text = str(segment.get("text") or "").strip()
        if not raw_text:
            continue
        due = _extract_due_date_from_segment(raw_text)
        cleaned = _clean_goal_task_segment(raw_text)
        if not cleaned:
            cleaned = " ".join(raw_text.split()).strip()
        key = f"{kind}|{cleaned.lower()}|{due or ''}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "kind": "goal" if kind == "goal" else "task",
                "text": cleaned,
                "due": due or "",
            }
        )

    return deduped[:6]


def _build_onboarding_initial_task_payloads(
    *,
    topic_slug: str,
    topic_title: str,
    goals_tasks_text: str,
) -> List[Dict[str, Any]]:
    segments = _extract_onboarding_goal_task_segments(goals_tasks_text)
    payloads: List[Dict[str, Any]] = []
    for segment in segments:
        kind = segment["kind"]
        text = segment["text"]
        due = segment["due"] or None
        label = "Goal" if kind == "goal" else "Task"
        payload: Dict[str, Any] = {
            "title": _trim_onboarding_task_title(f"{topic_title} {label}: {text}"),
            "priority": "p2" if kind == "goal" else "p3",
            "tags": [topic_slug, "onboarding", kind],
            "scope": f"life/{topic_slug}",
        }
        if due:
            payload["due"] = due
        payloads.append(payload)
    return payloads


def _build_onboarding_followup_task_payload(
    *,
    topic_slug: str,
    topic_title: str,
    approved_turns: Any,
) -> Dict[str, Any]:
    due_date = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    title = f"{topic_title} follow-up interview check-in"

    return {
        "title": title,
        "priority": "p2",
        "tags": [topic_slug, "onboarding", "followup"],
        "scope": f"life/{topic_slug}",
        "due": due_date,
    }


def _extract_markdown_content_from_tool_payload(tool_payload: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tool_payload, dict):
        return None

    nested = tool_payload.get("data")
    if isinstance(nested, dict):
        content = nested.get("content")
        if isinstance(content, str) and content.strip():
            return content

    content = tool_payload.get("content")
    if isinstance(content, str) and content.strip():
        return content

    return None



def _extract_life_topic_from_scope_path(scope_path: Any) -> Optional[str]:
    if not isinstance(scope_path, str):
        return None

    normalized = scope_path.strip().replace("\\", "/").strip("/")
    if not normalized.startswith("life/"):
        return None

    parts = normalized.split("/")
    if len(parts) < 2:
        return None

    topic = parts[1].strip().lower()
    if topic in LIFE_ONBOARDING_TOPICS:
        return topic

    return None


def _extract_cross_pollination_target_topics(
    message_text: Optional[str],
    *,
    source_topic: Optional[str],
) -> List[Dict[str, Any]]:
    if not isinstance(message_text, str):
        return []

    normalized = " ".join(message_text.strip().split())
    lowered = normalized.lower()
    if not lowered:
        return []

    if lowered.endswith("?") and len(lowered.split()) <= 18:
        return []

    source_normalized = str(source_topic or "").strip().lower()
    ranked: List[Dict[str, Any]] = []
    for topic, keywords in CROSS_POLLINATION_TOPIC_KEYWORDS.items():
        if source_normalized and topic == source_normalized:
            continue
        hits: List[str] = []
        for keyword in keywords:
            keyword_normalized = str(keyword or "").strip().lower()
            if not keyword_normalized:
                continue
            if re.search(rf"\b{re.escape(keyword_normalized)}\b", lowered):
                hits.append(keyword_normalized)
        if not hits:
            continue
        ranked.append(
            {
                "topic": topic,
                "score": len(hits),
                "keywords": hits,
            }
        )

    ranked.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("topic") or ""),
        )
    )
    return ranked


def _build_cross_pollination_followthrough_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if _is_capture_intake_conversation(conversation_type):
        return None
    if _is_digest_conversation(conversation_type):
        return None
    can_create_markdown = "create_markdown" in available_tool_names
    can_read_markdown = "read_markdown" in available_tool_names
    if not can_create_markdown and not can_read_markdown:
        return None

    attempted_statuses = {
        "success",
        "error",
        "blocked_intent",
        "blocked_context",
        "denied",
    }
    if executed_tool_calls:
        # Allow non-synthetic read-only calls (provider variance) but stop chaining
        # if non-synthetic mutating calls already occurred in this pass.
        blocking_mutations = []
        for call in executed_tool_calls:
            if not isinstance(call, dict):
                continue
            if bool(call.get("synthetic_reason")):
                continue
            status = str(call.get("status") or "").strip().lower()
            if status and status not in attempted_statuses:
                continue
            tool_name = str(call.get("name") or "").strip()
            if infer_safety_class(tool_name) == "mutating":
                blocking_mutations.append(call)
        if blocking_mutations:
            return None

    scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    source_topic = _extract_life_topic_from_scope_path(scope_path)
    if not source_topic:
        return None

    normalized_message = " ".join(str(latest_user_message or "").strip().split())
    if not normalized_message:
        return None
    if _normalize_approval_action(normalized_message):
        for item in reversed(executed_tool_calls):
            if not isinstance(item, dict):
                continue
            reason = str(item.get("synthetic_reason") or "").strip().lower()
            if not reason.startswith("cross_pollination_"):
                continue
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                continue
            content = arguments.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            captured_match = re.search(
                r"##\s*Captured Context\s*(.+?)(?:\n##\s*|\Z)",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not captured_match:
                continue
            extracted = " ".join(str(captured_match.group(1) or "").strip().split())
            if extracted:
                normalized_message = extracted
                break

    now_utc = datetime.now(timezone.utc)
    today_iso = now_utc.date().isoformat()
    slug = _slugify_capture_fragment(
        normalized_message,
        fallback=f"{source_topic}-context",
        max_length=42,
    )

    candidates = _extract_cross_pollination_target_topics(
        normalized_message,
        source_topic=source_topic,
    )
    source_title = LIFE_ONBOARDING_TOPICS.get(source_topic) or source_topic.title()

    if not can_create_markdown:
        candidate = candidates[0] if candidates else None
        if not isinstance(candidate, dict):
            return None
        target_topic = str(candidate.get("topic") or "").strip().lower()
        if target_topic not in LIFE_ONBOARDING_TOPICS:
            return None
        context_reason = f"cross_pollination_context_{source_topic}_to_{target_topic}"
        if _has_synthetic_reason_execution(
            executed_tool_calls,
            context_reason,
            statuses=attempted_statuses,
        ):
            return None
        return {
            "id": f"auto_cross_pollination_context_{source_topic}_{target_topic}",
            "name": "read_markdown",
            "arguments": {
                "path": f"life/{target_topic}/{CROSS_POLLINATION_CONTEXT_SOURCE_FILE}",
            },
            "synthetic": True,
            "reason": context_reason,
        }

    for candidate in candidates:
        target_topic = str(candidate.get("topic") or "").strip().lower()
        if target_topic not in LIFE_ONBOARDING_TOPICS:
            continue

        synthetic_reason = f"cross_pollination_{source_topic}_to_{target_topic}"
        context_reason = f"cross_pollination_context_{source_topic}_to_{target_topic}"
        if _has_synthetic_reason_execution(
            executed_tool_calls,
            synthetic_reason,
            statuses=attempted_statuses,
        ):
            continue
        if can_read_markdown and not _has_synthetic_reason_execution(
            executed_tool_calls,
            context_reason,
            statuses=attempted_statuses,
        ):
            return {
                "id": f"auto_cross_pollination_context_{source_topic}_{target_topic}",
                "name": "read_markdown",
                "arguments": {
                    "path": f"life/{target_topic}/{CROSS_POLLINATION_CONTEXT_SOURCE_FILE}",
                },
                "synthetic": True,
                "reason": context_reason,
            }

        target_title = LIFE_ONBOARDING_TOPICS.get(target_topic) or target_topic.title()
        keyword_hits = [
            item
            for item in (candidate.get("keywords") or [])
            if isinstance(item, str) and item.strip()
        ]
        keyword_text = ", ".join(keyword_hits[:6]) if keyword_hits else "detected topic overlap"
        target_context = _extract_cross_pollination_context_excerpt(
            executed_tool_calls=executed_tool_calls,
            synthetic_reason=context_reason,
        )
        content = "\n".join(
            [
                "# Cross-Pollination Context",
                "",
                f"- Date (UTC): {now_utc.isoformat()}",
                f"- Source Topic: {source_title}",
                f"- Target Topic: {target_title}",
                f"- Trigger Keywords: {keyword_text}",
                "",
                "## Captured Context",
                normalized_message,
                "",
                "## Target Topic Context",
                (
                    f"From life/{target_topic}/{CROSS_POLLINATION_CONTEXT_SOURCE_FILE}: "
                    f"{target_context}"
                    if target_context
                    else "No target-topic context was available from AGENT.md during this run."
                ),
            ]
        )
        path = f"life/{target_topic}/cross-pollination/{today_iso}-{slug}.md"
        return {
            "id": f"auto_cross_pollination_{source_topic}_{target_topic}",
            "name": "create_markdown",
            "arguments": {
                "path": path,
                "content": content,
            },
            "synthetic": True,
            "reason": synthetic_reason,
        }

    return None


def _extract_cross_pollination_context_excerpt(
    *,
    executed_tool_calls: List[Dict[str, Any]],
    synthetic_reason: str,
) -> Optional[str]:
    expected_reason = str(synthetic_reason or "").strip().lower()
    if not expected_reason:
        return None

    for item in reversed(executed_tool_calls):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("synthetic_reason") or "").strip().lower()
        if reason != expected_reason:
            continue
        if str(item.get("status") or "").strip().lower() != "success":
            return None
        if str(item.get("name") or "").strip() != "read_markdown":
            return None

        payload = item.get("result")
        content = _extract_markdown_content_from_tool_payload(
            payload if isinstance(payload, dict) else {}
        )
        if not content:
            return None

        compact = " ".join(content.split())
        if not compact:
            return None

        if len(compact) > CROSS_POLLINATION_CONTEXT_MAX_CHARS:
            compact = f"{compact[:CROSS_POLLINATION_CONTEXT_MAX_CHARS].rstrip()}..."
        return compact
    return None


def _parse_chat_approval_action(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    normalized = message_text.strip().lower()
    if not normalized:
        return None

    direct = _normalize_approval_action(normalized)
    if direct:
        return direct

    if any(token in normalized for token in ("approve", "approved", "yes", "allow", "looks good", "go ahead")):
        return "approve"
    if any(token in normalized for token in ("reject", "denied", "deny", "no", "change it", "edit")):
        return "reject"
    return None


def _has_tool_execution(
    executed_tool_calls: List[Dict[str, Any]],
    tool_name: str,
    *,
    statuses: Optional[set[str]] = None,
) -> bool:
    expected = str(tool_name or "").strip()
    if not expected:
        return False

    normalized_statuses = {item.strip().lower() for item in (statuses or {"success"}) if str(item).strip()}
    if not normalized_statuses:
        normalized_statuses = {"success"}

    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if name == expected and status in normalized_statuses:
            return True
    return False


def _has_successful_tool_execution(
    executed_tool_calls: List[Dict[str, Any]],
    tool_name: str,
) -> bool:
    return _has_tool_execution(
        executed_tool_calls,
        tool_name,
        statuses={"success"},
    )


def _capture_task_mutation_kind(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return None

    if not re.search(r"\b(task|tasks|todo|to-do|t-\d{1,6})\b", normalized):
        return None

    negated_mutation_patterns = (
        r"\b(?:do\s+not|don't|dont|without)\s+(?:create|add|open|make|edit|modify|update|change|complete|close|finish|resolve|mark)\b",
        r"\blookup\s+only\b",
        r"\bread[\s-]?only\b",
        r"\bno\s+changes?\b",
    )
    if any(re.search(pattern, normalized) for pattern in negated_mutation_patterns):
        return None

    create_new_markers = (
        "create a task",
        "create task",
        "new task",
        "add a task",
        "add task",
        "make a task",
        "open a task",
        "log a task",
        "track a task",
    )
    if any(marker in normalized for marker in create_new_markers):
        return None

    completion_patterns = (
        r"\bcomplete(d|ing)?\b",
        r"\bmark\b.{0,32}\b(done|complete|completed)\b",
        r"\bdone\b",
        r"\bfinish(ed|ing)?\b",
        r"\bclose(d|ing)?\b",
        r"\bresolve(d|ing)?\b",
    )
    if any(re.search(pattern, normalized) for pattern in completion_patterns):
        return "complete"

    edit_patterns = (
        r"\b(edit|update|modify|change|adjust|rename|reschedule)\b",
        r"\bassign(?:ed)?\b",
        r"\bset\b.{0,32}\b(?:due|owner|assignee|priority|status|tags?|scope|project)\b",
        r"\bmove\b.{0,32}\b(?:due|owner|assignee|priority|status|tags?|scope|project|to\s+p[0-3])\b",
    )
    if any(re.search(pattern, normalized) for pattern in edit_patterns):
        return "edit"

    return None


def _capture_is_task_lookup_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False

    if not re.search(r"\b(task|tasks|todo|to-do|t-\d{1,6})\b", normalized):
        return False

    if _capture_task_mutation_kind(normalized) in {"complete", "edit"}:
        return False

    create_markers = (
        "create a task",
        "create task",
        "new task",
        "add a task",
        "add task",
        "make a task",
        "open a task",
        "log a task",
        "track a task",
    )
    if any(marker in normalized for marker in create_markers):
        return False

    if normalized.endswith("?"):
        return True

    lookup_markers = (
        "check",
        "show",
        "list",
        "find",
        "lookup",
        "look up",
        "search",
        "existing",
        "open task",
        "open tasks",
        "already",
        "match",
        "matches",
        "read-only",
        "read only",
        "lookup only",
    )
    return any(marker in normalized for marker in lookup_markers)


def _is_capture_existing_task_mutation_intent(message_text: Optional[str]) -> bool:
    return _capture_task_mutation_kind(message_text) is not None


def _capture_task_guard_error(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    tool_name: str,
    executed_tool_calls: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not _is_capture_intake_conversation(conversation_type):
        return None

    normalized_tool = str(tool_name or "").strip()
    if normalized_tool not in {"create_task", "update_task", "complete_task"}:
        return None

    if not _is_capture_existing_task_mutation_intent(latest_user_message):
        return None

    if normalized_tool == "create_task":
        return {
            "code": "CAPTURE_TASK_INTENT_CONFLICT",
            "message": (
                "Completion/edit intent detected. Do not call create_task for existing-task completion or edits. "
                "Call list_tasks first, then use update_task and/or complete_task with the matched task ID."
            ),
        }

    if normalized_tool in {"update_task", "complete_task"} and not _has_successful_tool_execution(
        executed_tool_calls,
        "list_tasks",
    ):
        return {
            "code": "CAPTURE_TASK_ID_RESOLUTION_REQUIRED",
            "message": (
                "Task mutation requires ID resolution first. Call list_tasks, then use update_task/complete_task "
                "for the matched task ID."
            ),
        }

    return None


def _rewrite_capture_decisions_path(
    *,
    conversation_type: str,
    mcp_scope: Dict[str, Any],
    tool_name: str,
    tool_arguments: Dict[str, Any],
) -> Dict[str, Any]:
    if not _is_capture_intake_conversation(conversation_type):
        return tool_arguments
    if tool_name not in {"create_markdown", "write_markdown"}:
        return tool_arguments
    if not isinstance(tool_arguments, dict):
        return tool_arguments

    path_key: Optional[str] = None
    raw_path: Optional[str] = None
    for key in ("path", "file_path"):
        candidate = tool_arguments.get(key)
        if isinstance(candidate, str) and candidate.strip():
            path_key = key
            raw_path = candidate
            break

    if not path_key or not raw_path:
        return tool_arguments

    normalized_path = raw_path.strip().replace("\\", "/")
    normalized_path = re.sub(r"^(?:\./)+", "", normalized_path)
    normalized_path = normalized_path.lstrip("/")

    if normalized_path.lower() != "decisions.md":
        return tool_arguments

    normalized_scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    if not normalized_scope_path or normalized_scope_path in {"capture", "life"}:
        return tool_arguments

    scoped_decisions_path = f"{normalized_scope_path.rstrip('/')}/decisions.md"
    if scoped_decisions_path.lower() == normalized_path.lower():
        return tool_arguments

    rewritten_arguments = dict(tool_arguments)
    rewritten_arguments[path_key] = scoped_decisions_path
    return rewritten_arguments


def _normalize_capture_inbox_path_date(path_value: str) -> str:
    if not isinstance(path_value, str):
        return path_value

    normalized_path = path_value.strip().replace("\\", "/")
    normalized_path = re.sub(r"^(?:\./)+", "", normalized_path)
    normalized_path = normalized_path.lstrip("/")
    if not normalized_path:
        return path_value

    lowered = normalized_path.lower()
    if not lowered.startswith("capture/inbox/"):
        # Provider variance can emit non-canonical inbox paths such as
        # "finances/relationships/inbox/2023-10-05.md" in capture mode.
        # Force these to canonical capture/inbox before date normalization.
        normalized_tokens = [token for token in normalized_path.split("/") if token]
        inbox_index = -1
        for idx, token in enumerate(normalized_tokens):
            if token.lower() == "inbox":
                inbox_index = idx
                break
        if inbox_index < 0:
            return normalized_path
        filename = (
            normalized_tokens[inbox_index + 1]
            if inbox_index + 1 < len(normalized_tokens)
            else ""
        )
        if not filename:
            filename = "capture-entry.md"
        if not filename.lower().endswith(".md"):
            filename = f"{filename}.md"
        normalized_path = f"capture/inbox/{filename}"

    parent, _, filename = normalized_path.rpartition("/")
    if not filename:
        return normalized_path

    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>(?:[-_].*)?\.md)$",
        filename,
        flags=re.IGNORECASE,
    )
    if not match:
        return normalized_path

    today_iso = datetime.now(timezone.utc).date().isoformat()
    if match.group("date") == today_iso:
        return normalized_path

    updated_filename = f"{today_iso}{match.group('suffix')}"
    return f"{parent}/{updated_filename}" if parent else updated_filename


def _normalize_capture_due_value(raw_due: Any) -> Optional[str]:
    if not isinstance(raw_due, str):
        return None

    candidate = raw_due.strip()
    if not candidate:
        return None

    parsed_iso = _extract_due_date_from_segment(candidate)
    if parsed_iso:
        return parsed_iso

    normalized_text, _ = _normalize_relative_dates_in_text(candidate)
    parsed_normalized = _extract_due_date_from_segment(normalized_text)
    if parsed_normalized:
        return parsed_normalized

    return None


def _normalize_capture_markdown_content(
    *,
    content: str,
    path: Optional[str],
    ensure_capture_stamp: bool,
) -> str:
    if not isinstance(content, str):
        return content

    normalized_content, _date_resolutions = _normalize_relative_dates_in_text(content)
    if not ensure_capture_stamp:
        return normalized_content

    normalized_path = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized_path.lower().startswith("capture/inbox/"):
        return normalized_content

    if re.search(r"\bcaptured on\s*\(utc\)\s*:\s*\d{4}-\d{2}-\d{2}\b", normalized_content, flags=re.IGNORECASE):
        return normalized_content
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", normalized_content):
        return normalized_content

    today_iso = datetime.now(timezone.utc).date().isoformat()
    body = normalized_content.lstrip("\n")
    return f"Captured On (UTC): {today_iso}\n\n{body}" if body else f"Captured On (UTC): {today_iso}\n"


def _normalize_owner_profile_tool_arguments(
    *,
    latest_user_message: Optional[str],
    tool_name: str,
    tool_arguments: Dict[str, Any],
    synthetic_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if tool_name not in {"create_markdown", "write_markdown"}:
        return tool_arguments
    if not isinstance(tool_arguments, dict):
        return tool_arguments

    normalized_reason = str(synthetic_reason or "").strip().lower()
    owner_profile_intent = bool(_extract_owner_profile_update_text(latest_user_message))
    if normalized_reason not in OWNER_PROFILE_UPDATE_REASONS and not owner_profile_intent:
        return tool_arguments

    rewritten_arguments = dict(tool_arguments)
    for path_key in ("path", "file_path"):
        if isinstance(rewritten_arguments.get(path_key), str):
            rewritten_arguments[path_key] = OWNER_PROFILE_RELATIVE_PATH
            return rewritten_arguments

    rewritten_arguments["path"] = OWNER_PROFILE_RELATIVE_PATH
    return rewritten_arguments


def _normalize_life_scope_task_arguments(
    *,
    conversation_type: str,
    mcp_scope: Dict[str, Any],
    tool_name: str,
    tool_arguments: Dict[str, Any],
) -> Dict[str, Any]:
    if tool_name != "create_task":
        return tool_arguments
    if not isinstance(tool_arguments, dict):
        return tool_arguments

    life_topic = _extract_life_topic(conversation_type)
    if not life_topic:
        scope_path = _normalize_project_scope_path(
            mcp_scope.get("mcp_project_slug") if isinstance(mcp_scope, dict) else None
        )
        life_topic = _extract_life_topic_from_scope_path(scope_path)
    if not life_topic:
        return tool_arguments

    normalized_arguments = dict(tool_arguments)
    default_scope = f"life/{life_topic}"

    existing_scope = normalized_arguments.get("scope")
    if isinstance(existing_scope, str) and existing_scope.strip():
        scope_token = existing_scope.strip().lower()
        if "/" not in scope_token:
            alias_topic = CAPTURE_LIFE_TOPIC_ALIASES.get(scope_token)
            if alias_topic:
                normalized_arguments["scope"] = f"life/{alias_topic}"
            else:
                normalized_arguments["scope"] = default_scope
    else:
        normalized_arguments["scope"] = default_scope

    resolved_scope = str(normalized_arguments.get("scope") or "").strip().lower()
    if resolved_scope.startswith("life/"):
        normalized_arguments["project"] = resolved_scope.split("/")[-1]
    elif not (isinstance(normalized_arguments.get("project"), str) and normalized_arguments.get("project").strip()):
        normalized_arguments["project"] = life_topic

    owner_value = normalized_arguments.get("owner")
    if not (isinstance(owner_value, str) and owner_value.strip()):
        normalized_arguments["owner"] = "user"

    return normalized_arguments


def _normalize_capture_tool_arguments(
    *,
    conversation_type: str,
    mcp_scope: Dict[str, Any],
    tool_name: str,
    tool_arguments: Dict[str, Any],
    ensure_capture_stamp: bool = True,
) -> Dict[str, Any]:
    rewritten_arguments = _rewrite_capture_decisions_path(
        conversation_type=conversation_type,
        mcp_scope=mcp_scope,
        tool_name=tool_name,
        tool_arguments=tool_arguments,
    )
    if isinstance(rewritten_arguments, dict):
        rewritten_arguments = _normalize_life_scope_task_arguments(
            conversation_type=conversation_type,
            mcp_scope=mcp_scope,
            tool_name=tool_name,
            tool_arguments=rewritten_arguments,
        )
    if not _is_capture_intake_conversation(conversation_type) or not isinstance(
        rewritten_arguments, dict
    ):
        return rewritten_arguments

    normalized_arguments = dict(rewritten_arguments)

    if tool_name in {"create_markdown", "write_markdown"}:
        for path_key in ("path", "file_path"):
            path_candidate = normalized_arguments.get(path_key)
            if isinstance(path_candidate, str) and path_candidate.strip():
                normalized_arguments[path_key] = _normalize_capture_inbox_path_date(path_candidate)
                break

    if tool_name == "create_markdown":
        content = normalized_arguments.get("content")
        if isinstance(content, str):
            content_path = (
                normalized_arguments.get("path")
                if isinstance(normalized_arguments.get("path"), str)
                else normalized_arguments.get("file_path")
            )
            normalized_arguments["content"] = _normalize_capture_markdown_content(
                content=content,
                path=content_path if isinstance(content_path, str) else None,
                ensure_capture_stamp=ensure_capture_stamp,
            )

    if tool_name == "write_markdown":
        operation = normalized_arguments.get("operation")
        if isinstance(operation, dict) and isinstance(operation.get("content"), str):
            updated_operation = dict(operation)
            updated_operation["content"] = _normalize_capture_markdown_content(
                content=operation["content"],
                path=str(normalized_arguments.get("path") or normalized_arguments.get("file_path") or ""),
                ensure_capture_stamp=False,
            )
            normalized_arguments["operation"] = updated_operation

    if tool_name == "create_task":
        normalized_due = _normalize_capture_due_value(normalized_arguments.get("due"))
        if normalized_due:
            normalized_arguments["due"] = normalized_due
        elif isinstance(normalized_arguments.get("title"), str):
            title_due = _normalize_capture_due_value(normalized_arguments.get("title"))
            if title_due:
                normalized_arguments["due"] = title_due

        scope_hint_parts: List[str] = []
        for key in ("title", "description", "notes"):
            value = normalized_arguments.get(key)
            if isinstance(value, str) and value.strip():
                scope_hint_parts.append(value.strip())
        scope_hint_text = " ".join(scope_hint_parts) if scope_hint_parts else None

        inferred_scope = _extract_capture_scope_path_from_message(
            message_text=scope_hint_text,
            mcp_scope=mcp_scope,
        )
        resolved_scope: Optional[str] = None

        existing_scope = normalized_arguments.get("scope")
        if isinstance(existing_scope, str) and existing_scope.strip():
            scope_token = existing_scope.strip()
            scope_token_lower = scope_token.lower()
            alias_topic = CAPTURE_LIFE_TOPIC_ALIASES.get(scope_token_lower)
            if not alias_topic:
                alias_items = sorted(
                    CAPTURE_LIFE_TOPIC_ALIASES.items(),
                    key=lambda item: len(item[0]),
                    reverse=True,
                )
                for alias, topic in alias_items:
                    if re.search(rf"\b{re.escape(alias)}\b", scope_token_lower):
                        alias_topic = topic
                        break
            if alias_topic:
                resolved_scope = f"life/{alias_topic}"
            elif scope_token_lower in {"personal", "general", "default", "self", "me", "my"}:
                if inferred_scope and inferred_scope not in {"capture", "life"}:
                    resolved_scope = inferred_scope
        else:
            if inferred_scope and inferred_scope not in {"capture", "life"}:
                resolved_scope = inferred_scope

        if resolved_scope:
            normalized_arguments["scope"] = resolved_scope
            if "/" in resolved_scope:
                normalized_arguments["project"] = resolved_scope.split("/")[-1]

        owner_value = normalized_arguments.get("owner")
        if not (isinstance(owner_value, str) and owner_value.strip()):
            inferred_owner = _extract_capture_owner_from_text(scope_hint_text)
            normalized_arguments["owner"] = inferred_owner or "user"

    if tool_name == "list_tasks":
        # Capture verify flows should not over-filter list_tasks. Provider-supplied
        # filters (owner/tag/status/scope) frequently cause false negatives.
        normalized_arguments = {}

    if tool_name == "update_task":
        fields = normalized_arguments.get("fields")
        if isinstance(fields, dict):
            normalized_due = _normalize_capture_due_value(fields.get("due"))
            if normalized_due:
                updated_fields = dict(fields)
                updated_fields["due"] = normalized_due
                normalized_arguments["fields"] = updated_fields

    return normalized_arguments


def _extract_capture_project_hint(mcp_scope: Dict[str, Any]) -> Optional[str]:
    if not isinstance(mcp_scope, dict):
        return None

    normalized_scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    if not normalized_scope_path:
        return None

    if normalized_scope_path.startswith("life/"):
        parts = normalized_scope_path.split("/")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()

    if normalized_scope_path.startswith("projects/active/"):
        parts = normalized_scope_path.split("/")
        if parts and parts[-1].strip():
            return parts[-1].strip()

    if normalized_scope_path in {"capture", "life"}:
        return None

    if "/" not in normalized_scope_path and normalized_scope_path.strip():
        return normalized_scope_path.strip()
    return None


def _has_synthetic_reason_execution(
    executed_tool_calls: List[Dict[str, Any]],
    synthetic_reason: str,
    *,
    statuses: Optional[set[str]] = None,
) -> bool:
    expected = str(synthetic_reason or "").strip().lower()
    if not expected:
        return False

    normalized_statuses = {item.strip().lower() for item in (statuses or {"success"}) if str(item).strip()}
    if not normalized_statuses:
        normalized_statuses = {"success"}

    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("synthetic_reason") or "").strip().lower()
        if status in normalized_statuses and reason == expected:
            return True
    return False


def _has_successful_synthetic_reason(
    executed_tool_calls: List[Dict[str, Any]],
    synthetic_reason: str,
) -> bool:
    return _has_synthetic_reason_execution(
        executed_tool_calls,
        synthetic_reason,
        statuses={"success"},
    )


def _slugify_capture_fragment(value: str, *, fallback: str = "entry", max_length: int = 48) -> str:
    if not isinstance(value, str):
        return fallback

    normalized = " ".join(value.strip().split())
    if not normalized:
        return fallback

    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        return fallback
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or fallback


def _extract_capture_life_topic_from_message(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    lowered = " ".join(message_text.strip().lower().split())
    if not lowered:
        return None

    alias_items = sorted(
        CAPTURE_LIFE_TOPIC_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, topic in alias_items:
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return topic

    finance_signals = (
        r"\$\s*\d+(?:\.\d+)?",
        r"\b\d+(?:\.\d+)?\s*(?:dollars?|usd)\b",
    )
    if any(re.search(pattern, lowered) for pattern in finance_signals):
        return "finances"

    finance_keywords = (
        "pay back",
        "payback",
        "repay",
        "repayment",
        "owe",
        "owed",
        "payment",
        "debt",
        "invoice",
        "bill",
    )
    if any(keyword in lowered for keyword in finance_keywords):
        return "finances"

    return None


def _extract_capture_scope_path_from_message(
    message_text: Optional[str],
    mcp_scope: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    normalized_scope_match = re.search(
        r"\b(?:scope|path)\s*:\s*([a-z0-9/_-]{3,120})",
        str(message_text or ""),
        flags=re.IGNORECASE,
    )
    if normalized_scope_match:
        explicit_scope = _normalize_project_scope_path(normalized_scope_match.group(1))
        if explicit_scope:
            return explicit_scope

    if isinstance(message_text, str):
        lowered = " ".join(message_text.strip().lower().split())
        if lowered:
            topic = _extract_capture_life_topic_from_message(lowered)
            if topic:
                if re.search(rf"\b(?:in|for|under)\s+{re.escape(topic)}\b", lowered):
                    return f"life/{topic}"
                if topic == "finances":
                    return "life/finances"

            explicit_project = re.search(
                r"\bproject\s*:\s*([a-z0-9][a-z0-9 _-]{1,60})\b",
                lowered,
            )
            if explicit_project:
                project_slug = _slugify_capture_fragment(
                    explicit_project.group(1),
                    fallback="project",
                    max_length=64,
                )
                if project_slug:
                    return f"projects/active/{project_slug}"

    scope_source = mcp_scope if isinstance(mcp_scope, dict) else {}
    scope_path = _normalize_project_scope_path(scope_source.get("mcp_project_slug"))
    if scope_path and scope_path not in {"capture", "life"}:
        return scope_path
    return None


def _extract_capture_fanout_scope_paths(
    *,
    message_text: Optional[str],
    mcp_scope: Optional[Dict[str, Any]] = None,
) -> List[str]:
    discovered: List[str] = []
    seen: set[str] = set()

    def _append_scope(candidate: Any) -> None:
        normalized = _normalize_project_scope_path(candidate)
        if not normalized or normalized in {"capture", "life"}:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        discovered.append(normalized)

    message_raw = str(message_text or "")
    for match in re.finditer(
        r"\b(?:scope|path)\s*:\s*([a-z0-9/_-]{3,120})",
        message_raw,
        flags=re.IGNORECASE,
    ):
        _append_scope(match.group(1))
    for match in re.finditer(
        r"\b((?:life/[a-z0-9][a-z0-9_-]{1,80})|(?:projects/[a-z0-9][a-z0-9/_-]{2,140}))\b",
        message_raw,
        flags=re.IGNORECASE,
    ):
        _append_scope(match.group(1))

    if isinstance(message_text, str):
        lowered = " ".join(message_text.strip().lower().split())
        if lowered:
            for explicit_project in re.finditer(
                r"\bproject\s*:\s*([a-z0-9][a-z0-9 _-]{1,60})\b",
                lowered,
            ):
                project_slug = _slugify_capture_fragment(
                    explicit_project.group(1),
                    fallback="project",
                    max_length=64,
                )
                if project_slug:
                    _append_scope(f"projects/active/{project_slug}")

            alias_items = sorted(
                CAPTURE_LIFE_TOPIC_ALIASES.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            )
            for phrase_match in re.finditer(
                r"\b(?:in|for|under|across|to)\s+([a-z0-9 ,/_-]{3,160})",
                lowered,
                flags=re.IGNORECASE,
            ):
                phrase_segment = re.split(
                    r"[.;!?]",
                    str(phrase_match.group(1) or ""),
                    maxsplit=1,
                )[0].strip()
                if not phrase_segment:
                    continue
                for alias, topic in alias_items:
                    if re.search(rf"\b{re.escape(alias)}\b", phrase_segment):
                        _append_scope(f"life/{topic}")

    scope_source = mcp_scope if isinstance(mcp_scope, dict) else {}
    default_scope = _normalize_project_scope_path(scope_source.get("mcp_project_slug"))
    _append_scope(default_scope)
    if isinstance(default_scope, str) and default_scope in discovered and len(discovered) > 1:
        discovered = [scope for scope in discovered if scope != default_scope] + [default_scope]

    return discovered[:CAPTURE_SCOPE_FANOUT_MAX_TARGETS]


def _capture_scope_fanout_token(scope_path: Any) -> str:
    normalized = str(scope_path or "").strip().lower()
    token = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if len(token) > 72:
        token = token[:72].rstrip("_")
    return token or "scope"


def _is_capture_inbox_path(path_value: Any) -> bool:
    if not isinstance(path_value, str):
        return False
    normalized = path_value.strip().replace("\\", "/")
    if not normalized:
        return False
    normalized = re.sub(r"^\./+", "", normalized)
    return normalized.startswith("capture/inbox/") or "/capture/inbox/" in normalized


def _extract_capture_inbox_content_from_tool_call(
    tool_name: Any,
    tool_arguments: Any,
) -> Optional[str]:
    if not isinstance(tool_arguments, dict):
        return None

    normalized_tool = str(tool_name or "").strip()
    if normalized_tool not in {"create_markdown", "write_markdown"}:
        return None

    if not _is_capture_inbox_path(tool_arguments.get("path")):
        return None

    direct_content = tool_arguments.get("content")
    if isinstance(direct_content, str) and direct_content.strip():
        return direct_content.strip()

    operation = tool_arguments.get("operation")
    if isinstance(operation, dict):
        operation_content = operation.get("content")
        if isinstance(operation_content, str) and operation_content.strip():
            return operation_content.strip()

    return None


def _build_capture_scope_fanout_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _is_capture_intake_conversation(conversation_type):
        return None
    if "create_markdown" not in available_tool_names:
        return None
    if not isinstance(latest_user_message, str):
        return None

    normalized_message = " ".join(latest_user_message.strip().split())
    if not normalized_message:
        return None
    if _normalize_approval_action(normalized_message):
        for item in reversed(executed_tool_calls):
            if not isinstance(item, dict):
                continue
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                continue
            fallback_content: Optional[str] = None
            if str(item.get("synthetic_reason") or "").strip().lower() == "capture_inbox_persist":
                synthetic_content = arguments.get("content")
                if isinstance(synthetic_content, str) and synthetic_content.strip():
                    fallback_content = synthetic_content.strip()
            if not fallback_content:
                fallback_content = _extract_capture_inbox_content_from_tool_call(
                    item.get("name"),
                    arguments,
                )
            if isinstance(fallback_content, str) and fallback_content.strip():
                normalized_message = " ".join(fallback_content.strip().split())
                break

    target_scopes = _extract_capture_fanout_scope_paths(
        message_text=normalized_message,
        mcp_scope=mcp_scope,
    )
    if len(target_scopes) < 2:
        return None

    attempted_statuses = {
        "success",
        "error",
        "blocked_intent",
        "blocked_context",
        "denied",
    }
    capture_write_attempted = (
        _has_synthetic_reason_execution(
            executed_tool_calls,
            "capture_inbox_persist",
            statuses=attempted_statuses,
        )
        or _has_tool_execution(
            executed_tool_calls,
            "create_markdown",
            statuses=attempted_statuses,
        )
        or _has_tool_execution(
            executed_tool_calls,
            "write_markdown",
            statuses=attempted_statuses,
        )
    )
    if not capture_write_attempted:
        return None

    today_iso = datetime.now(timezone.utc).date().isoformat()
    filename_slug = _slugify_capture_fragment(normalized_message, max_length=56)

    for scope_path in target_scopes:
        token = _capture_scope_fanout_token(scope_path)
        reason = f"capture_scope_fanout_{token}"
        if _has_synthetic_reason_execution(
            executed_tool_calls,
            reason,
            statuses=attempted_statuses,
        ):
            continue

        content = "\n".join(
            [
                "# Capture Fanout Entry",
                "",
                f"- Captured On (UTC): {today_iso}",
                f"- Target Scope: {scope_path}",
                f"- Source Inbox Path: capture/inbox/{today_iso}-{filename_slug}.md",
                "",
                "## Captured Context",
                normalized_message,
            ]
        )
        return {
            "id": f"auto_capture_scope_fanout_{token}",
            "name": "create_markdown",
            "arguments": {
                "path": f"{scope_path}/capture/{today_iso}-{filename_slug}.md",
                "content": content,
            },
            "synthetic": True,
            "reason": reason,
        }

    return None


def _capture_is_new_task_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False

    if not re.search(r"\b(task|tasks|todo|to-do)\b", normalized):
        return False

    if _capture_task_mutation_kind(normalized) in {"complete", "edit"}:
        return False

    if "task:" in normalized or normalized.startswith("todo:"):
        return True

    create_markers = (
        "create a task",
        "create task",
        "new task",
        "add a task",
        "add task",
        "make a task",
        "open a task",
        "log a task",
        "track a task",
    )
    return any(marker in normalized for marker in create_markers)


def _extract_capture_priority_from_text(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    lowered = " ".join(message_text.strip().lower().split())
    if not lowered:
        return None

    if re.search(r"\b(p0|critical|blocker)\b", lowered):
        return "p0"
    if re.search(r"\b(p1|urgent|highest priority|high priority)\b", lowered):
        return "p1"
    if re.search(r"\b(p2|medium priority|normal priority)\b", lowered):
        return "p2"
    if re.search(r"\b(p3|low priority)\b", lowered):
        return "p3"
    return None


def _extract_capture_owner_from_text(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    candidate = " ".join(message_text.strip().split())
    if not candidate:
        return None

    owner_patterns = (
        r"\bowner\s*:\s*([A-Za-z][A-Za-z0-9 .'\-]{0,60}?)(?=(?:\s+\b(?:due|by|priority|tags?|scope|project|in)\b|[,;.]|$))",
        r"\bowner\s+(?:to\s+)?([A-Za-z][A-Za-z0-9 .'\-]{0,60}?)(?=(?:\s+\b(?:for|task|due|by|priority|tags?|scope|project|in)\b|[,;.]|$))",
        r"\bassign(?:ed)?\s+to\s+([A-Za-z][A-Za-z0-9 .'\-]{0,60}?)(?=(?:\s+\b(?:due|by|priority|tags?|scope|project|in)\b|[,;.]|$))",
        r"\bassign\s+([A-Za-z][A-Za-z0-9 .'\-]{0,60}?)\s+to\b",
        r"\bfor\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2})(?=(?:\s+\b(?:to|by|due|on|in|with)\b|[,;.]|$))",
    )
    for pattern in owner_patterns:
        match = re.search(pattern, candidate, flags=re.IGNORECASE)
        if not match:
            continue
        owner = " ".join(match.group(1).strip(" .;,:-").split())
        if owner:
            return owner
    owner_from_labeled_task = _extract_capture_owner_from_task_labeled_segment(candidate)
    if owner_from_labeled_task:
        return owner_from_labeled_task
    return None


def _extract_capture_owner_from_task_labeled_segment(task_text: str) -> Optional[str]:
    if not isinstance(task_text, str):
        return None
    compact = " ".join(task_text.strip().split())
    if not compact:
        return None
    segment_match = re.search(r"\btask\s*:\s*([^\n\r;.,!?]{4,180})", compact, flags=re.IGNORECASE)
    if not segment_match:
        return None
    segment = " ".join(str(segment_match.group(1) or "").strip().split())
    if not segment:
        return None

    name_token = r"[A-Z][A-Za-z]{0,24}\.?"
    with_to = re.match(
        rf"^(?P<owner>{name_token}(?:\s+{name_token}){{1,2}})\s+to\s+[a-z].+$",
        segment,
    )
    if with_to:
        owner = " ".join(str(with_to.group("owner") or "").strip(" .;,:-").split())
        if owner:
            return owner

    no_to = re.match(
        rf"^(?P<owner>{name_token}(?:\s+{name_token}){{1,2}})\s+[a-z].+$",
        segment,
    )
    if no_to:
        owner = " ".join(str(no_to.group("owner") or "").strip(" .;,:-").split())
        if owner:
            return owner
    return None


def _strip_capture_owner_prefix_from_task_segment(task_text: str) -> str:
    compact = " ".join(str(task_text or "").split()).strip()
    if not compact:
        return ""

    name_token = r"[A-Z][A-Za-z]{0,24}\.?"
    with_to = re.match(
        rf"^(?P<owner>{name_token}(?:\s+{name_token}){{1,2}})\s+to\s+(?P<body>.+)$",
        compact,
    )
    if with_to:
        body = str(with_to.group("body") or "").strip()
        if body:
            return body

    no_to = re.match(
        rf"^(?P<owner>{name_token}(?:\s+{name_token}){{1,2}})\s+(?P<body>[a-z].+)$",
        compact,
    )
    if no_to:
        body = str(no_to.group("body") or "").strip()
        if body:
            return body
    return compact


def _extract_capture_tags_from_text(message_text: Optional[str]) -> List[str]:
    if not isinstance(message_text, str):
        return []

    tags: List[str] = []
    seen: set[str] = set()

    for hash_tag in re.findall(r"#([a-z0-9][a-z0-9_-]{1,30})", message_text, flags=re.IGNORECASE):
        normalized = hash_tag.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)

    labeled_match = re.search(
        r"\btags?\s*:\s*([^\n\r;.]*)",
        message_text,
        flags=re.IGNORECASE,
    )
    if labeled_match:
        raw_value = str(labeled_match.group(1) or "").strip()
        if raw_value:
            stop_match = re.search(
                r"\b(?:due|by|priority|owner|assign(?:ed)?|scope|project)\b",
                raw_value,
                flags=re.IGNORECASE,
            )
            if stop_match:
                raw_value = raw_value[: stop_match.start()].strip(" ,")

        if "," in raw_value:
            raw_tokens = [token.strip() for token in raw_value.split(",")]
        else:
            raw_tokens = re.split(r"\s+", raw_value)
        for token in raw_tokens:
            normalized = token.strip().lower().strip("#")
            if len(normalized) < 2:
                continue
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,30}", normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            tags.append(normalized)

    return tags


def _extract_capture_task_title_from_message(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    compact = " ".join(message_text.strip().split())
    if not compact:
        return None

    labeled_segments = _split_labeled_goal_task_segments(compact)
    for segment in labeled_segments:
        if str(segment.get("kind") or "").strip().lower() != "task":
            continue
        raw_segment = str(segment.get("text") or "")
        stripped_segment = _strip_capture_owner_prefix_from_task_segment(raw_segment)
        title = _clean_goal_task_segment(stripped_segment or raw_segment)
        if title:
            return title

    patterns = (
        r"\b(?:create|add|open|make|log|track)\s+(?:a\s+)?task(?:\s+to)?\s+(.+)$",
        r"\btodo\s*:\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        title = _clean_goal_task_segment(match.group(1))
        if title:
            return title

    fallback_title = compact
    fallback_title = re.sub(
        r"\b(?:add|create|open|make|log|track)\s+(?:a\s+)?task(?:\s+to)?\b",
        "",
        fallback_title,
        flags=re.IGNORECASE,
    )
    fallback_title = re.sub(
        r"^(?:capture this|note this|log this)\s*[:\-]?\s*",
        "",
        fallback_title,
        flags=re.IGNORECASE,
    )
    fallback_title = re.sub(r"^(?:and|then)\s+", "", fallback_title, flags=re.IGNORECASE)
    fallback_title = _clean_goal_task_segment(fallback_title)
    if fallback_title and len(fallback_title) >= 6 and not fallback_title.endswith("?"):
        if len(fallback_title) > 140:
            return fallback_title[:137].rstrip() + "..."
        return fallback_title

    return None


def _build_capture_create_task_arguments(
    *,
    message_text: Optional[str],
    mcp_scope: Dict[str, Any],
) -> Dict[str, Any]:
    title = _extract_capture_task_title_from_message(message_text)
    if not title:
        title = "Follow up on captured item"

    arguments: Dict[str, Any] = {"title": title}

    priority = _extract_capture_priority_from_text(message_text)
    if priority:
        arguments["priority"] = priority

    owner = _extract_capture_owner_from_text(message_text)
    if owner:
        arguments["owner"] = owner

    due_value = _normalize_capture_due_value(message_text if isinstance(message_text, str) else None)
    if due_value:
        arguments["due"] = due_value

    tags = _extract_capture_tags_from_text(message_text)
    if tags:
        arguments["tags"] = tags

    scope_path = _extract_capture_scope_path_from_message(
        message_text=message_text,
        mcp_scope=mcp_scope,
    )
    if scope_path and scope_path not in {"capture", "life"}:
        arguments["scope"] = scope_path
        if "/" in scope_path:
            arguments["project"] = scope_path.split("/")[-1]

    return arguments


def _capture_has_intake_write_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False

    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False

    if _capture_is_task_lookup_intent(normalized):
        return False

    if normalized.endswith("?") and len(normalized.split()) <= 10:
        return False

    explicit_mutation_markers = (
        "create a task",
        "create task",
        "add a task",
        "add task",
        "make a task",
        "open a task",
        "log a task",
        "track a task",
        "capture this",
        "note this",
        "log this",
        "write this down",
        "remember this",
    )
    if _is_likely_question_request(normalized) and not any(
        marker in normalized for marker in explicit_mutation_markers
    ):
        return False

    markers = (
        "capture",
        "note",
        "log this",
        "write this down",
        "remember this",
        "decision",
        "task",
        "todo",
        "transcript",
    )
    return any(marker in normalized for marker in markers)


def _build_capture_inbox_persist_tool_call(message_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(message_text, str):
        return None

    normalized_message = " ".join(message_text.strip().split())
    if not normalized_message:
        return None

    today_iso = datetime.now(timezone.utc).date().isoformat()
    filename_slug = _slugify_capture_fragment(normalized_message)
    path = f"capture/inbox/{today_iso}-{filename_slug}.md"
    return {
        "id": "auto_capture_inbox_persist",
        "name": "create_markdown",
        "arguments": {
            "path": path,
            "content": normalized_message,
        },
        "synthetic": True,
        "reason": "capture_inbox_persist",
    }


def _title_from_scope_slug(scope_slug: str) -> str:
    normalized = str(scope_slug or "").strip().replace("_", " ").replace("-", " ")
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return "Untitled"
    return " ".join(token.capitalize() for token in tokens)


def _infer_new_page_engine_archetype(
    *,
    page_kind: str,
    page_slug: str,
    label_text: str,
) -> str:
    lowered = str(label_text or "").strip().lower()

    if page_kind == "life":
        known_topic = CAPTURE_LIFE_TOPIC_ALIASES.get(page_slug, page_slug)
        topic_archetypes = {
            "finances": "finance",
            "fitness": "wellbeing",
            "relationships": "relationships",
            "career": "career",
            "whyfinder": "purpose",
        }
        by_topic = topic_archetypes.get(known_topic)
        if by_topic:
            return by_topic

        keyword_map = (
            ("habit", ("habit", "routine", "streak", "discipline")),
            ("wellbeing", ("wellness", "sleep", "energy", "nutrition", "health", "fitness")),
            ("relationships", ("family", "partner", "kids", "marriage", "relationship")),
            ("career", ("career", "job", "promotion", "resume", "interview", "work")),
            ("finance", ("budget", "debt", "savings", "cash flow", "spending", "money")),
            ("purpose", ("purpose", "values", "mission", "meaning", "identity")),
        )
        for archetype, keywords in keyword_map:
            if any(keyword in lowered for keyword in keywords):
                return archetype
        return "life_general"

    keyword_map = (
        ("research", ("research", "analy", "discovery", "investigat", "validate", "experiment")),
        ("operations", ("operations", "ops", "process", "workflow", "runbook", "automation", "sop")),
        ("content", ("content", "newsletter", "blog", "video", "podcast", "editorial", "course")),
        ("planning", ("roadmap", "plan", "strategy", "milestone", "program")),
        ("product_build", ("build", "feature", "mvp", "prototype", "launch", "implementation", "app")),
    )
    for archetype, keywords in keyword_map:
        if any(keyword in lowered for keyword in keywords):
            return archetype
    return "project_general"


def _extract_new_page_engine_request(
    *,
    message_text: Optional[str],
    conversation_type: str,
    mcp_scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(message_text, str):
        return None

    normalized = " ".join(message_text.strip().split())
    if not normalized:
        return None

    verb_pattern = r"(?:create|add|start|open|make|build|spin up|set up|setup)"
    typed_match = re.search(
        rf"\b{verb_pattern}\s+(?:a\s+)?(?:new\s+)?(life|project)\s+"
        r"(?:page|area|scope|workspace|folder)?(?:\s+(?:for|about|called|named))?\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    untyped_match = re.search(
        rf"\b{verb_pattern}\s+(?:a\s+)?new\s+(?:page|area|scope|workspace|folder)"
        r"(?:\s+(?:for|about|called|named))?\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if not typed_match and not untyped_match:
        return None

    explicit_kind = ""
    raw_name = ""
    if typed_match:
        explicit_kind = str(typed_match.group(1) or "").strip().lower()
        raw_name = str(typed_match.group(2) or "").strip()
    else:
        raw_name = str(untyped_match.group(1) or "").strip()

    if not raw_name:
        return None

    # Keep the first clause as the page label; preserve long-form description in summary.
    label = re.split(r"\b(?:with|where|so that|because)\b", raw_name, maxsplit=1, flags=re.IGNORECASE)[0]
    label = label.strip(" .,:;!?")
    page_slug = _slugify_capture_fragment(label, fallback="", max_length=64)
    if not page_slug:
        return None

    page_kind = explicit_kind
    if page_kind not in {"life", "project"}:
        normalized_scope = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
        if _extract_life_topic(conversation_type) or (
            isinstance(normalized_scope, str) and normalized_scope.startswith("life/")
        ):
            page_kind = "life"
        elif any(keyword in label.lower() for keyword in NEW_PAGE_ENGINE_LIFE_KEYWORDS):
            page_kind = "life"
        else:
            page_kind = "project"

    if page_kind == "life":
        canonical_topic = CAPTURE_LIFE_TOPIC_ALIASES.get(page_slug, page_slug)
        page_slug = _slugify_capture_fragment(canonical_topic, fallback=page_slug, max_length=64)
        page_path = f"life/{page_slug}"
    else:
        page_path = f"projects/active/{page_slug}"

    page_archetype = _infer_new_page_engine_archetype(
        page_kind=page_kind,
        page_slug=page_slug,
        label_text=raw_name or label,
    )

    return {
        "page_kind": page_kind,
        "page_archetype": page_archetype,
        "page_slug": page_slug,
        "page_path": page_path,
        "title": _title_from_scope_slug(page_slug),
        "topic_summary": normalized,
    }


def _is_new_page_engine_intent(message_text: Optional[str]) -> bool:
    parsed = _extract_new_page_engine_request(
        message_text=message_text,
        conversation_type="chat",
        mcp_scope={},
    )
    return bool(parsed)


def _build_new_page_engine_seed_questions(
    *,
    page_kind: str,
    page_slug: str,
    page_title: str,
    page_archetype: str,
) -> List[str]:
    if page_kind == "life":
        known_topic = CAPTURE_LIFE_TOPIC_ALIASES.get(page_slug, page_slug)
        seeded = LIFE_ONBOARDING_DEFAULT_QUESTIONS.get(known_topic)
        if seeded:
            return list(seeded)[:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        if page_archetype == "habit":
            return [
                f"What habit in {page_title} would create the biggest improvement this month?",
                "What is your minimum viable daily routine for this first phase?",
                "What usually breaks consistency, and how will you handle it this time?",
                "What metric or signal will prove the routine is working?",
                "What should happen if you miss 1-2 days in a row?",
                "What is the first seven-day commitment starting now?",
            ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        if page_archetype == "relationships":
            return [
                f"Which relationship outcomes matter most for {page_title} this quarter?",
                "What communication patterns should change first?",
                "What boundaries or agreements need to be explicit?",
                "What recurring check-in cadence is realistic this month?",
                "What conflict trigger should be handled proactively?",
                "What first action can you take this week?",
            ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        if page_archetype == "finance":
            return [
                f"What financial target matters most in {page_title} over the next 90 days?",
                "Which current spending/debt pattern needs the fastest correction?",
                "What budget guardrails will you enforce starting this week?",
                "What baseline numbers should be tracked every week?",
                "What risk would derail this plan if left unresolved?",
                "What is your first concrete money action in the next 48 hours?",
            ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        if page_archetype == "career":
            return [
                f"What career outcome does {page_title} target in the next quarter?",
                "Which skills or projects need to advance first?",
                "What blockers in role/scope/support must be addressed now?",
                "What network or mentorship actions should happen this month?",
                "What evidence would show trajectory is improving?",
                "What one action can be completed this week?",
            ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        if page_archetype == "purpose":
            return [
                f"What values should anchor decisions in {page_title}?",
                "Where are current priorities misaligned with those values?",
                "What commitments should be reduced or removed first?",
                "What weekly reflection ritual will keep alignment visible?",
                "What decision filters should be explicit going forward?",
                "What immediate action best reflects your intended direction?",
            ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
        return [
            f"What does success look like for {page_title} in the next 90 days?",
            f"What is working today in {page_title}, and what is not?",
            f"What constraints could block progress in {page_title} right now?",
            "What habit or routine would create the biggest momentum this month?",
            "What should be explicitly out of scope for this first phase?",
            "What is the next concrete action you can take this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]

    if page_archetype == "research":
        return [
            f"What decisions should research in {page_title} unlock first?",
            "What hypotheses do you need to validate early?",
            "What evidence sources and methods will be used?",
            "What are your acceptance criteria for confidence in findings?",
            "What timeline and checkpoints should guide this research pass?",
            "What first artifact will you publish this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
    if page_archetype == "operations":
        return [
            f"What operational outcome does {page_title} need to improve first?",
            "Which workflow bottleneck causes the most recurring drag?",
            "What standard process should be documented first?",
            "What automation opportunities can remove manual work quickly?",
            "What service levels or quality targets should be explicit?",
            "What first runbook action can be completed this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
    if page_archetype == "content":
        return [
            f"What audience and channel are primary for {page_title}?",
            "What publishing cadence is realistic for the next 30 days?",
            "What content pillars should anchor the first cycle?",
            "What production constraints need mitigation early?",
            "What quality bar defines a ready-to-ship piece?",
            "What first deliverable will you publish this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
    if page_archetype == "planning":
        return [
            f"What top milestones should {page_title} hit in the next 4-8 weeks?",
            "What scope cuts are required to keep execution realistic?",
            "Which dependencies need owners and dates immediately?",
            "What risk register should be maintained from day one?",
            "What review cadence keeps this plan current?",
            "What first planning artifact should be finalized this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]
    if page_archetype == "product_build":
        return [
            f"What user problem does {page_title} solve first?",
            "What is the smallest end-to-end outcome that proves value?",
            "What architecture or platform constraints matter now?",
            "What technical unknowns should be de-risked first?",
            "What release criteria define MVP readiness?",
            "What first build milestone should land this week?",
        ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]

    return [
        f"What problem is this {page_title} page solving, and for whom?",
        "What does a successful first version look like?",
        "What is in scope now, and what is explicitly out of scope?",
        "What existing assets, code, or context already exist?",
        "What are the top risks, unknowns, or blockers?",
        "What are the first milestones for the next 2-4 weeks?",
    ][:NEW_PAGE_ENGINE_MAX_SEED_QUESTIONS]


def _build_new_page_engine_followup_questions(
    *,
    page_kind: str,
    page_title: str,
    page_archetype: str,
) -> List[str]:
    if page_kind == "life":
        if page_archetype == "habit":
            return [
                f"What consistency did you maintain in {page_title} this week?",
                "Which trigger broke the routine, and what is the adjustment?",
                "Should difficulty or cadence be scaled up or down next?",
                "What context update should be logged for the next coaching pass?",
            ]
        if page_archetype == "relationships":
            return [
                f"What changed in communication outcomes for {page_title}?",
                "Which conversation or boundary still needs work?",
                "What action should happen before the next check-in?",
                "What context update would improve future guidance quality?",
            ]
        if page_archetype == "finance":
            return [
                f"What money target moved in {page_title} since kickoff?",
                "Which spending/debt pattern improved or regressed?",
                "Which budget guardrail needs correction this week?",
                "What additional context should be captured for planning?",
            ]
        if page_archetype == "career":
            return [
                f"What career milestone in {page_title} moved since kickoff?",
                "Which blocker needs escalation or reframing next?",
                "What should be re-prioritized for the next week?",
                "What context update should be saved for future planning?",
            ]
        if page_archetype == "purpose":
            return [
                f"What decision in {page_title} best reflected your values this week?",
                "Where did misalignment still show up?",
                "What commitment should be changed before the next check-in?",
                "What context update would sharpen future guidance?",
            ]
        return [
            f"What progress have you made in {page_title} since kickoff?",
            "Which blockers or routines need adjustment right now?",
            "Which goal or task should be re-prioritized for the next week?",
            "What context should be added so future coaching is more accurate?",
        ]

    if page_archetype == "research":
        return [
            f"What evidence was gathered for {page_title} since kickoff?",
            "Which hypothesis is now validated or invalidated?",
            "What research gap should be closed next?",
            "What decision should be made based on current findings?",
        ]
    if page_archetype == "operations":
        return [
            f"What process improvement in {page_title} moved this week?",
            "Which operational bottleneck still blocks throughput?",
            "What SOP/runbook section needs refinement next?",
            "What metric should be reviewed before the next check-in?",
        ]
    if page_archetype == "content":
        return [
            f"What publishing progress did {page_title} make since kickoff?",
            "Which content pillar performed best or worst?",
            "What production bottleneck should be addressed next?",
            "What should the next planned content deliverable be?",
        ]
    if page_archetype == "planning":
        return [
            f"Which milestone in {page_title} moved since kickoff?",
            "What dependency or risk changed most this week?",
            "What should be re-scoped before the next planning pass?",
            "What is the next concrete planning deliverable?",
        ]
    if page_archetype == "product_build":
        return [
            f"What build milestone in {page_title} moved forward?",
            "Which implementation risk became clearer?",
            "What should be cut, delayed, or accelerated next?",
            "What is the next concrete deliverable for this week?",
        ]

    return [
        f"What milestone in {page_title} moved forward since kickoff?",
        "Which implementation risk or unknown became clearer?",
        "What should be re-scoped before the next build pass?",
        "What is the next concrete deliverable for this week?",
    ]


def _build_new_page_engine_meta_payload(
    *,
    page_kind: str,
    page_archetype: str,
    page_slug: str,
    page_title: str,
    topic_summary: str,
    created_on: str,
    first_followup_due: str,
    seed_questions: List[str],
    followup_questions: List[str],
) -> Dict[str, Any]:
    return {
        "engine_version": "v1.1",
        "page_kind": page_kind,
        "page_archetype": page_archetype,
        "page_slug": page_slug,
        "page_title": page_title,
        "created_on_utc": created_on,
        "first_followup_due_utc": first_followup_due,
        "topic_summary": topic_summary,
        "seed_questions": seed_questions,
        "followup_questions": followup_questions,
        "status": {
            "interview_stage": "seeded",
            "question_total": len(seed_questions),
            "approved_answers": 0,
        },
    }


def _build_new_page_engine_archetype_file_pack(
    *,
    page_kind: str,
    page_archetype: str,
    page_title: str,
    created_on: str,
) -> List[Dict[str, str]]:
    life_archetype_files: Dict[str, Dict[str, str]] = {
        "habit": {
            "path": "habits.md",
            "content": (
                f"# {page_title} Habits\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Keystone Habits\n\n"
                "## Daily Tracking\n\n"
                "## Weekly Review\n"
            ),
        },
        "finance": {
            "path": "money-map.md",
            "content": (
                f"# {page_title} Money Map\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Baseline Numbers\n\n"
                "## Budget Guardrails\n\n"
                "## Debt / Savings Plan\n"
            ),
        },
        "relationships": {
            "path": "connection-plan.md",
            "content": (
                f"# {page_title} Connection Plan\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Priority Relationships\n\n"
                "## Communication Cadence\n\n"
                "## Agreements / Boundaries\n"
            ),
        },
        "career": {
            "path": "career-map.md",
            "content": (
                f"# {page_title} Career Map\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Target Roles / Outcomes\n\n"
                "## Skill Gaps\n\n"
                "## Opportunity Pipeline\n"
            ),
        },
        "purpose": {
            "path": "values-map.md",
            "content": (
                f"# {page_title} Values Map\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Core Values\n\n"
                "## Decision Filters\n\n"
                "## Alignment Check-ins\n"
            ),
        },
        "wellbeing": {
            "path": "routine-baseline.md",
            "content": (
                f"# {page_title} Routine Baseline\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Sleep / Recovery\n\n"
                "## Movement\n\n"
                "## Nutrition / Energy\n"
            ),
        },
    }
    project_archetype_files: Dict[str, Dict[str, str]] = {
        "research": {
            "path": "research.md",
            "content": (
                f"# {page_title} Research\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Questions\n\n"
                "## Findings\n\n"
                "## Decision Impact\n"
            ),
        },
        "operations": {
            "path": "runbook.md",
            "content": (
                f"# {page_title} Runbook\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Standard Flow\n\n"
                "## Alerts / Escalation\n\n"
                "## Recovery Steps\n"
            ),
        },
        "content": {
            "path": "content-plan.md",
            "content": (
                f"# {page_title} Content Plan\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Audience / Channels\n\n"
                "## Editorial Calendar\n\n"
                "## Production Checklist\n"
            ),
        },
        "planning": {
            "path": "roadmap.md",
            "content": (
                f"# {page_title} Roadmap\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Milestones\n\n"
                "## Dependencies\n\n"
                "## Risks\n"
            ),
        },
        "product_build": {
            "path": "architecture.md",
            "content": (
                f"# {page_title} Architecture\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## System Shape\n\n"
                "## Tradeoffs\n\n"
                "## Open Questions\n"
            ),
        },
    }

    if page_kind == "life":
        pack = life_archetype_files.get(page_archetype)
        return [pack] if isinstance(pack, dict) else []

    pack = project_archetype_files.get(page_archetype)
    return [pack] if isinstance(pack, dict) else []


def _build_new_page_engine_files(request_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    page_kind = str(request_payload.get("page_kind") or "project").strip().lower()
    page_archetype = str(request_payload.get("page_archetype") or "").strip().lower()
    page_title = str(request_payload.get("title") or "New Page").strip() or "New Page"
    page_slug = str(request_payload.get("page_slug") or "").strip()
    summary = str(request_payload.get("topic_summary") or "").strip()
    if not summary:
        summary = f"{page_title} kickoff context (to be expanded during interview)."
    created_on = datetime.now(timezone.utc).date().isoformat()
    first_followup_due = (
        datetime.now(timezone.utc) + timedelta(days=NEW_PAGE_ENGINE_FIRST_FOLLOWUP_DAYS)
    ).date().isoformat()
    questions = _build_new_page_engine_seed_questions(
        page_kind=page_kind,
        page_slug=page_slug,
        page_title=page_title,
        page_archetype=page_archetype,
    )
    followup_questions = _build_new_page_engine_followup_questions(
        page_kind=page_kind,
        page_title=page_title,
        page_archetype=page_archetype,
    )
    question_lines = "\n".join(
        f"{index}. {question}" for index, question in enumerate(questions, start=1)
    )
    followup_lines = "\n".join(
        f"{index}. {question}" for index, question in enumerate(followup_questions, start=1)
    )
    interview_areas_lines = "\n".join(
        [
            "1. Core Goal",
            "2. Scope Boundaries",
            "3. Current State",
            "4. Approach",
            "5. Risks and Blockers",
            "6. Priorities and Next Actions",
        ]
    )
    meta_payload = _build_new_page_engine_meta_payload(
        page_kind=page_kind,
        page_archetype=page_archetype,
        page_slug=page_slug,
        page_title=page_title,
        topic_summary=summary,
        created_on=created_on,
        first_followup_due=first_followup_due,
        seed_questions=questions,
        followup_questions=followup_questions,
    )
    meta_content = json.dumps(meta_payload, ensure_ascii=True, indent=2)
    archetype_files = _build_new_page_engine_archetype_file_pack(
        page_kind=page_kind,
        page_archetype=page_archetype,
        page_title=page_title,
        created_on=created_on,
    )

    interview_content = (
        f"# {page_title} Interview\n\n"
        f"Created On (UTC): {created_on}\n\n"
        f"First Follow-up Due (UTC Date): {first_followup_due}\n\n"
        "## Topic Summary\n"
        f"{summary}\n\n"
        "## Interview Areas\n"
        f"{interview_areas_lines}\n\n"
        "## Seed Questions\n"
        f"{question_lines}\n"
    )
    followup_content = (
        f"# {page_title} Interview Follow-up\n\n"
        f"Created On (UTC): {created_on}\n\n"
        f"Target Follow-up Date (UTC Date): {first_followup_due}\n\n"
        "## Context Snapshot\n"
        f"{summary}\n\n"
        "## First Follow-up Prompts\n"
        f"{followup_lines}\n"
    )

    if page_kind == "life":
        return [
            {
                "path": "AGENT.md",
                "content": (
                    f"# {page_title} Agent\n\n"
                    f"Use this folder for {page_title.lower()} planning and execution.\n"
                    f"Created by unified page engine on {created_on}.\n"
                ),
            },
            {"path": "interview.md", "content": interview_content},
            {"path": "interview-followup.md", "content": followup_content},
            {
                "path": "spec.md",
                "content": (
                    f"# {page_title} Spec\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    "## Current Reality\n"
                    f"{summary}\n\n"
                    "## Desired Outcomes\n\n"
                    "## Constraints\n\n"
                    "## Success Criteria\n"
                ),
            },
            {
                "path": "build-plan.md",
                "content": (
                    f"# {page_title} Build Plan\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    "## Phase 1\n\n"
                    "## Phase 2\n\n"
                    "## Risks\n\n"
                    "## Next Review\n"
                ),
            },
            {
                "path": "goals.md",
                "content": (
                    f"# {page_title} Goals\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    "## Current Goals\n\n"
                    "## 90-Day Targets\n"
                ),
            },
            {
                "path": "action-plan.md",
                "content": (
                    f"# {page_title} Action Plan\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    "## Immediate Actions\n\n"
                    "## Weekly Cadence\n"
                ),
            },
            {
                "path": "context.md",
                "content": (
                    f"# {page_title} Context\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    "## Baseline Notes\n"
                    f"{summary}\n\n"
                    "## Constraints and Dependencies\n"
                ),
            },
            {
                "path": "checkins.md",
                "content": (
                    f"# {page_title} Check-ins\n\n"
                    f"Created On (UTC): {created_on}\n\n"
                    f"- Planned first follow-up: {first_followup_due}\n"
                ),
            },
            *archetype_files,
            {"path": "_meta/interview-state.md", "content": meta_content},
        ]

    return [
        {
            "path": "AGENT.md",
            "content": (
                f"# {page_title} Agent\n\n"
                "Use this scope to plan, build, and track execution.\n"
                f"Created by unified page engine on {created_on}.\n"
            ),
        },
        {"path": "interview.md", "content": interview_content},
        {"path": "interview-followup.md", "content": followup_content},
        {
            "path": "spec.md",
            "content": (
                f"# {page_title} Spec\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Problem Statement\n"
                f"{summary}\n\n"
                "## Scope\n\n"
                "## Success Criteria\n\n"
                "## Risks\n"
            ),
        },
        {
            "path": "build-plan.md",
            "content": (
                f"# {page_title} Build Plan\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Milestone 1\n\n"
                "## Milestone 2\n\n"
                "## Dependencies\n\n"
                "## Next Steps\n"
            ),
        },
        {
            "path": "decisions.md",
            "content": (
                f"# {page_title} Decisions\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Decision Log\n"
                "- (to be populated)\n"
            ),
        },
        {
            "path": "ideas.md",
            "content": (
                f"# {page_title} Ideas\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Candidate Ideas\n"
                "- (to be populated)\n"
            ),
        },
        {
            "path": "status.md",
            "content": (
                f"# {page_title} Status\n\n"
                f"Created On (UTC): {created_on}\n\n"
                "## Current Phase\n"
                "- Discovery\n\n"
                "## Next Checkpoint\n"
                f"- {first_followup_due}\n"
            ),
        },
        *archetype_files,
        {"path": "_meta/interview-state.md", "content": meta_content},
    ]


def _build_new_page_engine_tool_call(
    *,
    latest_user_message: Optional[str],
    conversation_type: str,
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    request_payload = _extract_new_page_engine_request(
        message_text=latest_user_message,
        conversation_type=conversation_type,
        mcp_scope=mcp_scope,
    )
    if not request_payload:
        return None

    page_path = request_payload["page_path"]
    if "create_project" in available_tool_names:
        return {
            "id": "auto_new_page_engine_create",
            "name": "create_project",
            "arguments": {
                "path": page_path,
                "files": _build_new_page_engine_files(request_payload),
            },
            "synthetic": True,
            "reason": "new_page_engine_scaffold",
        }

    if "create_project_scaffold" in available_tool_names:
        return {
            "id": "auto_new_page_engine_create",
            "name": "create_project_scaffold",
            "arguments": {"path": page_path},
            "synthetic": True,
            "reason": "new_page_engine_scaffold",
        }
    return None


def _build_new_page_engine_followthrough_tool_calls(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> List[Dict[str, Any]]:
    normalized_type = _normalize_conversation_type(conversation_type)
    if _is_capture_intake_conversation(normalized_type) or _is_digest_conversation(normalized_type):
        return []

    attempted_statuses = {"success", "error", "blocked_context", "denied"}
    if _has_synthetic_reason_execution(
        executed_tool_calls,
        "new_page_engine_scaffold",
        statuses=attempted_statuses,
    ):
        return []

    new_page_call = _build_new_page_engine_tool_call(
        latest_user_message=latest_user_message,
        conversation_type=conversation_type,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if new_page_call:
        return [new_page_call]
    return []


def _maybe_override_capture_provider_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    tool_name: str,
    tool_arguments: Dict[str, Any],
    synthetic_reason: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
) -> tuple[str, Dict[str, Any], Optional[str]]:
    if synthetic_reason:
        return tool_name, tool_arguments, synthetic_reason

    normalized_type = _normalize_conversation_type(conversation_type)
    if normalized_type != "capture":
        return tool_name, tool_arguments, synthetic_reason
    if "create_markdown" not in available_tool_names:
        return tool_name, tool_arguments, synthetic_reason
    if _capture_is_task_lookup_intent(latest_user_message):
        return tool_name, tool_arguments, synthetic_reason
    if _is_capture_existing_task_mutation_intent(latest_user_message):
        return tool_name, tool_arguments, synthetic_reason
    if not _capture_has_intake_write_intent(latest_user_message):
        return tool_name, tool_arguments, synthetic_reason

    attempted_statuses = {"success", "error", "blocked_intent", "blocked_context", "denied"}
    has_inbox_persist = _has_synthetic_reason_execution(
        executed_tool_calls,
        "capture_inbox_persist",
        statuses=attempted_statuses,
    )
    has_capture_inbox_write = False
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in attempted_statuses:
            continue
        executed_name = str(item.get("name") or "").strip()
        if executed_name not in {"create_markdown", "write_markdown"}:
            continue
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        if _is_capture_inbox_path(args.get("path")):
            has_capture_inbox_write = True
            break

    if has_inbox_persist or has_capture_inbox_write:
        return tool_name, tool_arguments, synthetic_reason

    if tool_name in {"create_markdown", "write_markdown"} and _is_capture_inbox_path(
        tool_arguments.get("path")
    ):
        return tool_name, tool_arguments, synthetic_reason

    override_call = _build_capture_inbox_persist_tool_call(latest_user_message)
    if not isinstance(override_call, dict):
        return tool_name, tool_arguments, synthetic_reason

    override_name = str(override_call.get("name") or "").strip() or tool_name
    override_arguments = (
        override_call.get("arguments")
        if isinstance(override_call.get("arguments"), dict)
        else tool_arguments
    )
    override_reason = str(override_call.get("reason") or "").strip() or synthetic_reason
    return override_name, override_arguments, override_reason


def _maybe_override_new_page_provider_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    tool_name: str,
    tool_arguments: Dict[str, Any],
    synthetic_reason: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> tuple[str, Dict[str, Any], Optional[str]]:
    if synthetic_reason:
        return tool_name, tool_arguments, synthetic_reason

    normalized_type = _normalize_conversation_type(conversation_type)
    if _is_capture_intake_conversation(normalized_type) or _is_digest_conversation(normalized_type):
        return tool_name, tool_arguments, synthetic_reason

    if tool_name not in {"create_project", "create_project_scaffold", "project_exists"}:
        return tool_name, tool_arguments, synthetic_reason

    if not _is_new_page_engine_intent(latest_user_message):
        return tool_name, tool_arguments, synthetic_reason

    attempted_statuses = {"success", "error", "blocked_intent", "blocked_context", "denied"}
    if _has_synthetic_reason_execution(
        executed_tool_calls,
        "new_page_engine_scaffold",
        statuses=attempted_statuses,
    ):
        return tool_name, tool_arguments, synthetic_reason

    override_call = _build_new_page_engine_tool_call(
        latest_user_message=latest_user_message,
        conversation_type=conversation_type,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if not isinstance(override_call, dict):
        return tool_name, tool_arguments, synthetic_reason

    override_name = str(override_call.get("name") or "").strip() or tool_name
    override_arguments = (
        override_call.get("arguments")
        if isinstance(override_call.get("arguments"), dict)
        else tool_arguments
    )
    override_reason = str(override_call.get("reason") or "").strip() or synthetic_reason
    return override_name, override_arguments, override_reason


def _maybe_override_compound_edit_provider_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    tool_name: str,
    tool_arguments: Dict[str, Any],
    synthetic_reason: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> tuple[str, Dict[str, Any], Optional[str]]:
    if synthetic_reason:
        return tool_name, tool_arguments, synthetic_reason

    normalized_type = _normalize_conversation_type(conversation_type)
    if _is_capture_intake_conversation(normalized_type) or _is_digest_conversation(normalized_type):
        return tool_name, tool_arguments, synthetic_reason

    if tool_name != "edit_markdown" or "edit_markdown" not in available_tool_names:
        return tool_name, tool_arguments, synthetic_reason

    scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    operations = _extract_compound_edit_operations_from_message(
        latest_user_message=latest_user_message,
        scope_path=scope_path,
    )
    if len(operations) < 2:
        return tool_name, tool_arguments, synthetic_reason

    attempted_statuses = {"success", "error", "blocked_intent", "blocked_context", "denied"}
    attempted_paths: set[str] = set()
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != "edit_markdown":
            continue
        if str(item.get("status") or "").strip() not in attempted_statuses:
            continue
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        normalized_path = _normalize_scoped_tool_path(args.get("path"), scope_path)
        if normalized_path:
            attempted_paths.add(normalized_path)

    for operation in operations:
        path = _normalize_scoped_tool_path(operation.get("path"), scope_path)
        if not path or path in attempted_paths:
            continue
        override_arguments = copy.deepcopy(operation)
        override_arguments["path"] = path
        return "edit_markdown", override_arguments, COMPOUND_EDIT_SYNTHETIC_REASON

    return tool_name, tool_arguments, synthetic_reason


def _extract_compound_edit_operations_from_message(
    *,
    latest_user_message: Optional[str],
    scope_path: Optional[str],
) -> List[Dict[str, Any]]:
    if not isinstance(scope_path, str) or not scope_path.strip():
        return []
    if not isinstance(latest_user_message, str):
        return []

    normalized = " ".join(latest_user_message.strip().split())
    lowered = normalized.lower()
    if not lowered:
        return []

    edit_markers = ("edit", "update", "revise", "append", "change")
    if not any(marker in lowered for marker in edit_markers):
        return []

    alias_to_file = [
        ("build-plan.md", "build-plan.md"),
        ("build plan", "build-plan.md"),
        ("spec.md", "spec.md"),
        ("status.md", "status.md"),
        ("decisions.md", "decisions.md"),
        ("ideas.md", "ideas.md"),
    ]
    target_files: List[str] = []
    seen_files: set[str] = set()
    for alias, file_name in alias_to_file:
        if alias not in lowered:
            continue
        if file_name in seen_files:
            continue
        seen_files.add(file_name)
        target_files.append(file_name)
    if len(target_files) < 2:
        return []

    quoted_segments: List[str] = []
    for match in re.findall(r'"([^"\n]{1,260})"', normalized):
        cleaned = " ".join(str(match).strip().split())
        if cleaned:
            quoted_segments.append(cleaned)
    for match in re.findall(r"'([^'\n]{1,260})'", normalized):
        cleaned = " ".join(str(match).strip().split())
        if cleaned:
            quoted_segments.append(cleaned)

    fallback_line = _summarize_new_page_answer(normalized, max_chars=180)
    timestamp = datetime.now(timezone.utc).date().isoformat()
    heading_map = {
        "spec.md": "## Success Criteria",
        "build-plan.md": "## Next Steps",
        "status.md": "## Current Phase",
        "decisions.md": "## Decision Log",
        "ideas.md": "## Candidate Ideas",
    }

    operations: List[Dict[str, Any]] = []
    for index, file_name in enumerate(target_files[:COMPOUND_EDIT_MAX_OPERATIONS]):
        quoted_line = quoted_segments[index] if index < len(quoted_segments) else fallback_line
        content_line = f"- {timestamp}: {quoted_line}"
        operations.append(
            {
                "path": f"{scope_path}/{file_name}",
                "operation": {
                    "type": "insert_after",
                    "target": heading_map.get(file_name, ""),
                    "content": content_line,
                },
            }
        )
    return operations


def _build_compound_edit_followthrough_tool_calls(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> List[Dict[str, Any]]:
    normalized_type = _normalize_conversation_type(conversation_type)
    if _is_capture_intake_conversation(normalized_type) or _is_digest_conversation(normalized_type):
        return []
    if "edit_markdown" not in available_tool_names:
        return []

    scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))
    operations = _extract_compound_edit_operations_from_message(
        latest_user_message=latest_user_message,
        scope_path=scope_path,
    )
    if len(operations) < 2:
        return []

    attempted_statuses = {"success", "error", "blocked_intent", "blocked_context", "denied"}
    attempted_paths: set[str] = set()
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != "edit_markdown":
            continue
        if str(item.get("synthetic_reason") or "").strip() != COMPOUND_EDIT_SYNTHETIC_REASON:
            continue
        if str(item.get("status") or "").strip() not in attempted_statuses:
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        path = arguments.get("path")
        if isinstance(path, str) and path.strip():
            attempted_paths.add(path.strip())

    for operation in operations:
        path = str(operation.get("path") or "").strip()
        if not path or path in attempted_paths:
            continue
        return [
            {
                "id": f"auto_{COMPOUND_EDIT_SYNTHETIC_REASON}",
                "name": "edit_markdown",
                "arguments": operation,
                "synthetic": True,
                "reason": COMPOUND_EDIT_SYNTHETIC_REASON,
            }
        ]

    return []


def _extract_capture_new_page_path(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None

    normalized = " ".join(message_text.strip().split())
    lowered = normalized.lower()
    if not normalized or "page" not in lowered:
        return None

    if not any(marker in lowered for marker in ("new page", "create page", "create a page", "create new", "new project page", "new life page")):
        return None

    typed_match = re.search(
        r"\b(?:create|add|open|start)\s+(?:a\s+)?new\s+(life|project)\s+page(?:\s+for)?\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,80})",
        normalized,
        flags=re.IGNORECASE,
    )
    untyped_match = re.search(
        r"\b(?:create|add|open|start)\s+(?:a\s+)?new\s+([A-Za-z0-9][A-Za-z0-9 _-]{1,80})\s+page\b",
        normalized,
        flags=re.IGNORECASE,
    )

    page_type = ""
    page_name = ""
    if typed_match:
        page_type = typed_match.group(1).strip().lower()
        page_name = typed_match.group(2).strip()
    elif untyped_match:
        page_name = untyped_match.group(1).strip()

    if not page_name:
        return None

    page_slug = _slugify_capture_fragment(page_name, fallback="", max_length=64)
    if not page_slug or page_slug in LIFE_ONBOARDING_TOPICS:
        return None

    if page_type == "life":
        return f"life/{page_slug}"
    if page_type == "project":
        return f"projects/active/{page_slug}"
    return f"projects/active/{page_slug}"


def _build_capture_new_page_tool_call(
    *,
    latest_user_message: Optional[str],
    available_tool_names: set[str],
) -> Optional[Dict[str, Any]]:
    page_path = _extract_capture_new_page_path(latest_user_message)
    if not page_path:
        return None

    if "create_project" in available_tool_names:
        return {
            "id": "auto_capture_create_page",
            "name": "create_project",
            "arguments": {"path": page_path},
            "synthetic": True,
            "reason": "capture_new_page_proposal",
        }

    if "create_project_scaffold" in available_tool_names:
        return {
            "id": "auto_capture_create_page",
            "name": "create_project_scaffold",
            "arguments": {"path": page_path},
            "synthetic": True,
            "reason": "capture_new_page_proposal",
        }

    return None


def _build_capture_digest_rollup_tool_call(
    *,
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
) -> Optional[Dict[str, Any]]:
    if "rollup_digest_period" not in available_tool_names:
        return None

    if _has_successful_tool_execution(executed_tool_calls, "rollup_digest_period"):
        return None

    digest_write_sources = {
        "create_markdown",
        "write_markdown",
        "create_task",
        "update_task",
        "complete_task",
        "reopen_task",
        "ingest_transcript",
    }
    has_recent_write = False
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status != "success":
            continue
        name = str(item.get("name") or "").strip()
        if name in digest_write_sources:
            has_recent_write = True
            break

    if not has_recent_write:
        return None

    return {
        "id": "auto_capture_digest_rollup_week",
        "name": "rollup_digest_period",
        "arguments": {
            "period": "week",
            "target_date": datetime.now(timezone.utc).date().isoformat(),
        },
        "synthetic": True,
        "reason": "capture_digest_rollup_week",
    }


def _extract_owner_profile_update_text(message_text: Optional[str]) -> Optional[str]:
    if not isinstance(message_text, str):
        return None
    normalized = " ".join(message_text.strip().split())
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered.startswith("profile:"):
        candidate = normalized.split(":", 1)[1].strip(" .")
        return candidate if candidate else None

    patterns = (
        r"\b(?:update|add|append|save|set|remember|note)\b(?:\s+(?:to|for|in))?\s+(?:my\s+)?(?:owner\s+)?profile(?:\s+with)?\s*[:,-]?\s*(.+)$",
        r"\b(?:my\s+)?(?:owner\s+)?profile(?:\s+should\s+include|\s+includes?|\s+update)\s*[:,-]?\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip(" .")
        if candidate:
            return candidate

    return None


def _build_owner_profile_update_note_line(update_text: str) -> str:
    compact = " ".join(str(update_text or "").split())
    if compact.startswith("- "):
        return compact
    timestamp = datetime.now(timezone.utc).date().isoformat()
    return f"- {timestamp}: {compact}"


def _build_owner_profile_markdown_scaffold(note_line: str) -> str:
    normalized_note = _build_owner_profile_update_note_line(note_line)
    return (
        "# Profile\n\n"
        "## Identity\n\n"
        "## Goals\n\n"
        "## Constraints\n\n"
        "## Preferences\n\n"
        "## Last Updated\n"
        f"{normalized_note}\n"
    )


def _latest_owner_profile_update_note_line(
    executed_tool_calls: List[Dict[str, Any]],
) -> Optional[str]:
    for item in reversed(executed_tool_calls):
        if not isinstance(item, dict):
            continue
        if str(item.get("synthetic_reason") or "").strip() != OWNER_PROFILE_UPDATE_WRITE_REASON:
            continue
        arguments = item.get("arguments")
        if not isinstance(arguments, dict):
            continue
        operation = arguments.get("operation")
        if not isinstance(operation, dict):
            continue
        content = operation.get("content")
        if isinstance(content, str) and content.strip():
            return _build_owner_profile_update_note_line(content.strip())
    return None


def _has_owner_profile_write_missing_file_error(
    executed_tool_calls: List[Dict[str, Any]],
) -> bool:
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != "write_markdown":
            continue
        if str(item.get("synthetic_reason") or "").strip() != OWNER_PROFILE_UPDATE_WRITE_REASON:
            continue
        if str(item.get("status") or "").strip().lower() != "error":
            continue
        error_code = _extract_tool_execution_error_code(item)
        if error_code == "FILE_NOT_FOUND":
            return True
    return False


def _build_owner_profile_followthrough_tool_call(
    *,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
) -> Optional[Dict[str, Any]]:
    attempted_statuses = {"success", "error", "blocked_intent", "blocked_context", "denied"}
    write_attempted = _has_synthetic_reason_execution(
        executed_tool_calls,
        OWNER_PROFILE_UPDATE_WRITE_REASON,
        statuses=attempted_statuses,
    )
    create_attempted = _has_synthetic_reason_execution(
        executed_tool_calls,
        OWNER_PROFILE_UPDATE_CREATE_REASON,
        statuses=attempted_statuses,
    )
    if _has_successful_synthetic_reason(executed_tool_calls, OWNER_PROFILE_UPDATE_WRITE_REASON):
        return None
    if _has_successful_synthetic_reason(executed_tool_calls, OWNER_PROFILE_UPDATE_CREATE_REASON):
        return None

    if _has_owner_profile_write_missing_file_error(executed_tool_calls):
        if "create_markdown" not in available_tool_names or create_attempted:
            return None
        fallback_line = _latest_owner_profile_update_note_line(executed_tool_calls)
        if not fallback_line:
            extracted_text = _extract_owner_profile_update_text(latest_user_message)
            if not extracted_text:
                return None
            fallback_line = _build_owner_profile_update_note_line(extracted_text)
        return {
            "id": "auto_owner_profile_create",
            "name": "create_markdown",
            "arguments": {
                "path": OWNER_PROFILE_RELATIVE_PATH,
                "content": _build_owner_profile_markdown_scaffold(fallback_line),
            },
            "synthetic": True,
            "reason": OWNER_PROFILE_UPDATE_CREATE_REASON,
        }

    update_text = _extract_owner_profile_update_text(latest_user_message)
    if not update_text:
        return None
    note_line = _build_owner_profile_update_note_line(update_text)

    if "write_markdown" in available_tool_names and not write_attempted:
        return {
            "id": "auto_owner_profile_write",
            "name": "write_markdown",
            "arguments": {
                "path": OWNER_PROFILE_RELATIVE_PATH,
                "operation": {
                    "type": "insert_after",
                    "target": "## Last Updated",
                    "content": note_line,
                },
            },
            "synthetic": True,
            "reason": OWNER_PROFILE_UPDATE_WRITE_REASON,
        }

    if "create_markdown" in available_tool_names and not create_attempted:
        return {
            "id": "auto_owner_profile_create",
            "name": "create_markdown",
            "arguments": {
                "path": OWNER_PROFILE_RELATIVE_PATH,
                "content": _build_owner_profile_markdown_scaffold(note_line),
            },
            "synthetic": True,
            "reason": OWNER_PROFILE_UPDATE_CREATE_REASON,
        }

    return None


def _build_capture_followthrough_tool_calls(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not _is_capture_intake_conversation(conversation_type):
        return []

    owner_profile_followthrough = _build_owner_profile_followthrough_tool_call(
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
    )
    if owner_profile_followthrough:
        return [owner_profile_followthrough]

    attempted_statuses = {
        "success",
        "error",
        "blocked_intent",
        "blocked_context",
        "denied",
    }

    task_followthrough = _build_capture_task_followthrough_tool_call(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if task_followthrough:
        return [task_followthrough]

    if (
        "list_tasks" in available_tool_names
        and _capture_is_task_lookup_intent(latest_user_message)
        and not _has_tool_execution(
            executed_tool_calls,
            "list_tasks",
            statuses=attempted_statuses,
        )
    ):
        lookup_args: Dict[str, Any] = {"status": "open"}
        project_hint = _extract_capture_project_hint(mcp_scope)
        if project_hint:
            lookup_args["project"] = project_hint
        return [
            {
                "id": "auto_capture_lookup_list_tasks",
                "name": "list_tasks",
                "arguments": lookup_args,
                "synthetic": True,
                "reason": "capture_task_lookup_list",
            }
        ]

    if not _has_successful_synthetic_reason(executed_tool_calls, "capture_new_page_proposal"):
        create_page_call = _build_capture_new_page_tool_call(
            latest_user_message=latest_user_message,
            available_tool_names=available_tool_names,
        )
        if create_page_call:
            return [create_page_call]

    if (
        "create_markdown" in available_tool_names
        and _capture_has_intake_write_intent(latest_user_message)
        and not (
            _has_synthetic_reason_execution(
                executed_tool_calls,
                "capture_inbox_persist",
                statuses=attempted_statuses,
            )
            or _has_tool_execution(
                executed_tool_calls,
                "create_markdown",
                statuses=attempted_statuses,
            )
            or _has_tool_execution(
                executed_tool_calls,
                "write_markdown",
                statuses=attempted_statuses,
            )
        )
    ):
        note_call = _build_capture_inbox_persist_tool_call(latest_user_message)
        if note_call:
            return [note_call]

    fanout_call = _build_capture_scope_fanout_tool_call(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if fanout_call:
        return [fanout_call]

    if (
        "create_task" in available_tool_names
        and _capture_is_new_task_intent(latest_user_message)
        and not _has_successful_synthetic_reason(executed_tool_calls, "capture_new_task_create")
        and not _has_tool_execution(
            executed_tool_calls,
            "create_task",
            statuses=attempted_statuses,
        )
    ):
        return [
            {
                "id": "auto_capture_create_task",
                "name": "create_task",
                "arguments": _build_capture_create_task_arguments(
                    message_text=latest_user_message,
                    mcp_scope=mcp_scope,
                ),
                "synthetic": True,
                "reason": "capture_new_task_create",
            }
        ]

    digest_rollup_call = _build_capture_digest_rollup_tool_call(
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
    )
    if digest_rollup_call:
        return [digest_rollup_call]

    return []


def _extract_digest_snapshot_tasks_for_scoring(
    executed_tool_calls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    for item in reversed(executed_tool_calls):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != "digest_snapshot":
            continue
        if str(item.get("status") or "").strip().lower() != "success":
            continue
        result_payload = item.get("result")
        if not isinstance(result_payload, dict):
            continue
        direct_tasks = result_payload.get("tasks")
        if isinstance(direct_tasks, list):
            return [task for task in direct_tasks if isinstance(task, dict)]
        data_payload = result_payload.get("data")
        if isinstance(data_payload, dict) and isinstance(data_payload.get("tasks"), list):
            return [task for task in data_payload.get("tasks") if isinstance(task, dict)]
    return []


def _build_digest_schedule_followthrough_tool_call(
    *,
    conversation_type: str,
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    digest_schedule_config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not _is_digest_conversation(conversation_type) or _is_digest_reply_conversation(
        conversation_type
    ):
        return None
    if not isinstance(digest_schedule_config, dict):
        return None
    if not bool(digest_schedule_config.get("enabled")):
        return None
    if not bool(digest_schedule_config.get("due_now")):
        return None

    if "digest_snapshot" in available_tool_names and not _has_successful_tool_execution(
        executed_tool_calls, "digest_snapshot"
    ):
        return {
            "id": "auto_digest_schedule_snapshot",
            "name": "digest_snapshot",
            "arguments": {"include_completed": True, "completed_limit": 10, "activity_limit": 50},
            "synthetic": True,
            "reason": "digest_schedule_snapshot",
        }

    if "score_digest_tasks" in available_tool_names and not _has_successful_tool_execution(
        executed_tool_calls, "score_digest_tasks"
    ):
        tasks_for_scoring = _extract_digest_snapshot_tasks_for_scoring(executed_tool_calls)
        if tasks_for_scoring:
            return {
                "id": "auto_digest_schedule_score",
                "name": "score_digest_tasks",
                "arguments": {
                    "tasks": tasks_for_scoring,
                    "now": datetime.now(timezone.utc).isoformat(),
                },
                "synthetic": True,
                "reason": "digest_schedule_score",
            }

    if "rollup_digest_period" in available_tool_names and not _has_successful_tool_execution(
        executed_tool_calls, "rollup_digest_period"
    ):
        return {
            "id": "auto_digest_schedule_rollup_week",
            "name": "rollup_digest_period",
            "arguments": {
                "period": "week",
                "target_date": datetime.now(timezone.utc).date().isoformat(),
            },
            "synthetic": True,
            "reason": "digest_schedule_rollup_week",
        }
    return None


def _build_orchestration_followthrough_tool_calls(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
    digest_schedule_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    capture_followthrough = _build_capture_followthrough_tool_calls(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if capture_followthrough:
        return capture_followthrough

    new_page_followthrough = _build_new_page_engine_followthrough_tool_calls(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if new_page_followthrough:
        return new_page_followthrough

    compound_edit_followthrough = _build_compound_edit_followthrough_tool_calls(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if compound_edit_followthrough:
        return compound_edit_followthrough

    owner_profile_followthrough = _build_owner_profile_followthrough_tool_call(
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
    )
    if owner_profile_followthrough:
        return [owner_profile_followthrough]

    cross_pollination_followthrough = _build_cross_pollination_followthrough_tool_call(
        conversation_type=conversation_type,
        latest_user_message=latest_user_message,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        mcp_scope=mcp_scope,
    )
    if cross_pollination_followthrough:
        return [cross_pollination_followthrough]

    digest_followthrough = _build_digest_schedule_followthrough_tool_call(
        conversation_type=conversation_type,
        executed_tool_calls=executed_tool_calls,
        available_tool_names=available_tool_names,
        digest_schedule_config=digest_schedule_config,
    )
    if digest_followthrough:
        return [digest_followthrough]

    return []


def _extract_capture_tasks_from_tool_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    candidates: List[Any] = []
    direct_tasks = payload.get("tasks")
    if isinstance(direct_tasks, list):
        candidates.extend(direct_tasks)

    nested_data = payload.get("data")
    if isinstance(nested_data, dict):
        nested_tasks = nested_data.get("tasks")
        if isinstance(nested_tasks, list):
            candidates.extend(nested_tasks)

        nested_inner = nested_data.get("data")
        if isinstance(nested_inner, dict) and isinstance(nested_inner.get("tasks"), list):
            candidates.extend(nested_inner.get("tasks"))

    parsed: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        task_id = item.get("id")
        try:
            parsed_id = int(task_id)
        except (TypeError, ValueError):
            continue

        status = str(item.get("status") or "").strip().lower()
        if status in {"x", "completed", "done"}:
            continue

        parsed.append(
            {
                "id": parsed_id,
                "title": str(item.get("title") or "").strip(),
            }
        )
    return parsed


def _latest_capture_listed_tasks(executed_tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in reversed(executed_tool_calls):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != "list_tasks":
            continue
        if str(item.get("status") or "").strip().lower() != "success":
            continue
        tool_result = item.get("result")
        if not isinstance(tool_result, dict):
            continue
        parsed_tasks = _extract_capture_tasks_from_tool_payload(tool_result)
        if parsed_tasks:
            return parsed_tasks
    return []


def _extract_capture_task_id_hint(message_text: Optional[str]) -> Optional[int]:
    if not isinstance(message_text, str):
        return None
    match = re.search(r"\bt-(\d{1,6})\b", message_text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _extract_capture_match_tokens(message_text: Optional[str]) -> List[str]:
    if not isinstance(message_text, str):
        return []
    stop_words = {
        "task",
        "tasks",
        "todo",
        "complete",
        "completed",
        "mark",
        "done",
        "finish",
        "finished",
        "close",
        "closed",
        "resolve",
        "resolved",
        "edit",
        "update",
        "change",
        "modify",
        "adjust",
        "rename",
        "reschedule",
        "please",
        "that",
        "this",
        "with",
        "from",
        "into",
    }
    tokens = re.findall(r"[a-z0-9]+", message_text.lower())
    deduped: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 3:
            continue
        if token in stop_words:
            continue
        if token.startswith("t") and token[1:].isdigit():
            continue
        if token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _match_capture_task_id(
    *,
    message_text: Optional[str],
    tasks: List[Dict[str, Any]],
) -> Optional[int]:
    if not tasks:
        return None

    explicit_id = _extract_capture_task_id_hint(message_text)
    if explicit_id is not None:
        for task in tasks:
            if int(task.get("id")) == explicit_id:
                return explicit_id
        return None

    if isinstance(message_text, str):
        quoted_values = re.findall(r"['\"]([^'\"]{3,})['\"]", message_text)
        for quoted in quoted_values:
            lowered = quoted.strip().lower()
            if not lowered:
                continue
            matches = [
                task for task in tasks if lowered in str(task.get("title") or "").strip().lower()
            ]
            if len(matches) == 1:
                return int(matches[0]["id"])

    tokens = _extract_capture_match_tokens(message_text)
    scored: List[tuple[int, int]] = []
    for task in tasks:
        title = str(task.get("title") or "").strip().lower()
        if not title:
            continue
        score = sum(1 for token in tokens if token in title)
        if score > 0:
            scored.append((score, int(task["id"])))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_id = scored[0]
        if len(scored) == 1:
            return best_id
        second_score = scored[1][0]
        if best_score > second_score:
            return best_id
        return None

    if len(tasks) == 1:
        return int(tasks[0]["id"])
    return None


def _extract_capture_update_fields_from_message(message_text: Optional[str]) -> Dict[str, Any]:
    if not isinstance(message_text, str):
        return {}

    normalized = " ".join(message_text.strip().split())
    if not normalized:
        return {}

    fields: Dict[str, Any] = {}
    due_value = _normalize_capture_due_value(normalized)
    if due_value:
        fields["due"] = due_value

    lowered = normalized.lower()
    priority = _extract_capture_priority_from_text(normalized)
    if priority:
        fields["priority"] = priority

    owner = _extract_capture_owner_from_text(normalized)
    if owner:
        fields["owner"] = owner

    tags = _extract_capture_tags_from_text(normalized)
    if tags:
        fields["tags"] = tags

    scope_path = _extract_capture_scope_path_from_message(
        message_text=normalized,
        mcp_scope={},
    )
    if scope_path and scope_path not in {"capture", "life"}:
        fields["scope"] = scope_path
        if "/" in scope_path:
            fields["project"] = scope_path.split("/")[-1]

    title_match = re.search(
        r"\b(?:rename|change title to|update title to)\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if title_match:
        candidate_title = title_match.group(1).strip(" .")
        if candidate_title:
            fields["title"] = candidate_title

    if re.search(r"\bmark\b.{0,24}\b(open|reopen|active)\b", lowered):
        fields["status"] = "open"
    elif re.search(r"\bmark\b.{0,24}\b(done|complete|completed)\b", lowered):
        fields["status"] = "completed"

    return fields


def _build_capture_task_followthrough_tool_call(
    *,
    conversation_type: str,
    latest_user_message: Optional[str],
    executed_tool_calls: List[Dict[str, Any]],
    available_tool_names: set[str],
    mcp_scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _is_capture_intake_conversation(conversation_type):
        return None

    intent_kind = _capture_task_mutation_kind(latest_user_message)
    if intent_kind not in {"complete", "edit"}:
        return None

    if "list_tasks" not in available_tool_names:
        return None

    if not _has_successful_tool_execution(executed_tool_calls, "list_tasks"):
        lookup_args: Dict[str, Any] = {"status": "open"}
        project_hint = _extract_capture_project_hint(mcp_scope)
        if project_hint:
            lookup_args["project"] = project_hint
        return {
            "id": "auto_capture_list_tasks",
            "name": "list_tasks",
            "arguments": lookup_args,
            "synthetic": True,
            "reason": "capture_task_id_resolution",
        }

    listed_tasks = _latest_capture_listed_tasks(executed_tool_calls)
    if not listed_tasks:
        return None

    matched_id = _match_capture_task_id(
        message_text=latest_user_message,
        tasks=listed_tasks,
    )
    if matched_id is None:
        return None

    if intent_kind == "complete" and "complete_task" in available_tool_names:
        return {
            "id": "auto_capture_complete_task",
            "name": "complete_task",
            "arguments": {"id": matched_id},
            "synthetic": True,
            "reason": "capture_task_followthrough_complete",
        }

    if intent_kind == "edit" and "update_task" in available_tool_names:
        update_fields = _extract_capture_update_fields_from_message(latest_user_message)
        if not update_fields:
            return None
        return {
            "id": "auto_capture_update_task",
            "name": "update_task",
            "arguments": {"id": matched_id, "fields": update_fields},
            "synthetic": True,
            "reason": "capture_task_followthrough_update",
        }

    return None


def _extract_life_onboarding_state_from_message_metadata(
    metadata: Dict[str, Any],
    expected_topic: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None

    mcp_meta = metadata.get("mcp")
    if not isinstance(mcp_meta, dict):
        return None

    state = mcp_meta.get("life_onboarding_deterministic")
    if not isinstance(state, dict):
        return None

    topic = state.get("topic")
    if not isinstance(topic, str) or topic.strip().lower() != expected_topic.strip().lower():
        return None

    awaiting = state.get("awaiting")
    if not isinstance(awaiting, str) or not awaiting.strip():
        return None

    return dict(state)


def _extract_new_page_interview_state_from_message_metadata(
    metadata: Dict[str, Any],
    expected_scope_path: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None
    mcp_meta = metadata.get("mcp")
    if not isinstance(mcp_meta, dict):
        return None
    state = mcp_meta.get(NEW_PAGE_INTERVIEW_STATE_KEY)
    if not isinstance(state, dict):
        return None
    scope_path = str(state.get("scope_path") or "").strip()
    if not scope_path:
        return None
    if scope_path != expected_scope_path:
        return None
    awaiting = str(state.get("awaiting") or "").strip().lower()
    if not awaiting:
        return None
    return dict(state)


def _is_new_page_interview_kickoff_intent(message_text: Optional[str]) -> bool:
    if not isinstance(message_text, str):
        return False
    normalized = " ".join(message_text.strip().lower().split())
    if not normalized:
        return False
    start_markers = ("start", "begin", "resume", "continue")
    if not any(marker in normalized for marker in start_markers):
        return False
    return "interview" in normalized or "onboarding" in normalized


def _parse_new_page_interview_questions(meta_payload: Dict[str, Any]) -> List[str]:
    questions_raw = meta_payload.get("seed_questions")
    if not isinstance(questions_raw, list):
        return []
    parsed: List[str] = []
    for item in questions_raw:
        if isinstance(item, str) and item.strip():
            parsed.append(" ".join(item.strip().split()))
    deduped = _dedupe_questions(parsed)
    return deduped[:NEW_PAGE_INTERVIEW_MAX_QUESTIONS]


def _summarize_new_page_answer(answer_text: str, *, max_chars: int = 220) -> str:
    if not isinstance(answer_text, str):
        return ""
    normalized = " ".join(answer_text.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _build_new_page_interview_edit_operations(
    *,
    scope_path: str,
    page_kind: str,
    question_index: int,
    question_total: int,
    question_text: str,
    answer_text: str,
) -> List[Dict[str, Any]]:
    timestamp = datetime.now(timezone.utc).date().isoformat()
    answer_summary = _summarize_new_page_answer(answer_text)
    question_summary = _summarize_new_page_answer(question_text, max_chars=140)
    base_line = (
        f"- {timestamp} Q{question_index}/{question_total}: {answer_summary}"
        if answer_summary
        else f"- {timestamp} Q{question_index}/{question_total}: (captured)"
    )
    question_line = (
        f"- Prompt: {question_summary}" if question_summary else "- Prompt: (not provided)"
    )
    plan_line = (
        f"- {timestamp}: Interview update from Q{question_index} -> {answer_summary or 'captured'}"
    )
    status_line = (
        f"- {timestamp}: Interview checkpoint Q{question_index} captured and integrated."
    )

    spec_target = "## Desired Outcomes" if page_kind == "life" else "## Success Criteria"
    plan_target = "## Next Review" if page_kind == "life" else "## Next Steps"
    status_target = "## Current Phase"

    operations: List[Dict[str, Any]] = [
        {
            "path": f"{scope_path}/spec.md",
            "operation": {
                "type": "insert_after",
                "target": spec_target,
                "content": f"{base_line}\n{question_line}",
            },
        },
        {
            "path": f"{scope_path}/build-plan.md",
            "operation": {
                "type": "insert_after",
                "target": plan_target,
                "content": plan_line,
            },
        },
        {
            "path": f"{scope_path}/status.md",
            "operation": {
                "type": "insert_after",
                "target": status_target,
                "content": status_line,
            },
        },
    ]
    return operations


def _extract_preview_summary(preview_payload: Dict[str, Any], *, fallback: str) -> str:
    if not isinstance(preview_payload, dict):
        return fallback
    summary = preview_payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        if preview_payload.get("diffTruncated"):
            return f"{summary.strip()} ({APPROVAL_PREVIEW_TRUNCATED_NOTICE.lower()})"
        return summary.strip()
    if preview_payload.get("diffTruncated"):
        return APPROVAL_PREVIEW_TRUNCATED_NOTICE
    return fallback


async def _read_new_page_interview_seed(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    scope_path: str,
    mcp_plugin_slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    meta_path = f"{scope_path}/_meta/interview-state.md"
    parsed_meta: Dict[str, Any] = {}
    read_meta = await _execute_tool_with_resync_fallback(
        runtime_service=runtime_service,
        mcp_user_id=mcp_user_id,
        tool_name="read_markdown",
        arguments={"path": meta_path},
        plugin_slug_hint=mcp_plugin_slug,
    )
    if read_meta.get("ok"):
        meta_payload = read_meta.get("data")
        meta_content = _extract_markdown_content_from_tool_payload(
            meta_payload if isinstance(meta_payload, dict) else {}
        )
        if meta_content:
            try:
                parsed = json.loads(meta_content)
                if isinstance(parsed, dict):
                    parsed_meta = parsed
            except Exception:
                parsed_meta = {}

    questions = _parse_new_page_interview_questions(parsed_meta)
    if not questions:
        interview_path = f"{scope_path}/interview.md"
        read_interview = await _execute_tool_with_resync_fallback(
            runtime_service=runtime_service,
            mcp_user_id=mcp_user_id,
            tool_name="read_markdown",
            arguments={"path": interview_path},
            plugin_slug_hint=mcp_plugin_slug,
        )
        if read_interview.get("ok"):
            interview_payload = read_interview.get("data")
            interview_content = _extract_markdown_content_from_tool_payload(
                interview_payload if isinstance(interview_payload, dict) else {}
            )
            questions = _extract_seed_questions(interview_content)
    questions = _dedupe_questions(questions)[:NEW_PAGE_INTERVIEW_MAX_QUESTIONS]
    if not questions:
        return None

    page_title = str(parsed_meta.get("page_title") or "").strip()
    if not page_title:
        page_title = _title_from_scope_slug(scope_path.split("/")[-1] if "/" in scope_path else scope_path)
    page_kind = str(parsed_meta.get("page_kind") or "").strip().lower()
    if page_kind not in {"life", "project"}:
        page_kind = "life" if scope_path.startswith("life/") else "project"

    status_payload = parsed_meta.get("status") if isinstance(parsed_meta.get("status"), dict) else {}
    approved_answers = _as_int(status_payload.get("approved_answers"), 0, 0, 1000)
    question_total = len(questions)
    question_index = max(1, min(question_total, approved_answers + 1))
    first_followup_due = str(parsed_meta.get("first_followup_due_utc") or "").strip()
    if not first_followup_due:
        first_followup_due = (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat()

    if not isinstance(parsed_meta.get("status"), dict):
        parsed_meta["status"] = {}
    normalized_status = parsed_meta["status"]
    normalized_status["question_total"] = question_total
    normalized_status["approved_answers"] = approved_answers
    stage_value = str(normalized_status.get("interview_stage") or "").strip().lower()
    if not stage_value:
        stage_value = "seeded" if approved_answers <= 0 else "in_progress"
    normalized_status["interview_stage"] = stage_value
    parsed_meta["status"] = normalized_status
    parsed_meta["page_kind"] = page_kind
    parsed_meta["page_slug"] = scope_path.split("/")[-1] if "/" in scope_path else scope_path
    parsed_meta["page_title"] = page_title
    if not str(parsed_meta.get("created_on_utc") or "").strip():
        parsed_meta["created_on_utc"] = datetime.now(timezone.utc).date().isoformat()
    parsed_meta["first_followup_due_utc"] = first_followup_due
    if not isinstance(parsed_meta.get("seed_questions"), list) or not parsed_meta.get("seed_questions"):
        parsed_meta["seed_questions"] = list(questions)

    return {
        "scope_path": scope_path,
        "meta_path": meta_path,
        "meta_payload": parsed_meta,
        "page_title": page_title,
        "page_kind": page_kind,
        "questions": questions,
        "question_total": question_total,
        "question_index": question_index,
        "approved_answers": approved_answers,
        "first_followup_due_utc": first_followup_due,
    }


async def _execute_tool_with_resync_fallback(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    plugin_slug_hint: Optional[str],
) -> Dict[str, Any]:
    execution = await runtime_service.execute_tool_call(
        mcp_user_id,
        tool_name,
        arguments,
    )
    if execution.get("ok"):
        return execution

    error = execution.get("error") if isinstance(execution.get("error"), dict) else {}
    if error.get("code") != "TOOL_NOT_ALLOWED":
        return execution

    sync_filters: List[Optional[str]] = []
    hint = plugin_slug_hint.strip() if isinstance(plugin_slug_hint, str) else ""
    if hint:
        sync_filters.append(hint)
    sync_filters.append(None)

    seen: set[str] = set()
    for sync_filter in sync_filters:
        key = sync_filter or "__none__"
        if key in seen:
            continue
        seen.add(key)

        try:
            await runtime_service.sync_user_servers(
                mcp_user_id,
                plugin_slug_filter=sync_filter,
            )
        except Exception as sync_error:
            MODULE_LOGGER.warning(
                "life_onboarding_tool_resync_failed tool=%s plugin_filter=%s error=%s",
                tool_name,
                sync_filter,
                sync_error,
            )

        retry = await runtime_service.execute_tool_call(
            mcp_user_id,
            tool_name,
            arguments,
        )
        if retry.get("ok"):
            return retry

        retry_error = retry.get("error") if isinstance(retry.get("error"), dict) else {}
        if retry_error.get("code") != "TOOL_NOT_ALLOWED":
            return retry

        execution = retry

    return execution


async def _read_life_seed_questions(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    life_topic: str,
    mcp_plugin_slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    interview_path = f"life/{life_topic}/interview.md"
    read_result = await _execute_tool_with_resync_fallback(
        runtime_service=runtime_service,
        mcp_user_id=mcp_user_id,
        tool_name="read_markdown",
        arguments={"path": interview_path},
        plugin_slug_hint=mcp_plugin_slug,
    )
    if not read_result.get("ok"):
        MODULE_LOGGER.info(
            "life_onboarding_seed_read_failed topic=%s error=%s",
            life_topic,
            read_result.get("error"),
        )
        return None

    payload = read_result.get("data")
    content = _extract_markdown_content_from_tool_payload(payload if isinstance(payload, dict) else {})
    if not content:
        return None

    questions = _extract_seed_questions(content)
    if not questions:
        return None

    return {
        "source_path": interview_path,
        "questions": questions,
    }


async def _read_life_agent_questions(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    life_topic: str,
    mcp_plugin_slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    agent_path = f"life/{life_topic}/AGENT.md"
    read_result = await _execute_tool_with_resync_fallback(
        runtime_service=runtime_service,
        mcp_user_id=mcp_user_id,
        tool_name="read_markdown",
        arguments={"path": agent_path},
        plugin_slug_hint=mcp_plugin_slug,
    )
    if not read_result.get("ok"):
        MODULE_LOGGER.info(
            "life_onboarding_agent_read_failed topic=%s error=%s",
            life_topic,
            read_result.get("error"),
        )
        return None

    payload = read_result.get("data")
    content = _extract_markdown_content_from_tool_payload(
        payload if isinstance(payload, dict) else {}
    )
    if not content:
        return None

    questions = _build_life_questions_from_agent_focus(
        life_topic=life_topic,
        agent_markdown=content,
    )
    if not questions:
        return None
    return {
        "source_path": agent_path,
        "source_kind": "agent",
        "questions": questions,
    }


async def _build_life_onboarding_kickoff_fallback(
    *,
    life_topic: str,
    latest_user_message: Optional[str],
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    mcp_plugin_slug: Optional[str] = None,
    auto_start: bool = False,
    start_question_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    explicit_kickoff = _is_life_onboarding_kickoff_intent(latest_user_message)
    resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
    if auto_start and _is_life_onboarding_skip_intent(latest_user_message):
        return None

    if not (explicit_kickoff or resume_intent or auto_start):
        return None

    interview_data = await _read_life_agent_questions(
        runtime_service=runtime_service,
        mcp_user_id=mcp_user_id,
        life_topic=life_topic,
        mcp_plugin_slug=mcp_plugin_slug,
    )
    if not interview_data:
        interview_data = await _read_life_seed_questions(
            runtime_service=runtime_service,
            mcp_user_id=mcp_user_id,
            life_topic=life_topic,
            mcp_plugin_slug=mcp_plugin_slug,
        )

    if not interview_data:
        MODULE_LOGGER.info(
            "life_onboarding_kickoff_fallback_skipped topic=%s reason=seed_unavailable",
            life_topic,
        )
        return None

    questions = interview_data["questions"]
    question_total = len(questions)
    question_index = 1
    if isinstance(start_question_index, int):
        question_index = max(1, min(question_total, start_question_index))
    first_question = questions[question_index - 1]
    topic_title = LIFE_ONBOARDING_TOPICS.get(life_topic, life_topic.title())
    if explicit_kickoff or resume_intent:
        response_text = (
            f"Great. We are building your {topic_title} library context so BrainDrive can get to know you better. "
            "I will ask one question at a time and wait for your answer.\n\n"
            f"Question {question_index} of {question_total}: {first_question}"
        )
        kickoff_mode = "explicit"
    else:
        response_text = (
            f"Welcome to {topic_title}. We will start your onboarding interview now so this page can personalize to you.\n\n"
            f"Question {question_index} of {question_total}: {first_question}"
        )
        kickoff_mode = "auto_first_visit"

    return {
        "topic": life_topic,
        "topic_title": topic_title,
        "source_path": interview_data["source_path"],
        "source_kind": interview_data.get("source_kind", "seed"),
        "questions": questions,
        "question": first_question,
        "question_index": question_index,
        "question_total": question_total,
        "kickoff_mode": kickoff_mode,
        "response_text": response_text,
    }


def _normalize_approval_action(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"approve", "approved", "allow", "yes"}:
        return "approve"
    if normalized in {"reject", "rejected", "deny", "denied", "no"}:
        return "reject"
    return None


def _extract_mcp_approval_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract explicit approval-resume controls from params.
    Supports both flat keys and nested `mcp_approval` object.
    """
    nested_raw = params.pop("mcp_approval", None)
    nested = nested_raw if isinstance(nested_raw, dict) else {}

    action = _normalize_approval_action(
        nested.get("action", params.pop("mcp_approval_action", None))
    )
    request_id = nested.get("request_id", params.pop("mcp_approval_request_id", None))
    tool = nested.get("tool", params.pop("mcp_approval_tool", None))
    arguments = nested.get("arguments", params.pop("mcp_approval_arguments", None))
    if not isinstance(arguments, dict):
        arguments = None

    if not isinstance(request_id, str) or not request_id.strip():
        request_id = None
    if not isinstance(tool, str) or not tool.strip():
        tool = None

    return {
        "action": action,
        "request_id": request_id.strip() if isinstance(request_id, str) else None,
        "tool": tool.strip() if isinstance(tool, str) else None,
        "arguments": arguments,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_approval_request_payload(
    *,
    tool: str,
    safety_class: str,
    arguments: Dict[str, Any],
    summary: Optional[str] = None,
    request_id: Optional[str] = None,
    scope: Optional[Dict[str, Any]] = None,
    synthetic_reason: Optional[str] = None,
    origin_user_message: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "approval_request",
        "request_id": request_id or f"apr_{int(time.time() * 1000)}",
        "tool": tool,
        "safety_class": safety_class,
        "summary": summary or f"Approval required to run mutating tool '{tool}'.",
        "arguments": arguments,
        "status": "pending",
        "created_at": _utc_timestamp(),
    }
    if isinstance(scope, dict) and scope:
        payload["scope"] = {
            "mcp_scope_mode": scope.get("mcp_scope_mode"),
            "mcp_project_slug": scope.get("mcp_project_slug"),
            "mcp_project_name": scope.get("mcp_project_name"),
            "mcp_project_lifecycle": scope.get("mcp_project_lifecycle"),
            "mcp_project_source": scope.get("mcp_project_source"),
            "mcp_plugin_slug": scope.get("mcp_plugin_slug"),
        }
    if isinstance(synthetic_reason, str) and synthetic_reason.strip():
        payload["synthetic_reason"] = synthetic_reason.strip()
    if isinstance(origin_user_message, str) and origin_user_message.strip():
        payload["origin_user_message"] = origin_user_message.strip()
    return payload


def _extract_execution_data_payload(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    direct_data = value.get("data")
    if isinstance(direct_data, dict):
        nested_data = direct_data.get("data")
        if isinstance(nested_data, dict):
            return nested_data
        return direct_data
    return value


def _build_markdown_preview_payload(raw_payload: Any) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if isinstance(raw_payload, dict):
        candidates.append(raw_payload)
        data_payload = raw_payload.get("data")
        if isinstance(data_payload, dict):
            candidates.append(data_payload)
            nested_payload = data_payload.get("data")
            if isinstance(nested_payload, dict):
                candidates.append(nested_payload)

    selected: Optional[Dict[str, Any]] = None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if any(key in candidate for key in {"diff", "summary", "riskLevel"}):
            selected = candidate
            break
    if not isinstance(selected, dict):
        return None

    diff_text = selected.get("diff")
    if not isinstance(diff_text, str):
        diff_text = ""
    truncated = False
    if len(diff_text) > APPROVAL_PREVIEW_MAX_DIFF_CHARS:
        diff_text = diff_text[:APPROVAL_PREVIEW_MAX_DIFF_CHARS].rstrip() + "\n...[truncated]"
        truncated = True

    payload: Dict[str, Any] = {}
    summary = selected.get("summary")
    if isinstance(summary, str) and summary.strip():
        payload["summary"] = summary.strip()
    risk_level = selected.get("riskLevel")
    if isinstance(risk_level, str) and risk_level.strip():
        payload["riskLevel"] = risk_level.strip()
    if diff_text:
        payload["diff"] = diff_text
        payload["diffTruncated"] = truncated
    if truncated:
        payload["previewNotice"] = APPROVAL_PREVIEW_TRUNCATED_NOTICE
    if not payload:
        return None
    payload["previewTool"] = "preview_markdown_change"
    return payload


async def _build_mutating_approval_context(
    *,
    runtime_service: MCPRegistryService,
    mcp_user_id: str,
    tool_name: str,
    tool_arguments: Dict[str, Any],
) -> Dict[str, Any]:
    context: Dict[str, Any] = {}

    path_value = tool_arguments.get("path")
    if (
        tool_name in {"write_markdown", "edit_markdown"}
        and isinstance(path_value, str)
        and path_value.strip()
        and isinstance(tool_arguments.get("operation"), dict)
    ):
        preview_result = await runtime_service.execute_tool_call(
            mcp_user_id,
            "preview_markdown_change",
            {
                "path": path_value,
                "operation": tool_arguments["operation"],
            },
        )
        if preview_result.get("ok"):
            preview_payload = _build_markdown_preview_payload(preview_result.get("data"))
            if isinstance(preview_payload, dict):
                context["preview"] = preview_payload

    preview_summary = (
        context.get("preview", {}).get("summary")
        if isinstance(context.get("preview"), dict)
        else None
    )
    if isinstance(preview_summary, str) and preview_summary.strip():
        context["summary"] = (
            f"Approval required to run mutating tool '{tool_name}'. "
            f"Preview: {preview_summary.strip()}"
        )
    elif isinstance(path_value, str) and path_value.strip():
        context["summary"] = (
            f"Approval required to run mutating tool '{tool_name}' on '{path_value.strip()}'."
        )
    return context


def _extract_commit_sha_from_tool_result(result_payload: Any) -> Optional[str]:
    if not isinstance(result_payload, dict):
        return None
    for key in ("commitSha", "commit_sha"):
        value = result_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested_data = result_payload.get("data")
    if isinstance(nested_data, dict):
        return _extract_commit_sha_from_tool_result(nested_data)
    return None


def _build_approval_execution_success_message(
    *,
    executed_tool_calls: List[Dict[str, Any]],
    approval_resolution_payload: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not isinstance(approval_resolution_payload, dict):
        return None
    if str(approval_resolution_payload.get("status") or "").strip().lower() != "approved":
        return None

    tool_name = str(approval_resolution_payload.get("tool") or "").strip()
    if not tool_name:
        return None

    successful_calls: List[Dict[str, Any]] = []
    for item in executed_tool_calls:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != tool_name:
            continue
        if str(item.get("status") or "").strip().lower() != "success":
            continue
        successful_calls.append(item)

    if len(successful_calls) > 1:
        paths: List[str] = []
        for item in successful_calls:
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            path_value = arguments.get("path")
            if isinstance(path_value, str) and path_value.strip() and path_value.strip() not in paths:
                paths.append(path_value.strip())
        message = (
            f"Approved and executed `{tool_name}` successfully across "
            f"{len(successful_calls)} operations."
        )
        if paths:
            preview_paths = ", ".join(f"`{path}`" for path in paths[:3])
            message += f" Paths: {preview_paths}."
        return message

    for item in reversed(executed_tool_calls):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() != tool_name:
            continue
        if str(item.get("status") or "").strip().lower() != "success":
            continue

        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        path_value = arguments.get("path")
        task_id = arguments.get("id")
        commit_sha = _extract_commit_sha_from_tool_result(item.get("result"))

        message = f"Approved and executed `{tool_name}` successfully."
        if isinstance(path_value, str) and path_value.strip():
            message += f" Path: `{path_value.strip()}`."
        elif isinstance(task_id, int):
            message += f" Task ID: `{task_id}`."
        if isinstance(commit_sha, str) and commit_sha:
            message += f" Commit: `{commit_sha}`."
        return message

    return f"Approved and executed `{tool_name}` successfully."


# Helper function to get provider instance from request
async def get_provider_instance_from_request(request, db):
    """Helper function to get provider instance from request."""
    # Use current user if not specified
    user_id = request.user_id or "current"
    
    # Normalize user_id by removing hyphens if present
    if user_id != "current":
        user_id = user_id.replace("-", "")
    
    logger = logging.getLogger(__name__)
    print(f"🚀 PROVIDER REQUEST RECEIVED")
    print(f"📊 Provider: {request.provider}")
    print(f"📊 Settings ID: {request.settings_id}")
    print(f"📊 Server ID: {request.server_id}")
    print(f"📊 Model: {getattr(request, 'model', 'N/A')}")
    print(f"📊 User ID: {user_id}")
    logger.info(f"Getting provider instance for: settings_id={request.settings_id}, user_id={user_id}")
    logger.info(f"Original user_id from request: {request.user_id}")
    
    # Helper to map provider to API key env var
    def _get_env_api_key(provider_name: str) -> Optional[str]:
        env_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_var = env_map.get(provider_name.lower())
        if env_var:
            return os.getenv(env_var)
        return None

    api_key_providers = {"openrouter", "openai", "claude", "groq"}

    try:
        # Get settings for the specified user
        MODULE_LOGGER.info(f"Fetching settings with definition_id={request.settings_id}, user_id={user_id}")
        settings = await SettingInstance.get_all_parameterized(
            db,
            definition_id=request.settings_id,
            scope=SettingScope.USER.value,
            user_id=user_id
        )
        # Fallback to legacy direct SQL if ORM returns none (compat with legacy enum storage)
        if not settings or len(settings) == 0:
            MODULE_LOGGER.info("ORM returned no settings; falling back to direct SQL query for settings")
            settings = await SettingInstance.get_all(
                db,
                definition_id=request.settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )
        
        MODULE_LOGGER.info(f"Found {len(settings)} settings for user_id={user_id}")
        
        if not settings or len(settings) == 0:
            logger.error(f"No settings found for definition_id={request.settings_id}, user_id={user_id}")
            # For testing purposes, use a default configuration if settings are not found
            if request.settings_id == "ollama_settings" and request.provider == "ollama":
                logger.warning(f"Using default Ollama configuration for testing. settings_id={request.settings_id}, user_id={user_id}")
                server = {
                    "id": request.server_id,
                    "serverName": "Test Ollama Server",
                    "serverAddress": "http://localhost:11434",
                    "apiKey": ""
                }
                config = {
                    "server_url": server["serverAddress"],
                    "api_key": server["apiKey"],
                    "server_name": server["serverName"]
                }
                
                # Get provider instance
                MODULE_LOGGER.info(f"Getting provider instance for: {request.provider}, {request.server_id}")
                provider_instance = await provider_registry.get_provider(
                    request.provider,
                    request.server_id,
                    config
                )
                
                MODULE_LOGGER.info(f"Got provider instance: {provider_instance.provider_name}")
                
                return provider_instance
            elif request.provider in api_key_providers:
                # Try environment fallback for API-key providers
                env_key = _get_env_api_key(request.provider)
                if env_key:
                    logger.warning(f"Using environment API key for provider '{request.provider}' due to missing settings")
                    if request.provider == "openai":
                        config = {"api_key": env_key, "server_url": "https://api.openai.com/v1", "server_name": "OpenAI API"}
                    elif request.provider == "openrouter":
                        config = {"api_key": env_key, "server_url": "https://openrouter.ai/api/v1", "server_name": "OpenRouter API"}
                    elif request.provider == "claude":
                        config = {"api_key": env_key, "server_url": "https://api.anthropic.com", "server_name": "Claude API"}
                    elif request.provider == "groq":
                        config = {"api_key": env_key, "server_url": "https://api.groq.com", "server_name": "Groq API"}
                    else:
                        config = {"api_key": env_key}

                    provider_instance = await provider_registry.get_provider(
                        request.provider,
                        request.server_id,
                        config
                    )
                    MODULE_LOGGER.info(f"Got provider instance with env key: {provider_instance.provider_name}")
                    return provider_instance

                # No settings and no env fallback
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{request.provider.capitalize()} API key is not configured. "
                        f"Please add your API key in Settings (definition_id={request.settings_id}) "
                        f"or set the environment variable {('OPENROUTER_API_KEY' if request.provider=='openrouter' else 'OPENAI_API_KEY' if request.provider=='openai' else 'ANTHROPIC_API_KEY' if request.provider=='claude' else 'GROQ_API_KEY')} and restart."
                    ),
                )
            else:
                # For other providers, raise an error
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider settings not found for settings_id={request.settings_id}, user_id={user_id}. "
                           f"Please ensure the settings are properly configured."
                )
        
        # Use the first setting found
        setting = settings[0]
        logger.debug(f"Using setting with ID: {setting['id'] if isinstance(setting, dict) else setting.id}")
        
        # Extract configuration from settings value using robust parsing
        setting_value = setting['value'] if isinstance(setting, dict) else setting.value
        setting_id = setting['id'] if isinstance(setting, dict) else setting.id
        
        # Use our robust JSON parsing utility that handles encryption issues
        try:
            # If the value appears encrypted (when using direct SQL dict path), try decrypting first
            if isinstance(setting_value, str):
                try:
                    from app.core.encryption import encryption_service as _enc, EncryptionError as _EncErr
                    if _enc.is_encrypted_value(setting_value):
                        MODULE_LOGGER.info("Attempting to decrypt settings value via encryption_service")
                        decrypted = _enc.decrypt_field('settings_instances', 'value', setting_value)
                        setting_value = decrypted
                except Exception as dec_err:
                    logger.debug(f"Settings decryption attempt failed or not needed: {dec_err}")

            value_dict = safe_encrypted_json_parse(
                setting_value,
                context=f"settings_id={request.settings_id}, user_id={user_id}",
                setting_id=setting_id,
                definition_id=request.settings_id
            )
            
            # Ensure we have a dictionary
            if not isinstance(value_dict, dict):
                logger.error(f"Parsed value is not a dictionary: {type(value_dict)}")
                # For Ollama settings, provide a default structure
                if 'ollama' in request.settings_id.lower():
                    logger.warning("Creating default Ollama settings structure")
                    value_dict = create_default_ollama_settings()
                elif request.provider in api_key_providers:
                    # Try environment fallback for API-key providers
                    env_key = _get_env_api_key(request.provider)
                    if env_key:
                        logger.warning(f"Using environment API key for provider '{request.provider}' due to non-dict settings value")
                        value_dict = {"api_key": env_key}
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"{request.provider.capitalize()} API key could not be read from settings. "
                                f"Please re-enter your key in Settings (definition_id={request.settings_id}) "
                                f"or set the appropriate environment variable."
                            )
                        )
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Settings value must be a dictionary, got {type(value_dict)}. "
                               f"Setting ID: {setting_id}"
                    )
            
            logger.debug(f"Successfully parsed settings value for {request.settings_id}")
            
        except ValueError as e:
            logger.error(f"Failed to parse encrypted settings: {e}")
            # For Ollama settings, provide helpful error message and fallback
            if 'ollama' in request.settings_id.lower():
                MODULE_LOGGER.info("Ollama settings parsing failed, using fallback configuration")
                value_dict = create_default_ollama_settings()
            elif request.provider in api_key_providers:
                # Try environment fallback for API-key providers
                env_key = _get_env_api_key(request.provider)
                if env_key:
                    logger.warning(f"Using environment API key for provider '{request.provider}' due to settings parse failure")
                    value_dict = {"api_key": env_key}
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{request.provider.capitalize()} API key could not be decrypted or parsed. "
                            f"Please re-enter your key in Settings (definition_id={request.settings_id}) "
                            f"or set the appropriate environment variable and restart."
                        )
                    )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=str(e)
                )
        except Exception as e:
            logger.error(f"Unexpected error parsing settings: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error parsing settings: {str(e)}. Setting ID: {setting_id}"
            )
        
        # Add specific validation for Ollama settings
        if 'ollama' in request.settings_id.lower():
            MODULE_LOGGER.info("Validating Ollama settings format")
            if not validate_ollama_settings_format(value_dict):
                logger.warning("Ollama settings format validation failed, using default structure")
                value_dict = create_default_ollama_settings()
            else:
                MODULE_LOGGER.info("Ollama settings format validation passed")
        
        # Handle different provider configurations
        if request.provider == "openai":
            # OpenAI uses simple api_key structure
            MODULE_LOGGER.info("Processing OpenAI provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openai") or ""
            if not api_key:
                logger.error("OpenAI API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="OpenAI API key is required. Please configure your OpenAI API key in settings."
                )
            configured_base_url = value_dict.get("base_url") or value_dict.get("baseUrl")
            if isinstance(configured_base_url, str) and configured_base_url.strip():
                openai_base_url = configured_base_url.strip()
            else:
                openai_base_url = "https://api.openai.com/v1"

            # For OpenAI, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "base_url": openai_base_url,
                "server_url": openai_base_url,  # Keep server_url metadata aligned for diagnostics
                "server_name": "OpenAI API",
            }
            MODULE_LOGGER.info(f"Created OpenAI config with API key")
        elif request.provider == "openrouter":
            # OpenRouter uses simple api_key structure (similar to OpenAI)
            MODULE_LOGGER.info("Processing OpenRouter provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openrouter") or ""
            if not api_key:
                logger.error("OpenRouter API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="OpenRouter API key is required. Please configure your OpenRouter API key in settings."
                )
            
            # For OpenRouter, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://openrouter.ai/api/v1",  # OpenRouter API URL
                "server_name": "OpenRouter API"
            }
            MODULE_LOGGER.info(f"Created OpenRouter config with API key")
        elif request.provider == "claude":
            # Claude uses simple api_key structure (similar to OpenAI)
            MODULE_LOGGER.info("Processing Claude provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("claude") or ""
            if not api_key:
                logger.error("Claude API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Claude API key is required. Please configure your Claude API key in settings."
                )
            
            # For Claude, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.anthropic.com",  # Claude API URL
                "server_name": "Claude API"
            }
            MODULE_LOGGER.info(f"Created Claude config with API key")
        elif request.provider == "groq":
            # Groq uses simple api_key structure (similar to OpenAI)
            MODULE_LOGGER.info("Processing Groq provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("groq") or ""
            if not api_key:
                logger.error("Groq API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Groq API key is required. Please configure your Groq API key in settings."
                )
            
            # For Groq, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.groq.com",  # Groq API URL
                "server_name": "Groq API"
            }
            MODULE_LOGGER.info(f"Created Groq config with API key")
        else:
            # Other providers (like Ollama) use servers array
            logger.debug("Processing server-based provider configuration")
            servers = value_dict.get("servers", [])
            logger.debug(f"Found {len(servers)} servers in settings")
            
            logger.debug("Processing server-based provider configuration")
            servers = value_dict.get("servers", [])
            logger.debug(f"Found {len(servers)} servers in settings")
            
            # Find the specific server by ID
            logger.debug(f"Looking for server with ID: '{request.server_id}'")
            server = next((s for s in servers if s.get("id") == request.server_id), None)
            
            if not server:
                # Provide detailed error message about available servers
                if servers:
                    available_servers = [f"{s.get('serverName', 'Unknown')} (ID: {s.get('id', 'Unknown')})" for s in servers]
                    available_list = ", ".join(available_servers)
                    logger.error(f"❌ Server with ID '{request.server_id}' not found")
                    logger.error(f"📋 Available servers: {available_list}")
                    raise HTTPException(
                        status_code=404,
                        detail=f"Ollama server '{request.server_id}' not found. "
                               f"Available servers: {available_list}. "
                               f"Please select a valid server from your Ollama settings."
                    )
                else:
                    logger.error(f"❌ No Ollama servers configured")
                    raise HTTPException(
                        status_code=404,
                        detail="No Ollama servers are configured. "
                               "Please add at least one Ollama server in your settings before using this provider."
                    )
            
            logger.debug(f"Found server: {server.get('serverName')} (ID: {server.get('id')})")
            
            # Create provider configuration from server details
            server_url = server.get("serverAddress")
            logger.debug(f"Server URL from settings: '{server_url}'")
            
            if not server_url:
                logger.error(f"Server URL is missing for server: {server.get('id')}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Server URL is missing for server: {server.get('id')}. "
                           f"Please update your server configuration with a valid URL."
                )
                
            config = {
                "server_url": server_url,
                "api_key": server.get("apiKey", ""),
                "server_name": server.get("serverName", "Unknown Server")
            }
            
            logger.debug(f"Created server config: {config.get('server_name')} -> {config.get('server_url')}")
        
        # Get provider instance
        logger.debug(f"Getting provider instance for: {request.provider}, {request.server_id}")
        provider_instance = await provider_registry.get_provider(
            request.provider,
            request.server_id,
            config
        )
        
        MODULE_LOGGER.info(f"Got provider instance: {provider_instance.provider_name}")
        
        return provider_instance
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error in get_provider_instance_from_request: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error getting provider instance: {str(e)}. "
                   f"Please check your configuration and try again."
        )


@router.get("/providers")
async def get_providers():
    """Get list of available AI providers."""
    return {
        "providers": provider_registry.get_available_providers()
    }


@router.get("/catalog")
async def get_provider_catalog(
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """
    Return provider metadata for UI selection (provider -> settings_id, server strategy, etc)
    plus per-user configured/enabled signals (no secrets).
    """
    # Resolve user_id from authentication if "current" is specified
    if user_id == "current":
        if not auth:
            raise HTTPException(status_code=401, detail="Authentication required")
        user_id = str(auth.user_id)

    # Normalize user_id by removing hyphens if present
    user_id = user_id.replace("-", "")

    provider_meta: Dict[str, Dict[str, Any]] = {
        "ollama": {
            "settings_id": "ollama_servers_settings",
            "server_strategy": "settings_servers",
            "default_server_id": None,
        },
        "openai": {
            "settings_id": "openai_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "openai_default_server",
        },
        "openrouter": {
            "settings_id": "openrouter_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "openrouter_default_server",
        },
        "claude": {
            "settings_id": "claude_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "claude_default_server",
        },
        "groq": {
            "settings_id": "groq_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "groq_default_server",
        },
    }

    def _get_env_api_key(provider_name: str) -> Optional[str]:
        env_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_var = env_map.get(provider_name.lower())
        if env_var:
            return os.getenv(env_var)
        return None

    async def _load_settings_value(settings_id: str) -> Optional[Dict[str, Any]]:
        if not settings_id:
            return None
        settings = await SettingInstance.get_all_parameterized(
            db,
            definition_id=settings_id,
            scope=SettingScope.USER.value,
            user_id=user_id
        )
        if not settings or len(settings) == 0:
            settings = await SettingInstance.get_all(
                db,
                definition_id=settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )
        if not settings or len(settings) == 0:
            return None

        setting = settings[0]
        setting_value = setting['value'] if isinstance(setting, dict) else getattr(setting, 'value', None)
        setting_instance_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')

        if isinstance(setting_value, dict):
            return setting_value

        raw_value = setting_value
        try:
            if isinstance(raw_value, str) and encryption_service.is_encrypted_value(raw_value):
                raw_value = encryption_service.decrypt_field('settings_instances', 'value', raw_value)

            value_dict = safe_encrypted_json_parse(
                raw_value,
                context=f"catalog settings_id={settings_id}, user_id={user_id}",
                setting_id=setting_instance_id,
                definition_id=settings_id,
            )

            if isinstance(value_dict, str):
                # Some legacy rows stored the API key directly as a string
                value_dict = {"api_key": value_dict}

            return value_dict if isinstance(value_dict, dict) else None
        except Exception:
            return None

    providers: List[Dict[str, Any]] = []
    for provider in provider_registry.get_available_providers():
        meta = provider_meta.get(provider, {})
        settings_id = meta.get("settings_id")
        server_strategy = meta.get("server_strategy", "unknown")
        default_server_id = meta.get("default_server_id")

        settings_value = await _load_settings_value(settings_id) if settings_id else None

        enabled = True
        if isinstance(settings_value, dict) and isinstance(settings_value.get("enabled"), bool):
            enabled = bool(settings_value.get("enabled"))

        configured = False
        configured_via = None
        server_count = 0

        if provider == "ollama":
            servers = []
            if isinstance(settings_value, dict):
                servers = settings_value.get("servers", []) or []
            servers = servers if isinstance(servers, list) else []
            server_count = len(servers)
            configured = any(bool(s.get("serverAddress")) for s in servers if isinstance(s, dict))
            configured_via = "settings" if configured else None
        else:
            api_key = None
            if isinstance(settings_value, dict):
                api_key = settings_value.get("api_key") or settings_value.get("apiKey")
            if isinstance(api_key, str) and api_key.strip():
                configured = True
                configured_via = "settings"
            else:
                env_key = _get_env_api_key(provider)
                if isinstance(env_key, str) and env_key.strip():
                    configured = True
                    configured_via = "env"

        providers.append({
            "id": provider,
            "label": provider.replace("_", " ").title(),
            "settings_id": settings_id,
            "server_strategy": server_strategy,
            "default_server_id": default_server_id,
            "configured": configured,
            "configured_via": configured_via,
            "enabled": enabled,
            "server_count": server_count,
        })

    return {
        "user_id": user_id,
        "providers": providers,
    }


@router.post("/validate")
async def validate_provider(request: ValidationRequest):
    """Validate connection to a provider."""
    try:
        provider_name = request.provider
        if provider_name not in provider_registry.get_available_providers():
            raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
        
        # Create a temporary provider instance for validation
        provider_class = provider_registry._providers.get(provider_name)
        provider = provider_class()
        result = await provider.validate_connection(request.config)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_models(
    provider: str = Query(..., description="Provider name"),
    settings_id: str = Query(..., description="Settings ID"),
    server_id: str = Query(..., description="Server ID"),
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user),
    _: None = Depends(rate_limit_user(limit=100, window_seconds=60))
):
    """Get available models from a provider."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not auth:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(auth.user_id)
        
        # Normalize user_id by removing hyphens if present
        user_id = user_id.replace("-", "")
            
        print(f"Getting models for: provider={provider}, settings_id={settings_id}, server_id={server_id}, user_id={user_id}")
        
        # Get settings for the specified user
        settings = await SettingInstance.get_all_parameterized(
            db, 
            definition_id=settings_id,
            scope=SettingScope.USER.value, 
            user_id=user_id
        )
        
        print(f"Found {len(settings)} settings for user_id={user_id}")
        
        if not settings or len(settings) == 0:
            print("ORM returned no settings; falling back to direct SQL query")
            settings = await SettingInstance.get_all(
                db,
                definition_id=settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )

        if not settings or len(settings) == 0:
            raise HTTPException(status_code=404, detail=f"Provider settings not found for user_id={user_id}")

        # Use the first setting found
        setting = settings[0]

        # Extract configuration from settings value
        setting_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')
        setting_value = setting['value'] if isinstance(setting, dict) else getattr(setting, 'value', None)

        if isinstance(setting_value, (dict, list)):
            value_dict = setting_value
        else:
            raw_value = setting_value
            try:
                if isinstance(raw_value, str) and encryption_service.is_encrypted_value(raw_value):
                    raw_value = encryption_service.decrypt_field('settings_instances', 'value', raw_value)

                value_dict = safe_encrypted_json_parse(
                    raw_value,
                    context=f"provider={provider}, settings_id={settings_id}, user_id={user_id}",
                    setting_id=setting_id,
                    definition_id=settings_id,
                )

                if isinstance(value_dict, str):
                    # Some legacy rows stored the API key directly as a string
                    value_dict = {"api_key": value_dict}

            except Exception as parse_err:
                raise HTTPException(status_code=400, detail="Invalid settings value format") from parse_err

        print(f"Parsed settings value: {value_dict}")
        
        # Handle different provider configurations
        if provider == "openai":
            # OpenAI uses simple api_key structure
            api_key = value_dict.get("api_key", "")
            if not api_key:
                raise HTTPException(status_code=400, detail="OpenAI API key is required")
            
            config = {
                "api_key": api_key,
                "server_url": "https://api.openai.com/v1",  # Default OpenAI API URL
                "server_name": "OpenAI API"
            }
            print(f"Created OpenAI config with API key")
        elif provider == "openrouter":
            # OpenRouter uses simple api_key structure
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or ""
            if not api_key:
                raise HTTPException(status_code=400, detail="OpenRouter API key is required")
            
            config = {
                "api_key": api_key,
                "server_url": "https://openrouter.ai/api/v1",  # OpenRouter API URL
                "server_name": "OpenRouter API"
            }
            print(f"Created OpenRouter config with API key")
        elif provider == "claude":
            # Claude uses simple api_key structure (similar to OpenAI)
            api_key = value_dict.get("api_key", "")
            if not api_key:
                raise HTTPException(status_code=400, detail="Claude API key is required")
            
            # For Claude, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.anthropic.com",  # Claude API URL
                "server_name": "Claude API"
            }
            print(f"Created Claude config with API key")
        elif provider == "groq":
            # Groq uses simple api_key structure (similar to OpenAI)
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or ""
            if not api_key:
                raise HTTPException(status_code=400, detail="Groq API key is required")
            
            # For Groq, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.groq.com",  # Groq API URL
                "server_name": "Groq API"
            }
            print(f"Created Groq config with API key")
        else:
            # Other providers (like Ollama) use servers array
            servers = value_dict.get("servers", [])
            print(f"Found {len(servers)} servers in settings")
            
            # Find the specific server by ID
            server = next((s for s in servers if s.get("id") == server_id), None)
            if not server and servers:
                # If the requested server ID is not found but there are servers available,
                # use the first server as a fallback
                print(f"Server with ID {server_id} not found, using first available server as fallback")
                server = servers[0]
                print(f"Using fallback server: {server.get('serverName')} ({server.get('id')})")
            
            if not server:
                raise HTTPException(status_code=404, detail=f"Server not found with ID: {server_id}")
            
            print(f"Found server: {server.get('serverName')}")
            
            # Create provider configuration from server details
            config = {
                "server_url": server.get("serverAddress"),
                "api_key": server.get("apiKey", ""),
                "server_name": server.get("serverName")
            }
        
        print(f"Created config with server_url: {config['server_url']}")
        
        # Get provider instance
        provider_instance = await provider_registry.get_provider(
            provider, 
            server_id,
            config
        )
        
        print(f"Got provider instance: {provider_instance.provider_name}")
        
        # Get models
        models = await provider_instance.get_models()
        print(f"Got {len(models)} models")
        
        return {
            "models": models
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_models: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all-models")
async def get_all_models(
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user),
    _: None = Depends(rate_limit_user(limit=100, window_seconds=60))
):
    """Get models from ALL connected providers for a user."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not auth:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(auth.user_id)
        
        # Normalize user_id by removing hyphens if present
        user_id = user_id.replace("-", "")
        
        print(f"Getting all models for user: {user_id}")
        
        # Define all possible provider settings
        provider_settings = [
            {
                "provider": "openai",
                "settings_id": "openai_api_keys_settings",
                "server_id": "openai_default_server"
            },
            {
                "provider": "openrouter", 
                "settings_id": "openrouter_api_keys_settings",
                "server_id": "openrouter_default_server"
            },
            {
                "provider": "claude",
                "settings_id": "claude_api_keys_settings", 
                "server_id": "claude_default_server"
            },
            {
                "provider": "groq",
                "settings_id": "groq_api_keys_settings",
                "server_id": "groq_default_server"
            },
            {
                "provider": "ollama",
                "settings_id": "ollama_servers_settings",
                "server_id": None  # Ollama uses dynamic server IDs
            }
        ]
        
        all_models = []
        errors = []
        successful_providers = 0
        
        # Helper to map provider to API key env var
        def _get_env_api_key(provider_name: str) -> Optional[str]:
            env_map = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "claude": "ANTHROPIC_API_KEY",
                "groq": "GROQ_API_KEY",
            }
            env_var = env_map.get(provider_name.lower())
            if env_var:
                return os.getenv(env_var)
            return None

        # Process each provider
        for provider_config in provider_settings:
            try:
                provider = provider_config["provider"]
                settings_id = provider_config["settings_id"]
                server_id = provider_config["server_id"]
                
                print(f"Processing provider: {provider}")
                
                # Get settings for this provider (prefer ORM; fallback to direct SQL if none)
                settings = await SettingInstance.get_all_parameterized(
                    db,
                    definition_id=settings_id,
                    scope=SettingScope.USER.value,
                    user_id=user_id
                )
                if not settings or len(settings) == 0:
                    print(f"ORM returned no settings for {provider}; falling back to direct SQL")
                    settings = await SettingInstance.get_all(
                        db,
                        definition_id=settings_id,
                        scope=SettingScope.USER.value,
                        user_id=user_id
                    )
                
                if not settings or len(settings) == 0:
                    print(f"No settings found for {provider}")
                    # For API-key providers, try env fallback
                    if provider in {"openai", "openrouter", "claude", "groq"}:
                        env_key = _get_env_api_key(provider)
                        if env_key:
                            try:
                                if provider == "openai":
                                    config = {"api_key": env_key, "server_url": "https://api.openai.com/v1", "server_name": "OpenAI API"}
                                elif provider == "openrouter":
                                    config = {"api_key": env_key, "server_url": "https://openrouter.ai/api/v1", "server_name": "OpenRouter API"}
                                elif provider == "claude":
                                    config = {"api_key": env_key, "server_url": "https://api.anthropic.com", "server_name": "Claude API"}
                                elif provider == "groq":
                                    config = {"api_key": env_key, "server_url": "https://api.groq.com", "server_name": "Groq API"}
                                else:
                                    config = {"api_key": env_key}

                                provider_instance = await provider_registry.get_provider(provider, server_id or f"{provider}_default_server", config)
                                models = await provider_instance.get_models()
                                for model in models:
                                    model["provider"] = provider
                                    model["server_id"] = server_id or f"{provider}_default_server"
                                    model["server_name"] = config["server_name"]
                                    all_models.append(model)
                                successful_providers += 1
                                print(f"Successfully loaded {len(models)} models from {provider} via env key")
                            except Exception as e:
                                error_msg = f"Failed to load models from {provider} via env key: {str(e)}"
                                errors.append(error_msg)
                                print(f"Error: {error_msg}")
                        else:
                            print(f"No env key for {provider}; skipping")
                    # Continue to next provider
                    continue
                
                # Use the first setting found
                setting = settings[0]
                setting_value = setting['value'] if isinstance(setting, dict) else setting.value
                setting_instance_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')

                # Robust parse to handle encrypted or malformed values
                try:
                    value_dict = None
                    if isinstance(setting_value, str) and encryption_service.is_encrypted_value(setting_value):
                        # Decrypt first, then use the decrypted JSON
                        try:
                            value_dict = encryption_service.decrypt_field('settings_instances', 'value', setting_value)
                        except EncryptionError as ee:
                            raise ValueError(f"Decryption failed for {settings_id}: {str(ee)}")
                    else:
                        # Use safe parser which handles multiple JSON edge cases
                        value_dict = safe_encrypted_json_parse(
                            setting_value,
                            context=f"all-models settings_id={settings_id}, user_id={user_id}",
                            setting_id=setting_instance_id,
                            definition_id=settings_id,
                        )
                except Exception as e:
                    err = f"Failed to parse settings for {provider}: {str(e)}"
                    print(err)
                    errors.append(err)
                    # For Ollama, attempt a safe default structure; otherwise skip
                    if provider == "ollama":
                        value_dict = create_default_ollama_settings()
                    elif provider in {"openai", "openrouter", "claude", "groq"}:
                        # Try env fallback for API-key providers
                        env_key = _get_env_api_key(provider)
                        if env_key:
                            value_dict = {"api_key": env_key}
                        else:
                            continue
                
                # Check if provider has valid configuration
                if provider == "ollama":
                    # Ollama needs servers array
                    if not isinstance(value_dict, dict) or not value_dict.get("servers") or len(value_dict["servers"]) == 0:
                        print(f"No servers configured for {provider}, skipping")
                        continue
                else:
                    # Other providers need API key (accept both api_key and apiKey)
                    if not isinstance(value_dict, dict):
                        print(f"Invalid settings format for {provider}, skipping")
                        continue
                    api_key = value_dict.get("api_key") or value_dict.get("apiKey")
                    if not api_key:
                        print(f"No API key for {provider}, skipping")
                        continue
                
                # Get provider instance and models
                if provider == "ollama":
                    # Handle Ollama servers dynamically
                    for server in value_dict["servers"]:
                        try:
                            config = {
                                "server_url": server.get("serverAddress"),
                                "api_key": server.get("apiKey", ""),
                                "server_name": server.get("serverName", "Unknown Server")
                            }
                            
                            provider_instance = await provider_registry.get_provider(
                                provider,
                                server["id"],
                                config
                            )
                            
                            models = await provider_instance.get_models()
                            for model in models:
                                model["provider"] = provider
                                model["server_id"] = server["id"]
                                model["server_name"] = server.get("serverName", "Unknown Server")
                                all_models.append(model)
                            
                            successful_providers += 1
                            print(f"Successfully loaded {len(models)} models from {provider} server: {server['id']}")
                            
                        except Exception as e:
                            error_msg = f"Failed to load models from {provider} server {server.get('id', 'unknown')}: {str(e)}"
                            errors.append(error_msg)
                            print(f"Error: {error_msg}")
                else:
                    # Handle API key-based providers
                    try:
                        if provider == "openai":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey"),
                                "server_url": "https://api.openai.com/v1",
                                "server_name": "OpenAI API"
                            }
                        elif provider == "openrouter":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openrouter"),
                                "server_url": "https://openrouter.ai/api/v1",
                                "server_name": "OpenRouter API"
                            }
                        elif provider == "claude":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("claude"),
                                "server_url": "https://api.anthropic.com",
                                "server_name": "Claude API"
                            }
                        elif provider == "groq":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("groq"),
                                "server_url": "https://api.groq.com",
                                "server_name": "Groq API"
                            }
                        
                        provider_instance = await provider_registry.get_provider(
                            provider,
                            server_id,
                            config
                        )
                        
                        models = await provider_instance.get_models()
                        for model in models:
                            model["provider"] = provider
                            model["server_id"] = server_id
                            model["server_name"] = config["server_name"]
                            all_models.append(model)
                        
                        successful_providers += 1
                        print(f"Successfully loaded {len(models)} models from {provider}")
                        
                    except Exception as e:
                        error_msg = f"Failed to load models from {provider}: {str(e)}"
                        errors.append(error_msg)
                        print(f"Error: {error_msg}")
                
            except Exception as e:
                error_msg = f"Error processing {provider}: {str(e)}"
                errors.append(error_msg)
                print(f"Error: {error_msg}")
        
        print(f"Total models loaded: {len(all_models)} from {successful_providers} providers")
        
        return {
            "models": all_models,
            "total_count": len(all_models),
            "successful_providers": successful_providers,
            "errors": errors,
            "summary": {
                "total_providers_checked": len(provider_settings),
                "successful_providers": successful_providers,
                "failed_providers": len(errors),
                "total_models": len(all_models)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_all_models: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
async def generate_text(request: TextGenerationRequest, db: AsyncSession = Depends(get_db)):
    """Generate text from a prompt.
    
    Uses the 'stream' parameter to determine whether to return a streaming or batch response.
    """
    try:
        # Get provider instance using the helper function
        provider_instance = await get_provider_instance_from_request(request, db)
        
        # Handle streaming
        if request.stream:
            async def stream_generator():
                async for chunk in provider_instance.generate_stream(
                    request.prompt, 
                    request.model, 
                    request.params
                ):
                    # Yield each chunk and flush immediately
                    yield f"data: {json.dumps(chunk)}\n\n"
                    # Add an explicit flush marker
                    yield ""
                yield "data: [DONE]\n\n"
            
            # Add headers to prevent buffering
            headers = {
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",  # Disable Nginx buffering
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
            
            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream",
                headers=headers
            )
        
        # Handle non-streaming
        result = await provider_instance.generate_text(
            request.prompt, 
            request.model, 
            request.params
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_generation(request: dict = Body(...)):
    """Cancel ongoing generation for a conversation."""
    # TODO: Complete backend cancellation logic here
    return {
            "status": "success",
            "message": "Generation cancellation requested (basic mode)",
            # "conversation_id": conversation_id,
            "cancelled": True
        }

@router.post("/chat")
async def chat_completion(request: ChatCompletionRequest, db: AsyncSession = Depends(get_db)):
    """Generate a chat completion.
    
    Uses the 'stream' parameter to determine whether to return a streaming or batch response.
    Also stores the conversation history in the database and uses it for context.
    """
    logger = logging.getLogger(__name__)
    try:
        print(f"🎯 CHAT COMPLETION ENDPOINT CALLED")
        print(f"📊 Provider: {request.provider}")
        print(f"📊 Settings ID: {request.settings_id}")
        print(f"📊 Server ID: {request.server_id}")
        print(f"📊 Model: {request.model}")
        print(f"📊 User ID: {request.user_id}")
        print(f"📊 Stream: {request.stream}")
        MODULE_LOGGER.info(f"Production chat endpoint called with: provider={request.provider}, settings_id={request.settings_id}, server_id={request.server_id}, model={request.model}")
        logger.debug(f"Messages: {request.messages}")
        logger.debug(f"Params: {request.params}")
        
        # Validate persona data if provided
        if request.persona_id or request.persona_system_prompt or request.persona_model_settings:
            MODULE_LOGGER.info(f"Persona data provided - persona_id: {request.persona_id}")
            
            # Basic validation: if persona_id is provided, persona_system_prompt should also be provided
            if request.persona_id and not request.persona_system_prompt:
                logger.error(f"Invalid persona data: persona_id provided but persona_system_prompt is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid persona data: persona_system_prompt is required when persona_id is provided"
                )
            
            # Validate persona model settings if provided
            if request.persona_model_settings:
                try:
                    # Import here to avoid circular imports
                    from app.schemas.persona import ModelSettings
                    ModelSettings(**request.persona_model_settings)
                    logger.debug(f"Persona model settings validated successfully")
                except Exception as validation_error:
                    logger.error(f"Invalid persona model settings: {validation_error}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid persona model settings: {str(validation_error)}"
                    )
        
        # Get provider instance using the helper function
        MODULE_LOGGER.info("Getting provider instance from request")
        provider_instance = await get_provider_instance_from_request(request, db)
        MODULE_LOGGER.info(f"Provider instance created successfully: {provider_instance.provider_name}")
        
        # Convert messages to the format expected by the provider
        current_messages = [message.model_dump() for message in request.messages]
        print(f"Current messages: {current_messages}")
        
        combined_messages = current_messages.copy()

        # Get or create a conversation
        from app.models.conversation import Conversation
        from app.models.message import Message
        import uuid
        
        # Extract user_id from the request
        user_id = request.user_id
        # The conversation_id is defined in the ChatCompletionRequest schema, so we can access it directly
        conversation_id = request.conversation_id
        conversation_was_created = False
        history_pre_compaction_event_ids: set[str] = set()
        history_digest_schedule_event_ids: set[str] = set()
        print(f"Conversation ID from request: {conversation_id}")
        print(f"USER ID from request: {user_id} - THIS SHOULD BE THE CURRENT USER'S ID, NOT HARDCODED")
        
        # Debug: Print the entire request for inspection
        print(f"Request details:")
        print(f"  provider: {request.provider}")
        print(f"  settings_id: {request.settings_id}")
        print(f"  server_id: {request.server_id}")
        print(f"  model: {request.model}")
        print(f"  user_id: {user_id}")
        print(f"  conversation_id: {conversation_id}")
        print(f"  messages count: {len(request.messages)}")
        for i, msg in enumerate(request.messages):
            print(f"    Message {i+1}: role={msg.role}, content={msg.content[:50]}...")
            
            # If conversation_id is provided, get the existing conversation
            if conversation_id:
                print(f"Attempting to retrieve conversation with ID: {conversation_id}")
                conversation = await Conversation.get_by_id(db, conversation_id)
                if not conversation:
                    print(f"ERROR: Conversation with ID {conversation_id} not found in database")
                    raise HTTPException(status_code=404, detail="Conversation not found")
                
                print(f"Found conversation: {conversation.id}, user_id: {conversation.user_id}")
                
                # Ensure the user owns the conversation
                if str(conversation.user_id) != str(user_id):
                    print(f"ERROR: User {user_id} is not authorized to access conversation {conversation_id}")
                    print(f"Conversation owner: {conversation.user_id}, Request user: {user_id}, Original request user_id: {request.user_id}")
                    raise HTTPException(status_code=403, detail="Not authorized to access this conversation")

                requested_page_id = _normalize_page_id(request.page_id)
                conversation_page_id = _normalize_page_id(getattr(conversation, "page_id", None))
                if requested_page_id and conversation_page_id and requested_page_id != conversation_page_id:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Conversation is bound to a different page. "
                            "Start a new chat for this page."
                        ),
                    )
                if requested_page_id and not conversation_page_id:
                    conversation.page_id = requested_page_id
                    await db.commit()
                    await db.refresh(conversation)
                
                # Update conversation with persona_id if provided and different from current
                if request.persona_id and conversation.persona_id != request.persona_id:
                    MODULE_LOGGER.info(f"Updating conversation {conversation_id} with persona_id: {request.persona_id}")
                    conversation.persona_id = request.persona_id
                    await db.commit()
                    await db.refresh(conversation)
                
                # Get previous messages for this conversation
                print(f"Retrieving previous messages for conversation {conversation_id}")
                previous_messages = await conversation.get_messages(db)
                print(f"Retrieved {len(previous_messages)} previous messages")
                history_pre_compaction_event_ids = _collect_mcp_event_ids_from_history(
                    previous_messages,
                    event_key="pre_compaction_flush_event_id",
                )
                history_digest_schedule_event_ids = _collect_mcp_event_ids_from_history(
                    previous_messages,
                    event_key="digest_schedule_event_id",
                )
                
                # Convert previous messages to the format expected by the provider
                if previous_messages and len(previous_messages) > 0:
                    # Sort messages by created_at to ensure correct order
                    previous_messages.sort(key=lambda x: x.created_at)
                    print(f"Sorted {len(previous_messages)} messages by timestamp")
                    
                    # Print all previous messages for debugging
                    for i, msg in enumerate(previous_messages):
                        print(f"  Previous message {i+1}: sender={msg.sender}, created_at={msg.created_at}, content={msg.message[:50]}...")
                    
                    # Convert to the format expected by the provider
                    # We'll skip the last message if it's from the user, as it's likely duplicated in the current request
                    skip_last = previous_messages[-1].sender == "user" and len(current_messages) > 0
                    print(f"Skip last message: {skip_last} (last message sender: {previous_messages[-1].sender}, current messages: {len(current_messages)})")
                    
                    history_messages = []
                    for i, msg in enumerate(previous_messages):
                        # Skip the last message if it's from the user and we have current messages
                        if skip_last and i == len(previous_messages) - 1:
                            print(f"  Skipping last message (index {i})")
                            continue
                        
                        # Prefer explicit role stored in metadata, fall back to sender
                        role = None
                        if msg.message_metadata and isinstance(msg.message_metadata, dict):
                            role = msg.message_metadata.get("role")
                        if role not in {"assistant", "user", "system"}:
                            role = "assistant" if msg.sender == "llm" else "user"
                        
                        history_messages.append({
                            "role": role,
                            "content": msg.message
                        })
                        print(f"  Added message to history: role={role}, content={msg.message[:50]}...")
                    
                    # Replace combined_messages with history followed by current
                    combined_messages = history_messages + current_messages
                    
                    print(f"Using {len(history_messages)} previous messages + {len(current_messages)} current messages = {len(combined_messages)} total messages")
                    print(f"Final combined messages:")
                    for i, msg in enumerate(combined_messages):
                        print(f"  Combined message {i+1}: role={msg.get('role', 'unknown')}, content={msg.get('content', '')[:50]}...")
                    
                    MODULE_LOGGER.info(f"Using {len(history_messages)} previous messages for context")
                    logger.debug(f"Combined messages: {combined_messages}")
            else:
                # Create a new conversation
                conversation = Conversation(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    title=f"Conversation with {request.model}",
                    page_context=request.page_context,  # This is already defined in the schema with a default of None
                    page_id=request.page_id,  # NEW FIELD - ID of the page this conversation belongs to
                    model=request.model,
                    server=provider_instance.server_name,
                    conversation_type=request.conversation_type or "chat",  # New field with default
                    persona_id=request.persona_id  # Store persona_id when creating conversation
                )
                db.add(conversation)
                await db.commit()
                await db.refresh(conversation)
                conversation_was_created = True
                print(f"Created new conversation with ID: {conversation.id}")
                
                # If persona has a sample greeting, add it as the first assistant message
                if request.persona_sample_greeting:
                    MODULE_LOGGER.info(f"Adding persona sample greeting for persona_id: {request.persona_id}")
                    greeting_message = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation.id,
                        sender="llm",
                        message=request.persona_sample_greeting,
                        message_metadata={
                            "persona_id": request.persona_id,
                            "persona_greeting": True,
                            "model": request.model,
                            "temperature": 0.0  # Greeting is static, not generated
                        }
                    )
                    db.add(greeting_message)
                    await db.commit()
                    print(f"Added persona sample greeting: {request.persona_sample_greeting[:50]}...")
            
            document_context_mode = (request.params or {}).get("document_context_mode")

            # Apply persona prompt/model settings after history merge (preserve new persona changes)
            combined_messages, enhanced_params = apply_persona_prompt_and_params(
                combined_messages,
                request.params or {},
                request.persona_system_prompt,
                request.persona_model_settings,
                max_history=100  # trim oldest history messages if needed
            )
            owner_profile_system_message, owner_profile_metadata = _build_owner_profile_system_message(
                user_id
            )
            combined_messages = _insert_orchestration_system_message(
                combined_messages,
                owner_profile_system_message,
            )

            # Remove local-only params before sending to provider
            provider_params = enhanced_params.copy()
            provider_params.pop("document_context_mode", None)

            # Extract explicit approval-resume controls before provider/tool params are finalized.
            approval_controls = _extract_mcp_approval_params(provider_params)

            # Extract MCP scope/tool flags from provider params (server-side orchestration contract).
            requested_conversation_type = _normalize_conversation_type(request.conversation_type)
            latest_user_message_hint = _latest_user_message_text(current_messages)
            mcp_scope = _extract_mcp_scope_params(provider_params)
            explicit_project_scope = (
                str(mcp_scope.get("mcp_scope_mode") or "").strip().lower() == "project"
                and isinstance(mcp_scope.get("mcp_project_slug"), str)
                and bool(str(mcp_scope.get("mcp_project_slug")).strip())
            )

            inferred_life_topic: Optional[str] = None
            if not explicit_project_scope:
                inferred_life_topic = _infer_life_topic_for_request(
                    requested_conversation_type,
                    request.page_context,
                    latest_user_message_hint,
                )

            effective_conversation_type = requested_conversation_type
            if inferred_life_topic and not _extract_life_topic(requested_conversation_type):
                effective_conversation_type = f"life-{inferred_life_topic}"

            digest_schedule_config = _extract_digest_schedule_config(
                provider_params,
                conversation_type=effective_conversation_type,
            )
            digest_delivery_send_config = _extract_digest_delivery_send_config(
                provider_params
            )
            digest_delivery_channel = _extract_digest_delivery_channel(
                effective_conversation_type
            )
            explicit_tool_profile_value = provider_params.get("mcp_tool_profile")
            explicit_tool_profile = (
                isinstance(explicit_tool_profile_value, str)
                and bool(explicit_tool_profile_value.strip())
            )
            if not explicit_tool_profile:
                if _is_digest_reply_conversation(effective_conversation_type):
                    provider_params["mcp_tool_profile"] = TOOL_PROFILE_FULL
                elif _is_digest_conversation(effective_conversation_type) and not _is_digest_reply_conversation(
                    effective_conversation_type
                ):
                    provider_params["mcp_tool_profile"] = TOOL_PROFILE_DIGEST

            scope_source = _apply_conversation_scope_defaults(effective_conversation_type, mcp_scope)
            mcp_tooling_metadata: Dict[str, Any] = {
                "conversation_type": effective_conversation_type,
                "conversation_type_requested": requested_conversation_type,
                "mcp_tools_enabled": bool(mcp_scope.get("mcp_tools_enabled")),
                "mcp_scope_mode": mcp_scope.get("mcp_scope_mode"),
                "mcp_project_slug": mcp_scope.get("mcp_project_slug"),
                "mcp_project_name": mcp_scope.get("mcp_project_name"),
                "mcp_project_lifecycle": mcp_scope.get("mcp_project_lifecycle"),
                "mcp_project_source": mcp_scope.get("mcp_project_source"),
                "available_count": 0,
                "selected_count": 0,
                "digest_schedule_enabled": bool(digest_schedule_config.get("enabled")),
                "digest_schedule_due_now": bool(digest_schedule_config.get("due_now")),
                "digest_sections": digest_schedule_config.get("sections") or list(DEFAULT_DIGEST_SECTIONS),
                "digest_cadence_hours": int(digest_schedule_config.get("cadence_hours") or 24),
                "digest_next_due_at_utc": digest_schedule_config.get("next_due_at_utc"),
                "digest_reply_to_capture_enabled": bool(
                    digest_schedule_config.get("reply_to_capture_enabled")
                ),
                "digest_delivery_channel": digest_delivery_channel,
                "digest_delivery_send_enabled": bool(digest_delivery_send_config.get("enabled")),
            }
            if digest_delivery_send_config.get("endpoint_sanitized"):
                mcp_tooling_metadata["digest_delivery_send_endpoint"] = str(
                    digest_delivery_send_config["endpoint_sanitized"]
                )
            if effective_conversation_type != requested_conversation_type:
                mcp_tooling_metadata["conversation_type_source"] = "inferred"
            if scope_source:
                mcp_tooling_metadata["mcp_scope_source"] = scope_source
            mcp_tooling_metadata.update(owner_profile_metadata)
            tool_routing_decision = _resolve_tool_routing_decision(
                provider=request.provider,
                model=request.model,
                provider_params=provider_params,
            )
            tool_routing_decision = _apply_dual_path_scope_tool_policy(
                routing_decision=tool_routing_decision,
                conversation_type=effective_conversation_type,
                mcp_scope=mcp_scope,
                latest_user_message=latest_user_message_hint,
            )
            if tool_routing_decision.get("disable_tools"):
                mcp_scope["mcp_tools_enabled"] = False
            mcp_tooling_metadata["mcp_tools_enabled"] = bool(mcp_scope.get("mcp_tools_enabled"))
            mcp_tooling_metadata.update(
                {
                    "tool_routing_mode": tool_routing_decision.get("route_mode"),
                    "tool_execution_mode": tool_routing_decision.get("execution_mode"),
                    "tool_profile": tool_routing_decision.get("tool_profile"),
                    "tool_profile_source": tool_routing_decision.get("tool_profile_source"),
                    "native_tool_calling_model": bool(
                        tool_routing_decision.get("native_tool_calling")
                    ),
                    "routing_capability_source": tool_routing_decision.get("capability_source"),
                    "dual_path_requested": bool(tool_routing_decision.get("dual_path_requested")),
                }
            )
            if tool_routing_decision.get("policy_mode"):
                mcp_tooling_metadata["tool_policy_mode"] = tool_routing_decision.get(
                    "policy_mode"
                )
            if tool_routing_decision.get("dual_path_fallback_reason"):
                mcp_tooling_metadata["dual_path_fallback_reason"] = tool_routing_decision.get(
                    "dual_path_fallback_reason"
                )

            logger.info(
                "chat_conversation_type_resolution requested=%s effective=%s inferred_life_topic=%s scope_mode=%s project_slug=%s",
                requested_conversation_type,
                effective_conversation_type,
                inferred_life_topic,
                mcp_scope.get("mcp_scope_mode"),
                mcp_scope.get("mcp_project_slug"),
            )

            if (
                hasattr(conversation, "conversation_type")
                and isinstance(conversation.conversation_type, str)
                and conversation.conversation_type.strip().lower() in {"", "chat"}
                and effective_conversation_type != conversation.conversation_type
            ):
                conversation.conversation_type = effective_conversation_type

            approval_resume_context: Optional[Dict[str, Any]] = None
            approval_action = approval_controls.get("action")
            approval_action_source = (
                "explicit" if isinstance(approval_action, str) and approval_action else None
            )
            if not approval_action and conversation_id:
                inferred_action = _parse_chat_approval_action(latest_user_message_hint)
                if inferred_action:
                    approval_action = inferred_action
                    approval_action_source = "message_inferred"
                    approval_controls["action"] = inferred_action
                    MODULE_LOGGER.info(
                        "approval_resume_inferred conversation_id=%s user_id=%s action=%s",
                        conversation.id,
                        user_id,
                        inferred_action,
                    )

            if approval_action:
                if not conversation_id:
                    raise HTTPException(
                        status_code=400,
                        detail="mcp_approval requires an existing conversation_id",
                    )

                pending_query = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender == "llm",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(50)
                )
                pending_result = await db.execute(pending_query)
                pending_messages = pending_result.scalars().all()
                pending_message: Optional[Any] = None
                pending_request: Optional[Dict[str, Any]] = None

                requested_id = approval_controls.get("request_id")
                requested_tool = approval_controls.get("tool")

                for candidate in pending_messages:
                    candidate_meta = (
                        candidate.message_metadata
                        if isinstance(candidate.message_metadata, dict)
                        else {}
                    )
                    candidate_mcp = (
                        candidate_meta.get("mcp")
                        if isinstance(candidate_meta.get("mcp"), dict)
                        else {}
                    )
                    candidate_request = candidate_mcp.get("approval_request")
                    if not isinstance(candidate_request, dict):
                        continue

                    candidate_status = str(
                        candidate_request.get("status") or "pending"
                    ).lower()
                    if candidate_status in {
                        "approved",
                        "rejected",
                        "denied",
                        "cancelled",
                        "canceled",
                    }:
                        continue

                    candidate_request_id = candidate_request.get("request_id")
                    if not isinstance(candidate_request_id, str) or not candidate_request_id.strip():
                        candidate_request_id = f"apr_{candidate.id}"
                        candidate_request["request_id"] = candidate_request_id

                    if requested_id and candidate_request_id != requested_id:
                        continue
                    if requested_tool and candidate_request.get("tool") != requested_tool:
                        continue

                    pending_message = candidate
                    pending_request = candidate_request
                    break

                if pending_message is None or pending_request is None:
                    if approval_action_source == "message_inferred":
                        MODULE_LOGGER.info(
                            "approval_resume_inferred_no_pending conversation_id=%s user_id=%s action=%s",
                            conversation.id,
                            user_id,
                            approval_action,
                        )
                    else:
                        raise HTTPException(
                            status_code=409,
                            detail="No pending approval request found for this conversation.",
                        )
                else:
                    pending_tool = pending_request.get("tool")
                    if not isinstance(pending_tool, str) or not pending_tool.strip():
                        raise HTTPException(
                            status_code=409,
                            detail="Pending approval request is missing tool metadata.",
                        )

                    pending_arguments = pending_request.get("arguments")
                    if not isinstance(pending_arguments, dict):
                        pending_arguments = {}

                    override_arguments = approval_controls.get("arguments")
                    effective_arguments = (
                        override_arguments if isinstance(override_arguments, dict) else pending_arguments
                    )

                    resolved_request_id = str(pending_request.get("request_id"))
                    resolution_status = "approved" if approval_action == "approve" else "rejected"
                    pending_request.update(
                        {
                            "status": resolution_status,
                            "resolved_at": _utc_timestamp(),
                            "resolution_source": "client",
                            "resolution_message": (
                                current_messages[-1].get("content")
                                if current_messages and isinstance(current_messages[-1], dict)
                                else None
                            ),
                        }
                    )

                    pending_meta = (
                        copy.deepcopy(pending_message.message_metadata)
                        if isinstance(pending_message.message_metadata, dict)
                        else {}
                    )
                    pending_mcp = (
                        pending_meta.get("mcp")
                        if isinstance(pending_meta.get("mcp"), dict)
                        else {}
                    )
                    pending_mcp["approval_request"] = pending_request
                    pending_meta["mcp"] = pending_mcp
                    pending_message.message_metadata = pending_meta
                    flag_modified(pending_message, "message_metadata")
                    await db.commit()

                    approval_resume_context = {
                        "action": approval_action,
                        "request_id": resolved_request_id,
                        "tool": pending_tool.strip(),
                        "arguments": effective_arguments,
                        "safety_class": pending_request.get("safety_class") or "mutating",
                        "summary": pending_request.get("summary"),
                        "synthetic_reason": pending_request.get("synthetic_reason"),
                        "origin_user_message": pending_request.get("origin_user_message"),
                        "scope": pending_request.get("scope")
                        if isinstance(pending_request.get("scope"), dict)
                        else {},
                        "prior_tool_calls": [
                            dict(item)
                            for item in (
                                pending_mcp.get("tool_calls_executed")
                                if isinstance(pending_mcp.get("tool_calls_executed"), list)
                                else []
                            )
                            if isinstance(item, dict)
                        ],
                    }
                    mcp_tooling_metadata.update(
                        {
                            "approval_resume_action": approval_action,
                            "approval_resume_request_id": resolved_request_id,
                            "approval_resume_tool": pending_tool.strip(),
                            "approval_resume_source": approval_action_source or "explicit",
                        }
                    )

                    # If client omitted scope in resume request, reuse stored scope from original approval.
                    pending_scope = approval_resume_context.get("scope") or {}
                    if (
                        approval_action == "approve"
                        and str(mcp_scope.get("mcp_scope_mode")) != "project"
                        and isinstance(pending_scope, dict)
                        and pending_scope.get("mcp_project_slug")
                    ):
                        mcp_scope.update(
                            {
                                "mcp_tools_enabled": True,
                                "mcp_scope_mode": "project",
                                "mcp_project_slug": pending_scope.get("mcp_project_slug"),
                                "mcp_project_name": pending_scope.get("mcp_project_name"),
                                "mcp_project_lifecycle": pending_scope.get("mcp_project_lifecycle"),
                                "mcp_project_source": pending_scope.get("mcp_project_source") or "ui",
                                "mcp_plugin_slug": pending_scope.get("mcp_plugin_slug"),
                            }
                        )
                        mcp_tooling_metadata.update(
                            {
                                "mcp_tools_enabled": True,
                                "mcp_scope_mode": "project",
                                "mcp_project_slug": pending_scope.get("mcp_project_slug"),
                                "mcp_project_name": pending_scope.get("mcp_project_name"),
                                "mcp_project_lifecycle": pending_scope.get("mcp_project_lifecycle"),
                                "mcp_project_source": pending_scope.get("mcp_project_source") or "ui",
                            }
                        )

            capture_intent_message_hint = latest_user_message_hint
            if approval_resume_context and approval_resume_context.get("action") == "approve":
                capture_intent_message_hint = None
                origin_user_message = approval_resume_context.get("origin_user_message")
                if isinstance(origin_user_message, str) and origin_user_message.strip():
                    capture_intent_message_hint = origin_user_message.strip()

                if not capture_intent_message_hint:
                    resume_synthetic_reason = str(
                        approval_resume_context.get("synthetic_reason") or ""
                    ).strip().lower()
                    resume_tool_name = str(approval_resume_context.get("tool") or "").strip()
                    resume_arguments = (
                        approval_resume_context.get("arguments")
                        if isinstance(approval_resume_context.get("arguments"), dict)
                        else {}
                    )
                    if resume_synthetic_reason == "capture_inbox_persist":
                        resume_content = resume_arguments.get("content")
                        if isinstance(resume_content, str) and resume_content.strip():
                            capture_intent_message_hint = resume_content.strip()
                    if not capture_intent_message_hint:
                        resume_content = _extract_capture_inbox_content_from_tool_call(
                            resume_tool_name,
                            resume_arguments,
                        )
                        if isinstance(resume_content, str) and resume_content.strip():
                            capture_intent_message_hint = resume_content.strip()

                if not capture_intent_message_hint:
                    capture_intent_message_hint = _latest_non_approval_user_message_text(
                        combined_messages
                    )
                if not capture_intent_message_hint:
                    capture_intent_message_hint = latest_user_message_hint

            tool_routing_decision = _apply_approval_resume_tool_policy(
                routing_decision=tool_routing_decision,
                approval_resume_context=approval_resume_context,
            )
            mcp_tooling_metadata.update(
                {
                    "tool_profile": tool_routing_decision.get("tool_profile"),
                    "tool_profile_source": tool_routing_decision.get("tool_profile_source"),
                }
            )
            if tool_routing_decision.get("policy_mode"):
                mcp_tooling_metadata["tool_policy_mode"] = tool_routing_decision.get(
                    "policy_mode"
                )

            resolved_tools: List[Dict[str, Any]] = []
            mcp_user_id = user_id or "current"
            if bool(mcp_scope.get("mcp_tools_enabled")) and str(mcp_scope.get("mcp_scope_mode")) == "project":
                mcp_service = MCPRegistryService(db)

                if bool(mcp_scope.get("mcp_sync_on_request")) and mcp_user_id != "current":
                    try:
                        await mcp_service.sync_user_servers(
                            mcp_user_id,
                            plugin_slug_filter=mcp_scope.get("mcp_plugin_slug"),
                        )
                    except Exception as sync_error:
                        logger.warning(
                            "mcp_sync_on_request_failed user_id=%s error=%s",
                            mcp_user_id,
                            sync_error,
                        )

                resolved_tools, resolve_meta = await mcp_service.resolve_tools_for_request(
                    mcp_user_id,
                    mcp_tools_enabled=bool(mcp_scope.get("mcp_tools_enabled")),
                    mcp_scope_mode=str(mcp_scope.get("mcp_scope_mode") or "none"),
                    mcp_project_slug=mcp_scope.get("mcp_project_slug"),
                    plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                    tool_profile=str(tool_routing_decision.get("tool_profile") or TOOL_PROFILE_FULL),
                    allowed_safety_classes=tool_routing_decision.get("allowed_safety_classes"),
                    tool_name_allowlist=tool_routing_decision.get("tool_name_allowlist"),
                    priority_tool_names=_resolve_priority_tool_names(
                        conversation_type=effective_conversation_type,
                        tool_profile=str(
                            tool_routing_decision.get("tool_profile") or TOOL_PROFILE_FULL
                        ),
                        tool_name_allowlist=tool_routing_decision.get("tool_name_allowlist"),
                    ),
                    max_tools=int(mcp_scope.get("mcp_max_tools") or 32),
                    max_schema_bytes=int(mcp_scope.get("mcp_max_schema_bytes") or 128_000),
                )
                mcp_tooling_metadata.update(resolve_meta)

            resolved_tools, sanitized_schema_count = _sanitize_tools_for_provider(
                resolved_tools,
                request.provider,
            )
            if sanitized_schema_count > 0:
                mcp_tooling_metadata["sanitized_tool_schema_count"] = sanitized_schema_count
                mcp_tooling_metadata["sanitized_tool_schema_provider"] = str(
                    request.provider or ""
                ).strip().lower()

            resolved_tool_names = _extract_resolved_tool_names(resolved_tools)
            if resolved_tools:
                provider_params["tools"] = resolved_tools
                provider_params["tool_choice"] = provider_params.get("tool_choice", "auto")
            else:
                provider_params.pop("tools", None)
                if "tool_choice" not in provider_params and bool(mcp_scope.get("mcp_tools_enabled")):
                    provider_params["tool_choice"] = "none"

            orchestration_mode, orchestration_message = _build_conversation_orchestration_prompt(
                effective_conversation_type,
                resolved_tools,
                digest_sections=digest_schedule_config.get("sections")
                if isinstance(digest_schedule_config, dict)
                else None,
                digest_due_now=bool(
                    digest_schedule_config.get("due_now")
                    if isinstance(digest_schedule_config, dict)
                    else False
                ),
            )
            if orchestration_mode:
                mcp_tooling_metadata["conversation_orchestration"] = orchestration_mode
            combined_messages = _insert_orchestration_system_message(
                combined_messages,
                orchestration_message,
            )

            mcp_max_tool_iterations = _as_int(
                provider_params.pop("mcp_max_tool_iterations", 5),
                default=5,
                minimum=1,
                maximum=10,
            )
            mcp_tool_timeout_seconds = float(
                provider_params.pop("mcp_tool_timeout_seconds", 15.0)
                or 15.0
            )
            if mcp_tool_timeout_seconds < 1:
                mcp_tool_timeout_seconds = 1.0
            if mcp_tool_timeout_seconds > 120:
                mcp_tool_timeout_seconds = 120.0

            mcp_provider_timeout_seconds = float(
                provider_params.pop(
                    "mcp_provider_timeout_seconds",
                    provider_params.pop("provider_timeout_seconds", 90.0),
                )
                or 90.0
            )
            if mcp_provider_timeout_seconds < 0.1:
                mcp_provider_timeout_seconds = 0.1
            if mcp_provider_timeout_seconds > 300:
                mcp_provider_timeout_seconds = 300.0

            requested_auto_approve_mutating = _as_bool(
                provider_params.pop("mcp_auto_approve_mutating", False),
                False,
            )
            if requested_auto_approve_mutating:
                logger.warning(
                    "mcp_auto_approve_mutating_ignored conversation_id=%s user_id=%s",
                    conversation.id,
                    user_id,
                )

            approved_mutating_raw = provider_params.pop("mcp_approved_mutating_tools", [])
            if isinstance(approved_mutating_raw, str):
                approved_mutating_tools = {
                    item.strip()
                    for item in approved_mutating_raw.split(",")
                    if item.strip()
                }
            elif isinstance(approved_mutating_raw, list):
                approved_mutating_tools = {
                    str(item).strip()
                    for item in approved_mutating_raw
                    if str(item).strip()
                }
            else:
                approved_mutating_tools = set()
            mcp_tooling_metadata["approval_mode_policy"] = APPROVAL_MODE_POLICY
            mcp_tooling_metadata["provider_timeout_seconds"] = round(
                mcp_provider_timeout_seconds, 3
            )
            pre_compaction_flush_config = _extract_pre_compaction_flush_config(provider_params)
            mcp_tooling_metadata.update(
                {
                    "pre_compaction_flush_enabled": bool(
                        pre_compaction_flush_config.get("enabled")
                    ),
                    "pre_compaction_context_window_tokens": int(
                        pre_compaction_flush_config.get("context_window_tokens") or 0
                    ),
                    "pre_compaction_flush_threshold": round(
                        float(pre_compaction_flush_config.get("threshold") or 0.9), 3
                    ),
                }
            )

            orchestration_context_payload: Optional[Dict[str, Any]] = None
            if bool(mcp_scope.get("mcp_tools_enabled")) and str(mcp_scope.get("mcp_scope_mode")) == "project":
                context_runtime_service = MCPRegistryService(
                    db,
                    call_timeout_seconds=mcp_tool_timeout_seconds,
                )
                try:
                    orchestration_context_payload = await _build_orchestration_context_payload(
                        runtime_service=context_runtime_service,
                        mcp_user_id=mcp_user_id,
                        conversation_type=effective_conversation_type,
                        mcp_scope=mcp_scope,
                        resolved_tools=resolved_tools,
                        plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                    )
                except Exception as context_error:
                    logger.warning(
                        "orchestration_context_build_failed conversation_id=%s user_id=%s error=%s",
                        conversation.id,
                        user_id,
                        context_error,
                    )
                    orchestration_context_payload = {
                        "conversation_type": effective_conversation_type,
                        "page_kind": _infer_page_kind(effective_conversation_type),
                        "scope_root": None,
                        "scope_path": None,
                        "required_file_map": {},
                        "required_files_missing": [],
                        "required_files_unverified": [],
                        "onboarding_state": None,
                        "tool_safety_metadata": {
                            "tool_classes": _extract_resolved_tool_safety(resolved_tools),
                        },
                        "approval_mode_policy": APPROVAL_MODE_POLICY,
                        "context_missing": ["orchestration_context_build"],
                        "context_ready": False,
                    }

                if isinstance(orchestration_context_payload, dict):
                    mcp_tooling_metadata["orchestration_context"] = orchestration_context_payload
                    page_kind_value = orchestration_context_payload.get("page_kind")
                    if isinstance(page_kind_value, str) and page_kind_value:
                        mcp_tooling_metadata["page_kind"] = page_kind_value

            auto_continue_enabled = _as_bool(provider_params.pop("auto_continue", True), True)
            auto_continue_max_passes = _as_int(
                provider_params.pop("auto_continue_max_passes", DEFAULT_AUTO_CONTINUE_MAX_PASSES),
                default=DEFAULT_AUTO_CONTINUE_MAX_PASSES,
                minimum=0,
                maximum=10,
            )
            auto_continue_min_progress_chars = _as_int(
                provider_params.pop("auto_continue_min_progress_chars", DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS),
                default=DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS,
                minimum=0,
                maximum=200,
            )
            auto_continue_prompt = provider_params.pop("auto_continue_prompt", DEFAULT_AUTO_CONTINUE_PROMPT)
            if not isinstance(auto_continue_prompt, str) or not auto_continue_prompt.strip():
                auto_continue_prompt = DEFAULT_AUTO_CONTINUE_PROMPT

            # Store incoming messages (user/system) in the database
            for msg in request.messages:
                if msg.role == "system" and document_context_mode == "one-shot":
                    print("Skipping persistence of one-shot document context system message.")
                    continue

                if msg.role in {"user", "system"}:
                    sender = "user" if msg.role == "user" else "system"
                    db_message = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation.id,
                        sender=sender,
                        message=msg.content,
                        message_metadata={"role": msg.role}
                    )
                    db.add(db_message)
                    print(f"Added {msg.role} message to database: {msg.content[:50]}...")
            
            deterministic_tool_calls: List[Dict[str, Any]] = []
            deterministic_response_content: Optional[str] = None
            deterministic_stage: Optional[str] = None
            deterministic_state_to_store: Optional[Dict[str, Any]] = None
            deterministic_state_key: str = "life_onboarding_deterministic"
            deterministic_orchestration_name: str = "life_onboarding_deterministic"
            deterministic_stop_reason: str = "deterministic_life_onboarding_turn"
            life_topic_for_kickoff = _extract_life_topic(effective_conversation_type)
            latest_user_message = _latest_user_message_text(current_messages)
            normalized_scope_path = _normalize_project_scope_path(mcp_scope.get("mcp_project_slug"))

            if (
                not life_topic_for_kickoff
                and not approval_resume_context
                and isinstance(normalized_scope_path, str)
                and normalized_scope_path not in {"capture", "digest"}
            ):
                runtime_service = MCPRegistryService(
                    db,
                    call_timeout_seconds=mcp_tool_timeout_seconds,
                )
                prior_state: Optional[Dict[str, Any]] = None
                prior_turn = -1

                recent_llm_query = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender == "llm",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(50)
                )
                recent_llm_result = await db.execute(recent_llm_query)
                for candidate in recent_llm_result.scalars().all():
                    candidate_meta = (
                        candidate.message_metadata
                        if isinstance(candidate.message_metadata, dict)
                        else {}
                    )
                    candidate_state = _extract_new_page_interview_state_from_message_metadata(
                        candidate_meta,
                        normalized_scope_path,
                    )
                    if not candidate_state:
                        continue
                    candidate_turn = _as_int(candidate_state.get("turn"), 0, 0, 1_000_000)
                    if candidate_turn >= prior_turn:
                        prior_turn = candidate_turn
                        prior_state = candidate_state

                if prior_state is None:
                    if _is_new_page_interview_kickoff_intent(latest_user_message):
                        seed = await _read_new_page_interview_seed(
                            runtime_service=runtime_service,
                            mcp_user_id=user_id or "current",
                            scope_path=normalized_scope_path,
                            mcp_plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                        )
                        if seed:
                            question_index = _as_int(seed.get("question_index"), 1, 1, 1000)
                            question_total = _as_int(seed.get("question_total"), 1, 1, 1000)
                            questions = seed.get("questions") if isinstance(seed.get("questions"), list) else []
                            question = (
                                str(questions[question_index - 1]).strip()
                                if questions and question_index <= len(questions)
                                else ""
                            )
                            deterministic_stage = "new_page_kickoff"
                            deterministic_state_key = NEW_PAGE_INTERVIEW_STATE_KEY
                            deterministic_orchestration_name = "new_page_interview_deterministic"
                            deterministic_stop_reason = "deterministic_new_page_interview_turn"
                            deterministic_response_content = (
                                f"Starting the {seed.get('page_title')} page interview. "
                                "I will ask one question at a time and evolve your scope files as we go.\n\n"
                                f"Question {question_index} of {question_total}: {question}"
                            )
                            deterministic_state_to_store = {
                                "mode": "deterministic",
                                "scope_path": normalized_scope_path,
                                "page_title": seed.get("page_title"),
                                "page_kind": seed.get("page_kind"),
                                "meta_path": seed.get("meta_path"),
                                "meta_payload": seed.get("meta_payload"),
                                "first_followup_due_utc": seed.get("first_followup_due_utc"),
                                "questions": questions,
                                "question_index": question_index,
                                "question_total": question_total,
                                "question": question,
                                "approved_answers": _as_int(seed.get("approved_answers"), 0, 0, 1000),
                                "awaiting": "answer",
                                "updated_at": _utc_timestamp(),
                                "turn": 1,
                            }
                            deterministic_tool_calls.append(
                                {
                                    "name": "read_markdown",
                                    "status": "success",
                                    "arguments": {"path": seed.get("meta_path")},
                                    "deterministic": True,
                                }
                            )
                else:
                    questions_raw = prior_state.get("questions")
                    questions = (
                        [str(item).strip() for item in questions_raw if isinstance(item, str) and str(item).strip()]
                        if isinstance(questions_raw, list)
                        else []
                    )
                    questions = _dedupe_questions(questions)[:NEW_PAGE_INTERVIEW_MAX_QUESTIONS]
                    question_total = _as_int(prior_state.get("question_total"), len(questions), 1, 1000)
                    if questions and question_total != len(questions):
                        question_total = len(questions)
                    question_index = _as_int(prior_state.get("question_index"), 1, 1, 1000)
                    question_index = min(max(1, question_index), question_total)
                    current_question = str(prior_state.get("question") or "").strip()
                    if questions and (not current_question or question_index <= len(questions)):
                        current_question = questions[question_index - 1]
                    awaiting_state = str(prior_state.get("awaiting") or "").strip().lower()
                    next_turn = _as_int(prior_state.get("turn"), 0, 0, 1_000_000) + 1
                    page_title = str(prior_state.get("page_title") or "").strip() or _title_from_scope_slug(
                        normalized_scope_path.split("/")[-1]
                    )
                    page_kind = str(prior_state.get("page_kind") or "").strip().lower()
                    if page_kind not in {"life", "project"}:
                        page_kind = "life" if normalized_scope_path.startswith("life/") else "project"
                    first_followup_due = str(prior_state.get("first_followup_due_utc") or "").strip()
                    deterministic_state_key = NEW_PAGE_INTERVIEW_STATE_KEY
                    deterministic_orchestration_name = "new_page_interview_deterministic"
                    deterministic_stop_reason = "deterministic_new_page_interview_turn"

                    if awaiting_state == "answer":
                        answer_text = (
                            " ".join(str(latest_user_message or "").split())
                            if isinstance(latest_user_message, str)
                            else ""
                        )
                        resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
                        skip_intent = _is_life_onboarding_skip_intent(latest_user_message)
                        if resume_intent:
                            deterministic_stage = "new_page_awaiting_answer_resume"
                            deterministic_response_content = (
                                f"Resuming your page interview. Question {question_index} of {question_total}: "
                                f"{current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "awaiting": "answer",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif skip_intent:
                            next_index = question_index + 1
                            if questions and next_index <= len(questions):
                                next_question = questions[next_index - 1]
                                deterministic_stage = "new_page_skipped_question_next"
                                deterministic_response_content = (
                                    f"Skipped. Question {next_index} of {len(questions)}: {next_question}"
                                )
                                deterministic_state_to_store = {
                                    **prior_state,
                                    "scope_path": normalized_scope_path,
                                    "awaiting": "answer",
                                    "question_index": next_index,
                                    "question_total": len(questions),
                                    "question": next_question,
                                    "approved_answers": max(
                                        _as_int(prior_state.get("approved_answers"), 0, 0, 1000),
                                        next_index - 1,
                                    ),
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                            else:
                                deterministic_stage = "new_page_interview_complete_skip"
                                deterministic_response_content = (
                                    f"Interview complete for {page_title}. "
                                    f"First follow-up remains due on {first_followup_due or 'the scheduled date'}."
                                )
                                deterministic_state_to_store = {
                                    **prior_state,
                                    "scope_path": normalized_scope_path,
                                    "awaiting": "complete",
                                    "question_index": question_total,
                                    "question_total": question_total,
                                    "approved_answers": question_total,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                        elif not answer_text:
                            deterministic_stage = "new_page_awaiting_answer"
                            deterministic_response_content = (
                                f"Question {question_index} of {question_total}: {current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "awaiting": "answer",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            operations = _build_new_page_interview_edit_operations(
                                scope_path=normalized_scope_path,
                                page_kind=page_kind,
                                question_index=question_index,
                                question_total=question_total,
                                question_text=current_question,
                                answer_text=answer_text,
                            )
                            preview_rows: List[Dict[str, Any]] = []
                            for operation_payload in operations:
                                preview_execution = await _execute_tool_with_resync_fallback(
                                    runtime_service=runtime_service,
                                    mcp_user_id=user_id or "current",
                                    tool_name="preview_markdown_change",
                                    arguments=operation_payload,
                                    plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                )
                                deterministic_tool_calls.append(
                                    {
                                        "name": "preview_markdown_change",
                                        "status": "success" if preview_execution.get("ok") else "error",
                                        "arguments": operation_payload,
                                        "deterministic": True,
                                        "error": None
                                        if preview_execution.get("ok")
                                        else preview_execution.get("error"),
                                    }
                                )
                                if not preview_execution.get("ok"):
                                    continue
                                preview_payload = _build_markdown_preview_payload(
                                    preview_execution.get("data")
                                )
                                summary = _extract_preview_summary(
                                    preview_payload or {},
                                    fallback="Update pending.",
                                )
                                preview_rows.append(
                                    {
                                        "path": operation_payload.get("path"),
                                        "summary": summary,
                                    }
                                )

                            preview_lines = "\n".join(
                                f"- `{str(item.get('path') or '').strip()}`: {str(item.get('summary') or '').strip()}"
                                for item in preview_rows[:NEW_PAGE_INTERVIEW_PREVIEW_MAX_ITEMS]
                                if str(item.get("path") or "").strip()
                            )
                            deterministic_stage = "new_page_awaiting_approval"
                            deterministic_response_content = (
                                "I prepared scoped updates from your answer:\n"
                                f"{preview_lines}\n\n"
                                f"{NEW_PAGE_INTERVIEW_APPROVAL_TEXT}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "page_title": page_title,
                                "page_kind": page_kind,
                                "questions": questions,
                                "question_index": question_index,
                                "question_total": question_total,
                                "question": current_question,
                                "awaiting": "approval",
                                "pending_answer": answer_text,
                                "pending_operations": operations,
                                "pending_preview_rows": preview_rows,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "approval":
                        pending_answer = str(prior_state.get("pending_answer") or "").strip()
                        pending_operations = (
                            prior_state.get("pending_operations")
                            if isinstance(prior_state.get("pending_operations"), list)
                            else []
                        )
                        approval_action = _parse_chat_approval_action(latest_user_message)
                        resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
                        if approval_action == "reject":
                            deterministic_stage = "new_page_approval_rejected"
                            deterministic_response_content = (
                                "No problem. Share a revised answer and I will recalculate the scoped updates."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "awaiting": "answer",
                                "pending_answer": None,
                                "pending_operations": [],
                                "pending_preview_rows": [],
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif approval_action == "approve" and pending_answer and pending_operations:
                            success_count = 0
                            for operation_payload in pending_operations:
                                if not isinstance(operation_payload, dict):
                                    continue
                                execution = await _execute_tool_with_resync_fallback(
                                    runtime_service=runtime_service,
                                    mcp_user_id=user_id or "current",
                                    tool_name="edit_markdown",
                                    arguments=operation_payload,
                                    plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                )
                                deterministic_tool_calls.append(
                                    {
                                        "name": "edit_markdown",
                                        "status": "success" if execution.get("ok") else "error",
                                        "arguments": operation_payload,
                                        "deterministic": True,
                                        "error": None if execution.get("ok") else execution.get("error"),
                                    }
                                )
                                if execution.get("ok"):
                                    success_count += 1

                            if success_count == len(pending_operations):
                                approved_answers = max(
                                    _as_int(prior_state.get("approved_answers"), 0, 0, 1000),
                                    question_index,
                                )
                                meta_payload = (
                                    copy.deepcopy(prior_state.get("meta_payload"))
                                    if isinstance(prior_state.get("meta_payload"), dict)
                                    else {}
                                )
                                meta_status = (
                                    meta_payload.get("status")
                                    if isinstance(meta_payload.get("status"), dict)
                                    else {}
                                )
                                meta_status.update(
                                    {
                                        "interview_stage": (
                                            "complete"
                                            if approved_answers >= question_total
                                            else "in_progress"
                                        ),
                                        "question_total": question_total,
                                        "approved_answers": approved_answers,
                                    }
                                )
                                meta_payload["status"] = meta_status
                                meta_payload["last_interview_at_utc"] = _utc_timestamp()
                                meta_path = str(prior_state.get("meta_path") or "").strip()
                                if not meta_path:
                                    meta_path = f"{normalized_scope_path}/_meta/interview-state.md"
                                write_meta_result = await _execute_tool_with_resync_fallback(
                                    runtime_service=runtime_service,
                                    mcp_user_id=user_id or "current",
                                    tool_name="write_markdown",
                                    arguments={
                                        "path": meta_path,
                                        "content": json.dumps(meta_payload, ensure_ascii=True, indent=2),
                                    },
                                    plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                )
                                deterministic_tool_calls.append(
                                    {
                                        "name": "write_markdown",
                                        "status": "success" if write_meta_result.get("ok") else "error",
                                        "arguments": {"path": meta_path},
                                        "deterministic": True,
                                        "error": None
                                        if write_meta_result.get("ok")
                                        else write_meta_result.get("error"),
                                    }
                                )

                                next_index = question_index + 1
                                if questions and next_index <= len(questions):
                                    next_question = questions[next_index - 1]
                                    deterministic_stage = "new_page_next_question"
                                    deterministic_response_content = (
                                        f"Saved scoped updates. Question {next_index} of {len(questions)}: "
                                        f"{next_question}"
                                    )
                                    deterministic_state_to_store = {
                                        **prior_state,
                                        "scope_path": normalized_scope_path,
                                        "page_title": page_title,
                                        "page_kind": page_kind,
                                        "meta_path": meta_path,
                                        "meta_payload": meta_payload,
                                        "questions": questions,
                                        "question_index": next_index,
                                        "question_total": len(questions),
                                        "question": next_question,
                                        "approved_answers": approved_answers,
                                        "awaiting": "answer",
                                        "pending_answer": None,
                                        "pending_operations": [],
                                        "pending_preview_rows": [],
                                        "updated_at": _utc_timestamp(),
                                        "turn": next_turn,
                                    }
                                else:
                                    deterministic_stage = "new_page_interview_complete"
                                    deterministic_response_content = (
                                        f"Saved scoped updates. {page_title} interview is complete."
                                        + (
                                            f" First follow-up is due on {first_followup_due}."
                                            if first_followup_due
                                            else ""
                                        )
                                    )
                                    deterministic_state_to_store = {
                                        **prior_state,
                                        "scope_path": normalized_scope_path,
                                        "page_title": page_title,
                                        "page_kind": page_kind,
                                        "meta_path": meta_path,
                                        "meta_payload": meta_payload,
                                        "questions": questions,
                                        "question_index": question_total,
                                        "question_total": question_total,
                                        "question": current_question,
                                        "approved_answers": approved_answers,
                                        "awaiting": "complete",
                                        "pending_answer": None,
                                        "pending_operations": [],
                                        "pending_preview_rows": [],
                                        "updated_at": _utc_timestamp(),
                                        "turn": next_turn,
                                    }
                            else:
                                deterministic_stage = "new_page_approval_retry"
                                deterministic_response_content = (
                                    "I could not apply all scoped updates yet. "
                                    "Reply `approve` to retry, or `reject` to revise your answer."
                                )
                                deterministic_state_to_store = {
                                    **prior_state,
                                    "scope_path": normalized_scope_path,
                                    "awaiting": "approval",
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                        elif resume_intent:
                            preview_rows = (
                                prior_state.get("pending_preview_rows")
                                if isinstance(prior_state.get("pending_preview_rows"), list)
                                else []
                            )
                            preview_lines = "\n".join(
                                f"- `{str(item.get('path') or '').strip()}`: {str(item.get('summary') or '').strip()}"
                                for item in preview_rows[:NEW_PAGE_INTERVIEW_PREVIEW_MAX_ITEMS]
                                if isinstance(item, dict) and str(item.get("path") or "").strip()
                            )
                            deterministic_stage = "new_page_awaiting_approval_resume"
                            deterministic_response_content = (
                                "You still have pending scoped updates:\n"
                                f"{preview_lines}\n\n"
                                f"{NEW_PAGE_INTERVIEW_APPROVAL_TEXT}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "awaiting": "approval",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            deterministic_stage = "new_page_awaiting_approval"
                            deterministic_response_content = NEW_PAGE_INTERVIEW_APPROVAL_TEXT
                            deterministic_state_to_store = {
                                **prior_state,
                                "scope_path": normalized_scope_path,
                                "awaiting": "approval",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "complete" and _is_new_page_interview_intent(latest_user_message):
                        deterministic_stage = "new_page_already_complete"
                        deterministic_response_content = (
                            f"{page_title} interview is already complete."
                            + (
                                f" First follow-up is due on {first_followup_due}."
                                if first_followup_due
                                else ""
                            )
                        )
                        deterministic_state_to_store = {
                            **prior_state,
                            "scope_path": normalized_scope_path,
                            "awaiting": "complete",
                            "updated_at": _utc_timestamp(),
                            "turn": next_turn,
                        }

            if (
                life_topic_for_kickoff
                and not approval_resume_context
                and deterministic_response_content is None
            ):
                runtime_service = MCPRegistryService(
                    db,
                    call_timeout_seconds=mcp_tool_timeout_seconds,
                )
                prior_state: Optional[Dict[str, Any]] = None
                prior_turn = -1

                recent_llm_query = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender == "llm",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(50)
                )
                recent_llm_result = await db.execute(recent_llm_query)
                for candidate in recent_llm_result.scalars().all():
                    candidate_meta = (
                        candidate.message_metadata
                        if isinstance(candidate.message_metadata, dict)
                        else {}
                    )
                    candidate_state = _extract_life_onboarding_state_from_message_metadata(
                        candidate_meta,
                        life_topic_for_kickoff,
                    )
                    if not candidate_state:
                        continue

                    candidate_turn = _as_int(candidate_state.get("turn"), 0, 0, 1_000_000)
                    if candidate_turn >= prior_turn:
                        prior_turn = candidate_turn
                        prior_state = candidate_state

                if prior_state is None:
                    onboarding_topic_status = (
                        orchestration_context_payload.get("onboarding_topic_status")
                        if isinstance(orchestration_context_payload, dict)
                        else None
                    )
                    resume_question_index = _derive_life_onboarding_resume_index_from_context(
                        orchestration_context=orchestration_context_payload,
                        life_topic=life_topic_for_kickoff,
                    )
                    resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
                    auto_start_first_visit = (
                        bool(conversation_was_created)
                        and not _is_life_onboarding_topic_complete(onboarding_topic_status)
                    )
                    life_onboarding_kickoff = await _build_life_onboarding_kickoff_fallback(
                        life_topic=life_topic_for_kickoff,
                        latest_user_message=latest_user_message,
                        runtime_service=runtime_service,
                        mcp_user_id=user_id or "current",
                        mcp_plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                        auto_start=auto_start_first_visit,
                        start_question_index=(
                            resume_question_index
                            if (auto_start_first_visit or resume_intent)
                            else None
                        ),
                    )
                    if life_onboarding_kickoff:
                        deterministic_stage = (
                            "kickoff_auto_first_visit"
                            if life_onboarding_kickoff.get("kickoff_mode") == "auto_first_visit"
                            else "kickoff"
                        )
                        deterministic_response_content = life_onboarding_kickoff["response_text"]
                        deterministic_state_to_store = {
                            "mode": "deterministic",
                            "topic": life_onboarding_kickoff["topic"],
                            "phase": "opening",
                            "awaiting": "answer",
                            "question_index": life_onboarding_kickoff["question_index"],
                            "question_total": life_onboarding_kickoff["question_total"],
                            "question": life_onboarding_kickoff["question"],
                            "questions": life_onboarding_kickoff.get("questions", []),
                            "approved_turns": [],
                            "updated_at": _utc_timestamp(),
                            "turn": 1,
                        }
                        deterministic_tool_calls.append(
                            {
                                "name": "read_markdown",
                                "status": "success",
                                "arguments": {
                                    "path": life_onboarding_kickoff["source_path"],
                                },
                                "deterministic": True,
                            }
                        )
                else:
                    topic_title = LIFE_ONBOARDING_TOPICS.get(
                        life_topic_for_kickoff,
                        life_topic_for_kickoff.title(),
                    )
                    opening_questions = _extract_opening_questions_from_state(prior_state)
                    if not opening_questions:
                        regenerated = await _read_life_agent_questions(
                            runtime_service=runtime_service,
                            mcp_user_id=user_id or "current",
                            life_topic=life_topic_for_kickoff,
                            mcp_plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                        )
                        if not regenerated:
                            regenerated = await _read_life_seed_questions(
                                runtime_service=runtime_service,
                                mcp_user_id=user_id or "current",
                                life_topic=life_topic_for_kickoff,
                                mcp_plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                            )
                        if regenerated:
                            opening_questions = regenerated.get("questions", [])
                            deterministic_tool_calls.append(
                                {
                                    "name": "read_markdown",
                                    "status": "success",
                                    "arguments": {"path": regenerated["source_path"]},
                                    "deterministic": True,
                                }
                            )

                    total_questions = len(opening_questions)
                    if total_questions <= 0:
                        total_questions = _as_int(prior_state.get("question_total"), 1, 1, 1000)
                    current_index = _as_int(prior_state.get("question_index"), 1, 1, 1000)
                    if current_index > total_questions and total_questions > 0:
                        current_index = total_questions
                    current_question = str(prior_state.get("question") or "").strip()
                    if opening_questions and (not current_question or current_index <= len(opening_questions)):
                        current_question = opening_questions[max(0, current_index - 1)]

                    awaiting_state = str(prior_state.get("awaiting") or "").strip().lower()
                    next_turn = _as_int(prior_state.get("turn"), 0, 0, 1_000_000) + 1
                    followup_due_iso = (
                        datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
                    ).isoformat()
                    approved_turns = (
                        prior_state.get("approved_turns")
                        if isinstance(prior_state.get("approved_turns"), list)
                        else []
                    )

                    if awaiting_state == "answer":
                        answer_text = latest_user_message.strip() if isinstance(latest_user_message, str) else ""
                        skip_intent = _is_life_onboarding_skip_intent(latest_user_message)
                        resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
                        if resume_intent:
                            deterministic_stage = "awaiting_answer_resume"
                            deterministic_response_content = (
                                "Resuming your onboarding interview. "
                                f"Question {current_index} of {total_questions}: {current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "answer",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif skip_intent:
                            next_index = current_index + 1
                            if opening_questions and next_index <= len(opening_questions):
                                next_question = opening_questions[next_index - 1]
                                deterministic_stage = "skipped_question_next"
                                deterministic_response_content = (
                                    f"Skipped. Question {next_index} of {len(opening_questions)}: {next_question}"
                                )
                                deterministic_state_to_store = {
                                    "mode": "deterministic",
                                    "topic": life_topic_for_kickoff,
                                    "phase": "opening",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "answer",
                                    "question_index": next_index,
                                    "question_total": len(opening_questions),
                                    "question": next_question,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                            else:
                                deterministic_stage = "goals_tasks_choice_prompt"
                                deterministic_response_content = (
                                    "Skipped. Your opening interview is complete. "
                                    "Do you want to add initial goals or tasks for this topic now? "
                                    "Reply `yes` or `no`."
                                )
                                deterministic_state_to_store = {
                                    "mode": "deterministic",
                                    "topic": life_topic_for_kickoff,
                                    "phase": "goals_tasks",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "goals_tasks_choice",
                                    "question_index": total_questions,
                                    "question_total": total_questions,
                                    "question": current_question,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                        elif not answer_text:
                            deterministic_stage = "awaiting_answer"
                            deterministic_response_content = (
                                f"Question {current_index} of {total_questions}: {current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "answer",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            deterministic_stage = "awaiting_approval"
                            deterministic_response_content = (
                                "Thanks. I captured your answer. "
                                "Reply `approve` to save it and continue, or `reject` to revise it."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "approval",
                                "pending_answer": answer_text,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "approval":
                        pending_answer = str(prior_state.get("pending_answer") or "").strip()
                        approval_action = _parse_chat_approval_action(latest_user_message)
                        skip_intent = _is_life_onboarding_skip_intent(latest_user_message)
                        resume_intent = _is_life_onboarding_resume_intent(latest_user_message)

                        if not pending_answer:
                            deterministic_stage = "awaiting_answer"
                            deterministic_response_content = (
                                "I do not have your answer saved yet. "
                                f"Question {current_index} of {total_questions}: {current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "answer",
                                "pending_answer": None,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif skip_intent:
                            next_index = current_index + 1
                            if opening_questions and next_index <= len(opening_questions):
                                next_question = opening_questions[next_index - 1]
                                deterministic_stage = "approval_skip_next_question"
                                deterministic_response_content = (
                                    "Skipped this pending answer. "
                                    f"Question {next_index} of {len(opening_questions)}: {next_question}"
                                )
                                deterministic_state_to_store = {
                                    "mode": "deterministic",
                                    "topic": life_topic_for_kickoff,
                                    "phase": "opening",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "answer",
                                    "question_index": next_index,
                                    "question_total": len(opening_questions),
                                    "question": next_question,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                            else:
                                deterministic_stage = "goals_tasks_choice_prompt"
                                deterministic_response_content = (
                                    "Skipped this pending answer. Your opening interview is complete. "
                                    "Do you want to add initial goals or tasks for this topic now? "
                                    "Reply `yes` or `no`."
                                )
                                deterministic_state_to_store = {
                                    "mode": "deterministic",
                                    "topic": life_topic_for_kickoff,
                                    "phase": "goals_tasks",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "goals_tasks_choice",
                                    "question_index": total_questions,
                                    "question_total": total_questions,
                                    "question": current_question,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                        elif approval_action == "reject":
                            deterministic_stage = "awaiting_answer"
                            deterministic_response_content = (
                                "No problem. Let us revise it. "
                                f"Question {current_index} of {total_questions}: {current_question}"
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "answer",
                                "pending_answer": None,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif approval_action == "approve":
                            save_payload = {
                                "topic": life_topic_for_kickoff,
                                "question": current_question,
                                "answer": pending_answer,
                                "context": pending_answer,
                                "approved": True,
                                "phase": "opening",
                                "question_index": current_index,
                                "question_total": total_questions,
                            }
                            save_result = await _execute_tool_with_resync_fallback(
                                runtime_service=runtime_service,
                                mcp_user_id=user_id or "current",
                                tool_name="save_topic_onboarding_context",
                                arguments=save_payload,
                                plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                            )
                            deterministic_tool_calls.append(
                                {
                                    "name": "save_topic_onboarding_context",
                                    "status": "success" if save_result.get("ok") else "error",
                                    "arguments": save_payload,
                                    "deterministic": True,
                                    "error": None if save_result.get("ok") else save_result.get("error"),
                                }
                            )

                            if not save_result.get("ok"):
                                deterministic_stage = "awaiting_approval"
                                deterministic_response_content = (
                                    "I could not save that answer yet. "
                                    "Reply `approve` to retry save, or `reject` to revise."
                                )
                                deterministic_state_to_store = {
                                    **prior_state,
                                    "phase": "opening",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "approval",
                                    "pending_answer": pending_answer,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                            else:
                                approved_turns_next = [
                                    *approved_turns,
                                    {
                                        "question": current_question,
                                        "answer": pending_answer,
                                        "approved_at_utc": _utc_timestamp(),
                                    },
                                ]
                                next_index = current_index + 1
                                if opening_questions and next_index <= len(opening_questions):
                                    next_question = opening_questions[next_index - 1]
                                    deterministic_stage = "next_question"
                                    deterministic_response_content = (
                                        f"Saved to your library. Question {next_index} of {len(opening_questions)}: "
                                        f"{next_question}"
                                    )
                                    deterministic_state_to_store = {
                                        "mode": "deterministic",
                                        "topic": life_topic_for_kickoff,
                                        "phase": "opening",
                                        "questions": opening_questions,
                                        "approved_turns": approved_turns_next,
                                        "awaiting": "answer",
                                        "question_index": next_index,
                                        "question_total": len(opening_questions),
                                        "question": next_question,
                                        "updated_at": _utc_timestamp(),
                                        "turn": next_turn,
                                    }
                                else:
                                    deterministic_stage = "goals_tasks_choice_prompt"
                                    deterministic_response_content = (
                                        "Saved. Your opening interview is complete. "
                                        "Do you want to add initial goals or tasks for this topic now? "
                                        "Reply `yes` or `no`."
                                    )
                                    deterministic_state_to_store = {
                                        "mode": "deterministic",
                                        "topic": life_topic_for_kickoff,
                                        "phase": "goals_tasks",
                                        "questions": opening_questions,
                                        "approved_turns": approved_turns_next,
                                        "awaiting": "goals_tasks_choice",
                                        "question_index": total_questions,
                                        "question_total": total_questions,
                                        "question": current_question,
                                        "updated_at": _utc_timestamp(),
                                        "turn": next_turn,
                                    }
                        elif resume_intent:
                            deterministic_stage = "awaiting_approval_resume"
                            deterministic_response_content = (
                                f"You still have a pending answer for question {current_index}. "
                                "Reply `approve` to save it and continue, "
                                "`reject` to revise, or `skip` to move on without saving."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "approval",
                                "pending_answer": pending_answer,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            deterministic_stage = "awaiting_approval"
                            deterministic_response_content = (
                                "Reply `approve` to save this answer and continue, "
                                "`reject` to revise it, or `skip` to move on without saving."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "opening",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "approval",
                                "pending_answer": pending_answer,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "goals_tasks_choice":
                        choice = _parse_yes_no(latest_user_message)
                        if choice is None:
                            deterministic_stage = "awaiting_goals_tasks_choice"
                            deterministic_response_content = (
                                "Reply `yes` to add initial goals/tasks now, "
                                "or `no` to finish onboarding for now."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_choice",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif choice is False:
                            completion_summary = (
                                f"Completed {topic_title} onboarding interview with approved answers."
                            )
                            complete_payload = {
                                "topic": life_topic_for_kickoff,
                                "summary": completion_summary,
                                "next_followup_due_at_utc": followup_due_iso,
                            }
                            complete_result = await _execute_tool_with_resync_fallback(
                                runtime_service=runtime_service,
                                mcp_user_id=user_id or "current",
                                tool_name="complete_topic_onboarding",
                                arguments=complete_payload,
                                plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                            )
                            deterministic_tool_calls.append(
                                {
                                    "name": "complete_topic_onboarding",
                                    "status": "success" if complete_result.get("ok") else "error",
                                    "arguments": complete_payload,
                                    "deterministic": True,
                                    "error": None if complete_result.get("ok") else complete_result.get("error"),
                                }
                            )
                            followup_task_payload = _build_onboarding_followup_task_payload(
                                topic_slug=life_topic_for_kickoff,
                                topic_title=topic_title,
                                approved_turns=approved_turns,
                            )
                            followup_task_result = (
                                await _execute_tool_with_resync_fallback(
                                    runtime_service=runtime_service,
                                    mcp_user_id=user_id or "current",
                                    tool_name="create_task",
                                    arguments=followup_task_payload,
                                    plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                )
                                if complete_result.get("ok")
                                else {"ok": False, "error": {"code": "SKIPPED_COMPLETE_FAILED"}}
                            )
                            deterministic_tool_calls.append(
                                {
                                    "name": "create_task",
                                    "status": "success" if followup_task_result.get("ok") else "error",
                                    "arguments": followup_task_payload,
                                    "deterministic": True,
                                    "error": None
                                    if followup_task_result.get("ok")
                                    else followup_task_result.get("error"),
                                }
                            )
                            deterministic_stage = "completed"
                            if complete_result.get("ok") and followup_task_result.get("ok"):
                                deterministic_response_content = (
                                    f"Saved. {topic_title} onboarding is complete. "
                                    f"I also scheduled a follow-up task for {followup_task_payload['due']}."
                                )
                            elif complete_result.get("ok"):
                                deterministic_response_content = (
                                    f"Saved. {topic_title} onboarding is complete, "
                                    "but I could not queue the follow-up task automatically."
                                )
                            else:
                                deterministic_response_content = (
                                    "I could not mark onboarding complete automatically yet."
                                )
                            deterministic_state_to_store = {
                                "mode": "deterministic",
                                "topic": life_topic_for_kickoff,
                                "phase": "complete",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "complete",
                                "question_index": total_questions,
                                "question_total": total_questions,
                                "question": current_question,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            deterministic_stage = "collect_goals_tasks"
                            deterministic_response_content = (
                                "Great. Share your initial goals or tasks for this topic. "
                                "If you mention relative dates like `next Friday`, "
                                "I will convert them to exact dates before save."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_details",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "goals_tasks_details":
                        details = latest_user_message.strip() if isinstance(latest_user_message, str) else ""
                        if not details:
                            deterministic_stage = "awaiting_goals_tasks_details"
                            deterministic_response_content = (
                                "Please share at least one initial goal or task for this topic."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_details",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        else:
                            normalized_details, date_resolutions = _normalize_relative_dates_in_text(details)
                            date_note = ""
                            if date_resolutions:
                                rendered = ", ".join(
                                    f"{item['phrase']} -> {item['resolved_date']}"
                                    for item in date_resolutions
                                )
                                date_note = f" Resolved dates: {rendered}."
                            deterministic_stage = "awaiting_goals_tasks_approval"
                            deterministic_response_content = (
                                "Thanks. I captured your goals/tasks."
                                f"{date_note} Reply `approve` to save, or `reject` to revise."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_approval",
                                "pending_goals_tasks_raw": details,
                                "pending_goals_tasks_normalized": normalized_details,
                                "pending_date_resolutions": date_resolutions,
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "goals_tasks_approval":
                        pending_raw = str(prior_state.get("pending_goals_tasks_raw") or "").strip()
                        pending_normalized = str(
                            prior_state.get("pending_goals_tasks_normalized") or pending_raw
                        ).strip()
                        approval_action = _parse_chat_approval_action(latest_user_message)

                        if not pending_raw:
                            deterministic_stage = "collect_goals_tasks"
                            deterministic_response_content = (
                                "I do not have goals/tasks captured yet. "
                                "Please share them now."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_details",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif approval_action == "reject":
                            deterministic_stage = "collect_goals_tasks"
                            deterministic_response_content = (
                                "No problem. Share the revised goals/tasks and I will recapture them."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_details",
                                "pending_goals_tasks_raw": None,
                                "pending_goals_tasks_normalized": None,
                                "pending_date_resolutions": [],
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                        elif approval_action == "approve":
                            save_payload = {
                                "topic": life_topic_for_kickoff,
                                "question": "What initial goals or tasks do you want to set now?",
                                "answer": pending_raw,
                                "context": pending_normalized,
                                "approved": True,
                                "phase": "goals_tasks",
                                "question_index": total_questions,
                                "question_total": total_questions,
                                "next_followup_due_at_utc": followup_due_iso,
                            }
                            save_result = await _execute_tool_with_resync_fallback(
                                runtime_service=runtime_service,
                                mcp_user_id=user_id or "current",
                                tool_name="save_topic_onboarding_context",
                                arguments=save_payload,
                                plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                            )
                            deterministic_tool_calls.append(
                                {
                                    "name": "save_topic_onboarding_context",
                                    "status": "success" if save_result.get("ok") else "error",
                                    "arguments": save_payload,
                                    "deterministic": True,
                                    "error": None if save_result.get("ok") else save_result.get("error"),
                                }
                            )

                            if not save_result.get("ok"):
                                deterministic_stage = "awaiting_goals_tasks_approval"
                                deterministic_response_content = (
                                    "I could not save those goals/tasks yet. "
                                    "Reply `approve` to retry, or `reject` to revise."
                                )
                                deterministic_state_to_store = {
                                    **prior_state,
                                    "phase": "goals_tasks",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns,
                                    "awaiting": "goals_tasks_approval",
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                            else:
                                approved_turns_with_goals = [
                                    *approved_turns,
                                    {
                                        "question": "What initial goals or tasks do you want to set now?",
                                        "answer": pending_normalized,
                                        "approved_at_utc": _utc_timestamp(),
                                    },
                                ]
                                completion_summary = (
                                    f"Completed {topic_title} onboarding interview with approved answers and "
                                    "initial goals/tasks."
                                )
                                complete_payload = {
                                    "topic": life_topic_for_kickoff,
                                    "summary": completion_summary,
                                    "next_followup_due_at_utc": followup_due_iso,
                                }
                                complete_result = await _execute_tool_with_resync_fallback(
                                    runtime_service=runtime_service,
                                    mcp_user_id=user_id or "current",
                                    tool_name="complete_topic_onboarding",
                                    arguments=complete_payload,
                                    plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                )
                                deterministic_tool_calls.append(
                                    {
                                        "name": "complete_topic_onboarding",
                                        "status": "success" if complete_result.get("ok") else "error",
                                        "arguments": complete_payload,
                                        "deterministic": True,
                                        "error": None if complete_result.get("ok") else complete_result.get("error"),
                                    }
                                )
                                initial_task_payloads = _build_onboarding_initial_task_payloads(
                                    topic_slug=life_topic_for_kickoff,
                                    topic_title=topic_title,
                                    goals_tasks_text=pending_normalized,
                                )
                                initial_task_total = len(initial_task_payloads)
                                initial_task_success = 0
                                if complete_result.get("ok"):
                                    for initial_task_payload in initial_task_payloads:
                                        initial_task_result = await _execute_tool_with_resync_fallback(
                                            runtime_service=runtime_service,
                                            mcp_user_id=user_id or "current",
                                            tool_name="create_task",
                                            arguments=initial_task_payload,
                                            plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                        )
                                        deterministic_tool_calls.append(
                                            {
                                                "name": "create_task",
                                                "status": "success"
                                                if initial_task_result.get("ok")
                                                else "error",
                                                "arguments": initial_task_payload,
                                                "deterministic": True,
                                                "error": None
                                                if initial_task_result.get("ok")
                                                else initial_task_result.get("error"),
                                            }
                                        )
                                        if initial_task_result.get("ok"):
                                            initial_task_success += 1
                                followup_task_payload = _build_onboarding_followup_task_payload(
                                    topic_slug=life_topic_for_kickoff,
                                    topic_title=topic_title,
                                    approved_turns=approved_turns_with_goals,
                                )
                                followup_task_result = (
                                    await _execute_tool_with_resync_fallback(
                                        runtime_service=runtime_service,
                                        mcp_user_id=user_id or "current",
                                        tool_name="create_task",
                                        arguments=followup_task_payload,
                                        plugin_slug_hint=mcp_scope.get("mcp_plugin_slug"),
                                    )
                                    if complete_result.get("ok")
                                    else {"ok": False, "error": {"code": "SKIPPED_COMPLETE_FAILED"}}
                                )
                                deterministic_tool_calls.append(
                                    {
                                        "name": "create_task",
                                        "status": "success" if followup_task_result.get("ok") else "error",
                                        "arguments": followup_task_payload,
                                        "deterministic": True,
                                        "error": None
                                        if followup_task_result.get("ok")
                                        else followup_task_result.get("error"),
                                    }
                                )
                                deterministic_stage = "completed"
                                if (
                                    complete_result.get("ok")
                                    and followup_task_result.get("ok")
                                    and initial_task_success == initial_task_total
                                ):
                                    if initial_task_total > 0:
                                        task_word = "task" if initial_task_success == 1 else "tasks"
                                        deterministic_response_content = (
                                            f"Saved. {topic_title} onboarding is complete. "
                                            f"I added {initial_task_success} onboarding {task_word} and scheduled a "
                                            f"follow-up task for {followup_task_payload['due']}."
                                        )
                                    else:
                                        deterministic_response_content = (
                                            f"Saved. {topic_title} onboarding is complete. "
                                            f"I also scheduled a follow-up task for {followup_task_payload['due']}."
                                        )
                                elif complete_result.get("ok"):
                                    deterministic_response_content = (
                                        f"Saved. {topic_title} onboarding is complete, "
                                        "but I could not queue all tasks automatically."
                                    )
                                else:
                                    deterministic_response_content = (
                                        "Saved your goals/tasks, but I could not mark onboarding complete automatically."
                                    )
                                deterministic_state_to_store = {
                                    "mode": "deterministic",
                                    "topic": life_topic_for_kickoff,
                                    "phase": "complete",
                                    "questions": opening_questions,
                                    "approved_turns": approved_turns_with_goals,
                                    "awaiting": "complete",
                                    "question_index": total_questions,
                                    "question_total": total_questions,
                                    "question": current_question,
                                    "updated_at": _utc_timestamp(),
                                    "turn": next_turn,
                                }
                        else:
                            deterministic_stage = "awaiting_goals_tasks_approval"
                            deterministic_response_content = (
                                "Reply `approve` to save these goals/tasks, or `reject` to revise."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "goals_tasks",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "goals_tasks_approval",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }
                    elif awaiting_state == "complete":
                        resume_intent = _is_life_onboarding_resume_intent(latest_user_message)
                        kickoff_intent = _is_life_onboarding_kickoff_intent(latest_user_message)
                        if resume_intent or kickoff_intent:
                            deterministic_stage = "already_complete"
                            deterministic_response_content = (
                                f"{topic_title} onboarding is already complete. "
                                "You can ask me to review your tasks or capture updates anytime."
                            )
                            deterministic_state_to_store = {
                                **prior_state,
                                "phase": "complete",
                                "questions": opening_questions,
                                "approved_turns": approved_turns,
                                "awaiting": "complete",
                                "updated_at": _utc_timestamp(),
                                "turn": next_turn,
                            }

                    else:
                        deterministic_stage = "awaiting_answer"
                        deterministic_response_content = (
                            f"Question {current_index} of {total_questions}: {current_question}"
                        )
                        deterministic_state_to_store = {
                            **prior_state,
                            "phase": prior_state.get("phase", "opening"),
                            "questions": opening_questions,
                            "approved_turns": approved_turns,
                            "awaiting": "answer",
                            "updated_at": _utc_timestamp(),
                            "turn": next_turn,
                        }

            if deterministic_response_content and deterministic_state_to_store:
                awaiting_value = str(deterministic_state_to_store.get("awaiting") or "").strip().lower()
                approval_pending = awaiting_value in {"approval", "goals_tasks_approval"}
                question_index = _as_int(deterministic_state_to_store.get("question_index"), 0, 0, 1000)
                question_total = _as_int(deterministic_state_to_store.get("question_total"), 0, 0, 1000)

                deterministic_metadata: Dict[str, Any] = {
                    "conversation_orchestration": deterministic_orchestration_name,
                    "tool_loop_enabled": True,
                    "tool_loop_iterations": max(1, len(deterministic_tool_calls)),
                    "tool_loop_stop_reason": deterministic_stop_reason,
                    "tool_calls_executed_count": len(deterministic_tool_calls),
                    "approval_required": approval_pending,
                    "approval_resolved": False,
                }
                if deterministic_state_key == NEW_PAGE_INTERVIEW_STATE_KEY:
                    deterministic_metadata.update(
                        {
                            "new_page_interview_stage": deterministic_stage,
                            "new_page_interview_scope": deterministic_state_to_store.get("scope_path"),
                            "new_page_interview_question_index": question_index,
                            "new_page_interview_question_total": question_total,
                        }
                    )
                else:
                    deterministic_metadata.update(
                        {
                            "life_onboarding_stage": deterministic_stage,
                            "life_onboarding_topic": deterministic_state_to_store.get("topic"),
                            "life_onboarding_question_index": question_index,
                            "life_onboarding_question_total": question_total,
                        }
                    )
                mcp_tooling_metadata.update(deterministic_metadata)

                estimated_token_count = max(1, int(len(deterministic_response_content.split()) * 1.3))
                message_metadata = {
                    "token_count": estimated_token_count,
                    "tokens_per_second": None,
                    "model": request.model,
                    "temperature": provider_params.get("temperature", 0.7),
                    "streaming": bool(request.stream),
                    "mcp": {
                        **mcp_tooling_metadata,
                        "tools_passed_count": len(resolved_tools),
                        "tool_calls_executed": deterministic_tool_calls,
                        deterministic_state_key: deterministic_state_to_store,
                    },
                }
                db_message = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation.id,
                    sender="llm",
                    message=deterministic_response_content,
                    message_metadata=message_metadata,
                )
                db.add(db_message)
                conversation.updated_at = db_message.created_at
                await db.commit()

                if request.stream:
                    async def deterministic_stream_generator():
                        initial_evt = {
                            "type": "conversation",
                            "conversation_id": conversation.id,
                        }
                        yield f"data: {json.dumps(initial_evt)}\n\n"

                        tooling_evt = {
                            "type": "tooling_state",
                            **mcp_tooling_metadata,
                            "tools_passed_count": len(resolved_tools),
                        }
                        yield f"data: {json.dumps(tooling_evt)}\n\n"

                        if (
                            str(mcp_scope.get("mcp_scope_mode")) == "project"
                            and mcp_scope.get("mcp_project_slug")
                        ):
                            scope_evt = {
                                "type": "project_scope_selected",
                                "project": {
                                    "slug": mcp_scope.get("mcp_project_slug"),
                                    "name": mcp_scope.get("mcp_project_name"),
                                    "lifecycle": mcp_scope.get("mcp_project_lifecycle"),
                                },
                                "source": mcp_scope.get("mcp_project_source"),
                            }
                            yield f"data: {json.dumps(scope_evt)}\n\n"

                        response_chunk = {
                            "choices": [
                                {
                                    "delta": {
                                        "role": "assistant",
                                        "content": deterministic_response_content,
                                    },
                                    "finish_reason": "stop",
                                }
                            ]
                        }
                        yield f"data: {json.dumps(response_chunk)}\n\n"
                        yield "data: [DONE]\n\n"

                    headers = {
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream",
                    }
                    return StreamingResponse(
                        deterministic_stream_generator(),
                        media_type="text/event-stream",
                        headers=headers,
                    )

                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": deterministic_response_content,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "conversation_id": conversation.id,
                    "tooling_state": {
                        **mcp_tooling_metadata,
                        "tools_passed_count": len(resolved_tools),
                    },
                }

            # Handle streaming
            if request.stream:
                async def stream_generator():
                    try:
                        print(f"Starting streaming with model: {request.model}")
                        full_response = ""
                        token_count = 0
                        start_time = time.time()
                        finish_reason_history: List[str] = []
                        finish_reason_final: Optional[str] = None
                        auto_continue_attempts = 0
                        truncated_at_least_once = False
                        stopped_by_guardrail: Optional[str] = None
                        provider_call_latencies_ms: List[int] = []
                        provider_timeout_count = 0

                        # Emit the conversation_id early so clients can persist context
                        try:
                            initial_evt = {
                                "type": "conversation",
                                "conversation_id": conversation.id,
                            }
                            yield f"data: {json.dumps(initial_evt)}\n\n"
                        except Exception as init_evt_error:
                            # Don't fail the stream if the initial event fails
                            print(f"Warning: failed to emit initial conversation_id event: {init_evt_error}")

                        try:
                            tooling_evt = {
                                "type": "tooling_state",
                                **mcp_tooling_metadata,
                                "tools_passed_count": len(resolved_tools),
                            }
                            yield f"data: {json.dumps(tooling_evt)}\n\n"
                        except Exception as tooling_evt_error:
                            print(f"Warning: failed to emit tooling_state event: {tooling_evt_error}")

                        if (
                            str(mcp_scope.get("mcp_scope_mode")) == "project"
                            and mcp_scope.get("mcp_project_slug")
                        ):
                            try:
                                scope_evt = {
                                    "type": "project_scope_selected",
                                    "project": {
                                        "slug": mcp_scope.get("mcp_project_slug"),
                                        "name": mcp_scope.get("mcp_project_name"),
                                        "lifecycle": mcp_scope.get("mcp_project_lifecycle"),
                                    },
                                    "source": mcp_scope.get("mcp_project_source"),
                                }
                                yield f"data: {json.dumps(scope_evt)}\n\n"
                            except Exception as scope_evt_error:
                                print(f"Warning: failed to emit project_scope_selected event: {scope_evt_error}")

                        pass_index = 1
                        loop_messages = _apply_digest_schedule_prompt(
                            messages=list(combined_messages),
                            config=digest_schedule_config,
                            conversation_id=str(conversation.id),
                            tooling_metadata=mcp_tooling_metadata,
                            seen_event_ids=history_digest_schedule_event_ids,
                        )
                        loop_messages = _apply_pre_compaction_flush_prompt(
                            messages=loop_messages,
                            config=pre_compaction_flush_config,
                            conversation_id=str(conversation.id),
                            tooling_metadata=mcp_tooling_metadata,
                            seen_event_ids=history_pre_compaction_event_ids,
                        )
                        tool_loop_iterations = 0
                        tool_loop_stop_reason = "provider_final_response"
                        stream_executed_tool_calls: List[Dict[str, Any]] = []
                        if approval_resume_context and approval_resume_context.get("action") == "approve":
                            prior_tool_calls = approval_resume_context.get("prior_tool_calls")
                            if isinstance(prior_tool_calls, list):
                                stream_executed_tool_calls.extend(
                                    dict(item) for item in prior_tool_calls if isinstance(item, dict)
                                )
                        approval_request_payload: Optional[Dict[str, Any]] = None
                        approval_resolution_payload: Optional[Dict[str, Any]] = None
                        mcp_runtime_service = MCPRegistryService(
                            db,
                            call_timeout_seconds=mcp_tool_timeout_seconds,
                        )

                        if approval_resume_context and approval_resume_context.get("action") == "reject":
                            approval_resolution_payload = {
                                "type": "approval_resolution",
                                "status": "rejected",
                                "request_id": approval_resume_context.get("request_id"),
                                "tool": approval_resume_context.get("tool"),
                                "summary": approval_resume_context.get("summary"),
                            }
                            tool_loop_stop_reason = "approval_rejected"
                            full_response = (
                                f"Understood. I did not run mutating tool "
                                f"'{approval_resume_context.get('tool')}'."
                            )
                            finish_reason_final = "stop"
                            finish_reason_history.append("stop")
                            token_count += max(1, len(full_response.split()))
                            reject_chunk = {
                                "choices": [
                                    {
                                        "delta": {
                                            "role": "assistant",
                                            "content": full_response,
                                        },
                                        "finish_reason": "stop",
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(reject_chunk)}\n\n"
                        elif approval_resume_context and approval_resume_context.get("action") == "approve":
                            resume_tool_name = str(approval_resume_context.get("tool") or "").strip()
                            resume_synthetic_reason = (
                                str(approval_resume_context.get("synthetic_reason") or "").strip()
                                if isinstance(approval_resume_context, dict)
                                else ""
                            )
                            resume_synthetic_reason = resume_synthetic_reason or None
                            resume_tool_arguments = (
                                approval_resume_context.get("arguments")
                                if isinstance(approval_resume_context.get("arguments"), dict)
                                else {}
                            )
                            resume_tool_arguments = _normalize_capture_tool_arguments(
                                conversation_type=effective_conversation_type,
                                mcp_scope=mcp_scope,
                                tool_name=resume_tool_name,
                                tool_arguments=resume_tool_arguments,
                            )
                            resume_tool_arguments = _normalize_owner_profile_tool_arguments(
                                latest_user_message=capture_intent_message_hint,
                                tool_name=resume_tool_name,
                                tool_arguments=resume_tool_arguments,
                                synthetic_reason=resume_synthetic_reason,
                            )
                            resume_request_id = approval_resume_context.get("request_id")
                            resume_call_id = (
                                f"resume_{resume_request_id}"
                                if isinstance(resume_request_id, str) and resume_request_id
                                else f"resume_{resume_tool_name}"
                            )
                            approval_resolution_payload = {
                                "type": "approval_resolution",
                                "status": "approved",
                                "request_id": resume_request_id,
                                "tool": resume_tool_name,
                                "summary": approval_resume_context.get("summary"),
                            }
                            if resume_tool_name:
                                loop_messages.append(
                                    {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "id": resume_call_id,
                                                "type": "function",
                                                "function": {
                                                    "name": resume_tool_name,
                                                    "arguments": json.dumps(
                                                        resume_tool_arguments,
                                                        ensure_ascii=True,
                                                    ),
                                                },
                                            }
                                        ],
                                    }
                                )
                                try:
                                    resume_tool_evt = {
                                        "type": "tool_call",
                                        "name": resume_tool_name,
                                        "arguments": resume_tool_arguments,
                                        "resumed": True,
                                    }
                                    if resume_synthetic_reason:
                                        resume_tool_evt["synthetic_reason"] = resume_synthetic_reason
                                    yield f"data: {json.dumps(resume_tool_evt)}\n\n"
                                except Exception as resume_evt_error:
                                    print(f"Warning: failed to emit resumed tool_call event: {resume_evt_error}")

                                resume_tool_record = await mcp_runtime_service.get_enabled_tool(
                                    user_id or "current",
                                    resume_tool_name,
                                )
                                resume_guard_error = _capture_task_guard_error(
                                    conversation_type=effective_conversation_type,
                                    latest_user_message=capture_intent_message_hint,
                                    tool_name=resume_tool_name,
                                    executed_tool_calls=stream_executed_tool_calls,
                                )
                                if resume_guard_error:
                                    execution = {
                                        "ok": False,
                                        "error": resume_guard_error,
                                    }
                                elif resume_tool_record is None:
                                    execution = {
                                        "ok": False,
                                        "error": {
                                            "code": "TOOL_NOT_ALLOWED",
                                            "message": f"Tool '{resume_tool_name}' is not enabled.",
                                        },
                                    }
                                else:
                                    context_allowed, context_error = _validate_mutating_orchestration_context(
                                        tool_name=resume_tool_name,
                                        safety_class=resume_tool_record.safety_class,
                                        orchestration_context=orchestration_context_payload,
                                    )
                                    if not context_allowed:
                                        execution = {
                                            "ok": False,
                                            "error": context_error,
                                        }
                                        tool_loop_stop_reason = "missing_orchestration_context"
                                    else:
                                        execution = await mcp_runtime_service.execute_tool_call(
                                            user_id or "current",
                                            resume_tool_name,
                                            resume_tool_arguments,
                                        )
                                if execution.get("ok"):
                                    tool_result_payload = execution.get("data", {})
                                    stream_executed_tool_calls.append(
                                        {
                                            "name": resume_tool_name,
                                            "status": "success",
                                            "latency_ms": execution.get("latency_ms"),
                                            "arguments": resume_tool_arguments,
                                            "result": tool_result_payload,
                                            "resumed": True,
                                            "synthetic_reason": resume_synthetic_reason,
                                        }
                                    )
                                else:
                                    tool_result_payload = {
                                        "ok": False,
                                        "error": execution.get("error"),
                                    }
                                    execution_error = execution.get("error")
                                    execution_error_code = (
                                        str(execution_error.get("code") or "").strip()
                                        if isinstance(execution_error, dict)
                                        else ""
                                    )
                                    execution_status = (
                                        "blocked_intent"
                                        if execution_error_code.startswith("CAPTURE_TASK_")
                                        else "error"
                                    )
                                    stream_executed_tool_calls.append(
                                        {
                                            "name": resume_tool_name,
                                            "status": execution_status,
                                            "arguments": resume_tool_arguments,
                                            "error": execution.get("error"),
                                            "resumed": True,
                                            "synthetic_reason": resume_synthetic_reason,
                                        }
                                    )

                                loop_messages.append(
                                    _build_tool_result_message(
                                        provider=request.provider,
                                        tool_name=resume_tool_name,
                                        tool_call_id=resume_call_id,
                                        content=json.dumps(tool_result_payload, ensure_ascii=True),
                                    )
                                )
                                tool_loop_iterations += 1
                                try:
                                    resume_result_evt = {
                                        "type": "tool_result",
                                        "name": resume_tool_name,
                                        "ok": bool(execution.get("ok")),
                                        "resumed": True,
                                    }
                                    yield f"data: {json.dumps(resume_result_evt)}\n\n"
                                except Exception as resume_result_evt_error:
                                    print(
                                        f"Warning: failed to emit resumed tool_result event: {resume_result_evt_error}"
                                    )

                        if approval_resolution_payload:
                            try:
                                yield f"data: {json.dumps(approval_resolution_payload)}\n\n"
                            except Exception as approval_resolution_evt_error:
                                print(
                                    f"Warning: failed to emit approval_resolution event: "
                                    f"{approval_resolution_evt_error}"
                                )

                        while True:
                            if tool_loop_stop_reason in {"approval_rejected", "missing_orchestration_context"}:
                                break
                            pass_text = ""
                            pass_finish_reason: Optional[str] = None
                            pass_tool_call_buffer: Dict[str, Dict[str, Any]] = {}

                            MODULE_LOGGER.info(
                                "chat_stream_pass_start provider=%s model=%s conversation_id=%s pass=%s",
                                request.provider,
                                request.model,
                                conversation.id,
                                pass_index,
                            )
                            pass_started_at = time.perf_counter()
                            provider_pass_timed_out = False
                            try:
                                async for chunk in iter_provider_stream_with_timeout(
                                    provider_instance.chat_completion_stream(
                                        loop_messages,
                                        request.model,
                                        provider_params,
                                    ),
                                    timeout_seconds=mcp_provider_timeout_seconds,
                                ):
                                    if isinstance(chunk, dict) and "error" in chunk:
                                        provider_error = chunk.get("error")
                                        if isinstance(provider_error, bool):
                                            provider_error = None
                                        raise RuntimeError(
                                            provider_error or chunk.get("message") or "Unknown provider error"
                                        )

                                    content = extract_chunk_content(chunk)
                                    if content:
                                        pass_text += content
                                        full_response += content

                                    tool_deltas = extract_chunk_tool_call_deltas(chunk)
                                    if tool_deltas:
                                        update_stream_tool_call_buffer(pass_tool_call_buffer, tool_deltas)

                                    detected_finish_reason = extract_finish_reason(chunk)
                                    if detected_finish_reason:
                                        pass_finish_reason = detected_finish_reason

                                    token_count += 1

                                    # Yield each chunk and flush immediately
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    # Add an explicit flush marker
                                    yield ""
                            except asyncio.TimeoutError:
                                provider_pass_timed_out = True
                                provider_timeout_count += 1
                                tool_loop_stop_reason = "provider_timeout"
                                MODULE_LOGGER.warning(
                                    "chat_stream_pass_timeout provider=%s model=%s conversation_id=%s pass=%s timeout=%s",
                                    request.provider,
                                    request.model,
                                    conversation.id,
                                    pass_index,
                                    mcp_provider_timeout_seconds,
                                )
                            finally:
                                provider_call_latencies_ms.append(
                                    int((time.perf_counter() - pass_started_at) * 1000)
                                )

                            if provider_pass_timed_out:
                                break

                            pass_tool_calls = finalize_stream_tool_calls(pass_tool_call_buffer)
                            if resolved_tool_names and not pass_tool_calls:
                                pass_tool_calls = _build_orchestration_followthrough_tool_calls(
                                    conversation_type=effective_conversation_type,
                                    latest_user_message=capture_intent_message_hint,
                                    executed_tool_calls=stream_executed_tool_calls,
                                    available_tool_names=resolved_tool_names,
                                    mcp_scope=mcp_scope,
                                    digest_schedule_config=digest_schedule_config,
                                )
                            finish_reason_final = pass_finish_reason
                            finish_reason_history.append(pass_finish_reason or "unknown")

                            pass_chars = len(pass_text)
                            progress_chars = len(pass_text.strip())
                            is_truncated = is_truncation_finish_reason(pass_finish_reason, request.provider)
                            if is_truncated:
                                truncated_at_least_once = True

                            MODULE_LOGGER.info(
                                "chat_stream_pass_end provider=%s model=%s conversation_id=%s pass=%s finish_reason=%s chars=%s tool_calls=%s",
                                request.provider,
                                request.model,
                                conversation.id,
                                pass_index,
                                pass_finish_reason,
                                pass_chars,
                                len(pass_tool_calls),
                            )

                            normalized_pass_tool_calls: List[Dict[str, Any]] = []
                            for call_index, call in enumerate(pass_tool_calls):
                                if not isinstance(call, dict):
                                    continue
                                tool_name = call.get("name")
                                if not isinstance(tool_name, str) or not tool_name.strip():
                                    continue
                                call_payload = dict(call)
                                call_id = str(call_payload.get("id") or "").strip()
                                if not call_id:
                                    call_id = f"stream_tool_call_{pass_index}_{call_index + 1}"
                                call_payload["id"] = call_id
                                normalized_pass_tool_calls.append(call_payload)
                            pass_tool_calls = normalized_pass_tool_calls
                            if _is_strict_native_provider(request.provider) and len(pass_tool_calls) > 1:
                                mcp_tooling_metadata["strict_native_tool_call_limit_applied"] = True
                                mcp_tooling_metadata["strict_native_tool_call_limit_count"] = len(pass_tool_calls)
                                pass_tool_calls = pass_tool_calls[:1]

                            if pass_tool_calls and resolved_tools:
                                tool_loop_iterations += 1
                                if tool_loop_iterations > mcp_max_tool_iterations:
                                    tool_loop_stop_reason = "max_tool_iterations_exceeded"
                                    stopped_by_guardrail = "max_tool_iterations_exceeded"
                                    break

                                assistant_payload: Dict[str, Any] = {
                                    "role": "assistant",
                                    "content": pass_text or "",
                                    "tool_calls": [
                                        {
                                            "id": call["id"],
                                            "type": "function",
                                            "function": {
                                                "name": call["name"],
                                                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=True),
                                            },
                                        }
                                        for call in pass_tool_calls
                                    ],
                                }
                                loop_messages.append(assistant_payload)

                                executed_any = False
                                for tool_call in pass_tool_calls:
                                    tool_call_id = str(tool_call.get("id") or "").strip()
                                    if not tool_call_id:
                                        tool_call_id = (
                                            f"stream_tool_call_{pass_index}_{tool_call.get('name') or 'unknown'}"
                                        )
                                    tool_name = tool_call["name"]
                                    synthetic_reason = (
                                        str(tool_call.get("reason") or "").strip()
                                        if isinstance(tool_call, dict)
                                        else ""
                                    )
                                    synthetic_reason = synthetic_reason or None
                                    tool_arguments = (
                                        tool_call.get("arguments")
                                        if isinstance(tool_call.get("arguments"), dict)
                                        else {}
                                    )
                                    tool_arguments = _normalize_capture_tool_arguments(
                                        conversation_type=effective_conversation_type,
                                        mcp_scope=mcp_scope,
                                        tool_name=tool_name,
                                        tool_arguments=tool_arguments,
                                    )
                                    (
                                        tool_name,
                                        tool_arguments,
                                        synthetic_reason,
                                    ) = _maybe_override_capture_provider_tool_call(
                                        conversation_type=effective_conversation_type,
                                        latest_user_message=capture_intent_message_hint,
                                        tool_name=tool_name,
                                        tool_arguments=tool_arguments,
                                        synthetic_reason=synthetic_reason,
                                        executed_tool_calls=stream_executed_tool_calls,
                                        available_tool_names=resolved_tool_names,
                                    )
                                    (
                                        tool_name,
                                        tool_arguments,
                                        synthetic_reason,
                                    ) = _maybe_override_new_page_provider_tool_call(
                                        conversation_type=effective_conversation_type,
                                        latest_user_message=capture_intent_message_hint,
                                        tool_name=tool_name,
                                        tool_arguments=tool_arguments,
                                        synthetic_reason=synthetic_reason,
                                        executed_tool_calls=stream_executed_tool_calls,
                                        available_tool_names=resolved_tool_names,
                                        mcp_scope=mcp_scope,
                                    )
                                    (
                                        tool_name,
                                        tool_arguments,
                                        synthetic_reason,
                                    ) = _maybe_override_compound_edit_provider_tool_call(
                                        conversation_type=effective_conversation_type,
                                        latest_user_message=capture_intent_message_hint,
                                        tool_name=tool_name,
                                        tool_arguments=tool_arguments,
                                        synthetic_reason=synthetic_reason,
                                        executed_tool_calls=stream_executed_tool_calls,
                                        available_tool_names=resolved_tool_names,
                                        mcp_scope=mcp_scope,
                                    )
                                    tool_arguments = _normalize_owner_profile_tool_arguments(
                                        latest_user_message=capture_intent_message_hint,
                                        tool_name=tool_name,
                                        tool_arguments=tool_arguments,
                                        synthetic_reason=synthetic_reason,
                                    )
                                    try:
                                        tool_call_event = {
                                            "type": "tool_call",
                                            "name": tool_name,
                                            "arguments": tool_arguments,
                                        }
                                        yield f"data: {json.dumps(tool_call_event)}\n\n"
                                    except Exception as tool_call_evt_error:
                                        print(f"Warning: failed to emit tool_call event: {tool_call_evt_error}")

                                    guard_error = _capture_task_guard_error(
                                        conversation_type=effective_conversation_type,
                                        latest_user_message=capture_intent_message_hint,
                                        tool_name=tool_name,
                                        executed_tool_calls=stream_executed_tool_calls,
                                    )
                                    if guard_error:
                                        error_payload = {
                                            "ok": False,
                                            "error": guard_error,
                                        }
                                        loop_messages.append(
                                            _build_tool_result_message(
                                                provider=request.provider,
                                                tool_name=tool_name,
                                                tool_call_id=tool_call_id,
                                                content=json.dumps(error_payload, ensure_ascii=True),
                                            )
                                        )
                                        stream_executed_tool_calls.append(
                                            {
                                                "id": tool_call_id,
                                                "name": tool_name,
                                                "status": "blocked_intent",
                                                "arguments": tool_arguments,
                                                "error": guard_error,
                                                "synthetic_reason": synthetic_reason,
                                            }
                                        )
                                        try:
                                            tool_result_event = {
                                                "type": "tool_result",
                                                "name": tool_name,
                                                "ok": False,
                                                "error": guard_error,
                                            }
                                            yield f"data: {json.dumps(tool_result_event)}\n\n"
                                        except Exception as tool_result_evt_error:
                                            print(
                                                "Warning: failed to emit blocked tool_result event: "
                                                f"{tool_result_evt_error}"
                                            )
                                        continue

                                    tool_record = await mcp_runtime_service.get_enabled_tool(user_id or "current", tool_name)
                                    if tool_record is None:
                                        tool_error = {
                                            "ok": False,
                                            "error": {
                                                "code": "TOOL_NOT_ALLOWED",
                                                "message": f"Tool '{tool_name}' is not enabled.",
                                            },
                                        }
                                        loop_messages.append(
                                            _build_tool_result_message(
                                                provider=request.provider,
                                                tool_name=tool_name,
                                                tool_call_id=tool_call_id,
                                                content=json.dumps(tool_error, ensure_ascii=True),
                                            )
                                        )
                                        stream_executed_tool_calls.append(
                                            {
                                                "id": tool_call_id,
                                                "name": tool_name,
                                                "status": "denied",
                                                "arguments": tool_arguments,
                                                "reason": "tool_not_enabled",
                                                "synthetic_reason": synthetic_reason,
                                            }
                                        )
                                        continue

                                    context_allowed, context_error = _validate_mutating_orchestration_context(
                                        tool_name=tool_name,
                                        safety_class=tool_record.safety_class,
                                        orchestration_context=orchestration_context_payload,
                                    )
                                    if not context_allowed:
                                        tool_error = {
                                            "ok": False,
                                            "error": context_error,
                                        }
                                        loop_messages.append(
                                            _build_tool_result_message(
                                                provider=request.provider,
                                                tool_name=tool_name,
                                                tool_call_id=tool_call_id,
                                                content=json.dumps(tool_error, ensure_ascii=True),
                                            )
                                        )
                                        stream_executed_tool_calls.append(
                                            {
                                                "id": tool_call_id,
                                                "name": tool_name,
                                                "status": "blocked_context",
                                                "arguments": tool_arguments,
                                                "error": context_error,
                                                "synthetic_reason": synthetic_reason,
                                            }
                                        )
                                        tool_loop_stop_reason = "missing_orchestration_context"
                                        try:
                                            context_evt = {
                                                "type": "orchestration_context_error",
                                                "tool": tool_name,
                                                "error": context_error,
                                            }
                                            yield f"data: {json.dumps(context_evt)}\n\n"
                                        except Exception as context_evt_error:
                                            print(f"Warning: failed to emit orchestration_context_error event: {context_evt_error}")
                                        break

                                    if (
                                        tool_record.safety_class == "mutating"
                                        and tool_name not in approved_mutating_tools
                                    ):
                                        approval_context = await _build_mutating_approval_context(
                                            runtime_service=mcp_runtime_service,
                                            mcp_user_id=user_id or "current",
                                            tool_name=tool_name,
                                            tool_arguments=tool_arguments,
                                        )
                                        approval_request_payload = _build_approval_request_payload(
                                            tool=tool_name,
                                            safety_class=tool_record.safety_class,
                                            arguments=tool_arguments,
                                            summary=approval_context.get("summary"),
                                            scope=mcp_scope,
                                            synthetic_reason=synthetic_reason,
                                            origin_user_message=capture_intent_message_hint,
                                        )
                                        approval_preview = approval_context.get("preview")
                                        if isinstance(approval_preview, dict):
                                            approval_request_payload["preview"] = approval_preview
                                        tool_loop_stop_reason = "approval_required"
                                        try:
                                            yield f"data: {json.dumps(approval_request_payload)}\n\n"
                                        except Exception as approval_evt_error:
                                            print(f"Warning: failed to emit approval_request event: {approval_evt_error}")
                                        break

                                    execution = await mcp_runtime_service.execute_tool_call(
                                        user_id or "current",
                                        tool_name,
                                        tool_arguments,
                                    )
                                    executed_any = True
                                    if execution.get("ok"):
                                        tool_result_payload = execution.get("data", {})
                                        stream_executed_tool_calls.append(
                                            {
                                                "id": tool_call_id,
                                                "name": tool_name,
                                                "status": "success",
                                                "latency_ms": execution.get("latency_ms"),
                                                "arguments": tool_arguments,
                                                "result": tool_result_payload,
                                                "synthetic_reason": synthetic_reason,
                                            }
                                        )
                                    else:
                                        tool_result_payload = {
                                            "ok": False,
                                            "error": execution.get("error"),
                                        }
                                        stream_executed_tool_calls.append(
                                            {
                                                "id": tool_call_id,
                                                "name": tool_name,
                                                "status": "error",
                                                "arguments": tool_arguments,
                                                "error": execution.get("error"),
                                                "synthetic_reason": synthetic_reason,
                                            }
                                        )

                                    loop_messages.append(
                                        _build_tool_result_message(
                                            provider=request.provider,
                                            tool_name=tool_name,
                                            tool_call_id=tool_call_id,
                                            content=json.dumps(tool_result_payload, ensure_ascii=True),
                                        )
                                    )
                                    try:
                                        tool_result_event = {
                                            "type": "tool_result",
                                            "name": tool_name,
                                            "ok": bool(execution.get("ok")),
                                        }
                                        yield f"data: {json.dumps(tool_result_event)}\n\n"
                                    except Exception as tool_result_evt_error:
                                        print(f"Warning: failed to emit tool_result event: {tool_result_evt_error}")

                                if approval_request_payload:
                                    break

                                if tool_loop_stop_reason == "missing_orchestration_context":
                                    break

                                if not executed_any:
                                    tool_loop_stop_reason = "tool_calls_without_execution"
                                pass_index += 1
                                continue

                            if pass_tool_calls and not resolved_tools:
                                tool_loop_stop_reason = "tool_calls_without_enabled_tools"
                                break

                            can_continue = (
                                auto_continue_enabled
                                and is_truncated
                                and auto_continue_attempts < auto_continue_max_passes
                            )

                            if can_continue:
                                # Guardrail: if continuation pass added almost nothing, stop to avoid loops.
                                if pass_index > 1 and progress_chars <= auto_continue_min_progress_chars:
                                    stopped_by_guardrail = "no_progress"
                                    logger.warning(
                                        "chat_stream_auto_continue_stopped provider=%s model=%s conversation_id=%s pass=%s reason=no_progress chars=%s threshold=%s",
                                        request.provider,
                                        request.model,
                                        conversation.id,
                                        pass_index,
                                        progress_chars,
                                        auto_continue_min_progress_chars,
                                    )
                                    break

                                auto_continue_attempts += 1
                                next_pass = pass_index + 1
                                continuation_event = {
                                    "type": "auto_continue",
                                    "pass": next_pass,
                                    "trigger_finish_reason": pass_finish_reason,
                                    "attempt": auto_continue_attempts,
                                }
                                yield f"data: {json.dumps(continuation_event)}\n\n"
                                yield ""

                                loop_messages = build_continuation_messages(
                                    loop_messages,
                                    full_response,
                                    continuation_prompt=auto_continue_prompt,
                                )
                                pass_index = next_pass
                                continue

                            if auto_continue_enabled and is_truncated and auto_continue_attempts >= auto_continue_max_passes:
                                stopped_by_guardrail = "max_passes_reached"

                            break

                        if not full_response.strip():
                            fallback_response: Optional[str] = None
                            fallback_finish_reason = finish_reason_final or "stop"

                            if approval_request_payload:
                                fallback_response = APPROVAL_REQUIRED_RESPONSE_TEXT
                                fallback_finish_reason = "tool_calls"
                            elif (
                                isinstance(approval_resolution_payload, dict)
                                and str(approval_resolution_payload.get("status") or "").strip().lower()
                                == "approved"
                            ):
                                fallback_response = _build_approval_execution_success_message(
                                    executed_tool_calls=stream_executed_tool_calls,
                                    approval_resolution_payload=approval_resolution_payload,
                                )
                                fallback_finish_reason = "stop"
                            elif tool_loop_stop_reason == "tool_calls_without_enabled_tools":
                                fallback_response = (
                                    "I requested a tool action, but tools are not enabled in this context. "
                                    "Please try again from a tool-enabled project chat."
                                )
                            elif tool_loop_stop_reason == "tool_calls_without_execution":
                                fallback_response = (
                                    "I requested a tool action, but it could not be executed. "
                                    "Please try again or rephrase your request."
                                )
                            elif tool_loop_stop_reason == "missing_orchestration_context":
                                fallback_response = (
                                    "I need canonical scope context before I can run mutating tools. "
                                    "Please run scaffold/bootstrap steps or let me create missing required files first."
                                )
                            elif tool_loop_stop_reason == "max_tool_iterations_exceeded":
                                fallback_response = "Tool execution stopped after reaching max iterations."
                                fallback_finish_reason = "length"
                            elif tool_loop_stop_reason == "provider_timeout":
                                fallback_response = _provider_timeout_fallback_response(
                                    effective_conversation_type
                                )
                                fallback_finish_reason = "stop"

                            if fallback_response:
                                full_response = fallback_response
                                finish_reason_final = fallback_finish_reason
                                if (
                                    not finish_reason_history
                                    or finish_reason_history[-1] != fallback_finish_reason
                                ):
                                    finish_reason_history.append(fallback_finish_reason)
                                token_count += max(1, len(fallback_response.split()))

                                fallback_chunk = {
                                    "choices": [
                                        {
                                            "delta": {
                                                "role": "assistant",
                                                "content": fallback_response,
                                            },
                                            "finish_reason": fallback_finish_reason,
                                        }
                                    ]
                                }
                                yield f"data: {json.dumps(fallback_chunk)}\n\n"

                        (
                            full_response,
                            citation_suffix,
                            citation_paths,
                            citation_appended,
                        ) = _apply_grounding_citations_if_needed(
                            response_content=full_response,
                            executed_tool_calls=stream_executed_tool_calls,
                            latest_user_message=capture_intent_message_hint,
                            tool_loop_stop_reason=tool_loop_stop_reason,
                            default_scope_path=(
                                mcp_scope.get("mcp_project_slug")
                                if isinstance(mcp_scope, dict)
                                else None
                            ),
                        )
                        if citation_appended and citation_suffix:
                            token_count += max(1, len(citation_suffix.split()))
                            citation_chunk = {
                                "choices": [
                                    {
                                        "delta": {
                                            "role": "assistant",
                                            "content": citation_suffix,
                                        },
                                        "finish_reason": None,
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(citation_chunk)}\n\n"
                        
                        # Calculate tokens per second
                        elapsed_time = time.time() - start_time
                        tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0

                        _finalize_pre_compaction_flush_status(
                            tooling_metadata=mcp_tooling_metadata,
                            tool_loop_stop_reason=tool_loop_stop_reason,
                            tool_calls_executed_count=len(stream_executed_tool_calls),
                        )
                        _finalize_digest_schedule_status(
                            tooling_metadata=mcp_tooling_metadata,
                            tool_loop_stop_reason=tool_loop_stop_reason,
                            tool_calls_executed_count=len(stream_executed_tool_calls),
                        )
                        
                        mcp_tooling_metadata.update(
                            {
                                "tool_loop_enabled": bool(resolved_tools) or bool(stream_executed_tool_calls),
                                "tool_loop_iterations": tool_loop_iterations,
                                "tool_loop_stop_reason": tool_loop_stop_reason,
                                "tool_calls_executed_count": len(stream_executed_tool_calls),
                                "approval_required": bool(approval_request_payload),
                                "approval_resolved": bool(approval_resolution_payload),
                                **_build_provider_timing_metadata(
                                    provider_timeout_seconds=mcp_provider_timeout_seconds,
                                    provider_call_latencies_ms=provider_call_latencies_ms,
                                    provider_timeout_count=provider_timeout_count,
                                ),
                            }
                        )
                        if citation_paths:
                            mcp_tooling_metadata["response_citations"] = citation_paths
                            mcp_tooling_metadata["response_citations_appended"] = citation_appended

                        delivery_handoff_payload = _build_digest_delivery_handoff_payload(
                            conversation_type=effective_conversation_type,
                            response_content=full_response,
                            conversation_id=str(conversation.id),
                            provider=request.provider,
                            model=request.model,
                            digest_schedule_config=digest_schedule_config,
                            tooling_metadata=mcp_tooling_metadata,
                        )
                        if isinstance(delivery_handoff_payload, dict):
                            delivery_handoff_payload = await _attach_digest_delivery_delivery_state(
                                handoff_payload=delivery_handoff_payload,
                                user_id=user_id,
                                tooling_metadata=mcp_tooling_metadata,
                                send_config=digest_delivery_send_config,
                            )
                            mcp_tooling_metadata["digest_delivery_handoff"] = delivery_handoff_payload
                            try:
                                handoff_evt = {
                                    "type": "delivery_handoff",
                                    **delivery_handoff_payload,
                                }
                                yield f"data: {json.dumps(handoff_evt)}\n\n"
                            except Exception as handoff_evt_error:
                                print(
                                    f"Warning: failed to emit delivery_handoff event: {handoff_evt_error}"
                                )

                        # Store the LLM response in the database with persona metadata
                        message_metadata = {
                            "token_count": token_count,
                            "tokens_per_second": round(tokens_per_second, 1),
                            "model": request.model,
                            "temperature": provider_params.get("temperature", 0.7),
                            "streaming": True,
                            "auto_continue_enabled": auto_continue_enabled,
                            "auto_continue_attempts": auto_continue_attempts,
                            "auto_continue_max_passes": auto_continue_max_passes,
                            "finish_reason_final": finish_reason_final,
                            "finish_reason_history": finish_reason_history,
                            "truncated_at_least_once": truncated_at_least_once,
                            "mcp": {
                                **mcp_tooling_metadata,
                                "tools_passed_count": len(resolved_tools),
                                "tool_calls_executed": stream_executed_tool_calls,
                            },
                        }
                        if approval_request_payload:
                            message_metadata["mcp"]["approval_request"] = approval_request_payload
                        if approval_resolution_payload:
                            message_metadata["mcp"]["approval_resolution"] = approval_resolution_payload
                        if stopped_by_guardrail:
                            message_metadata["stopped_by_guardrail"] = stopped_by_guardrail
                        
                        # Add persona metadata if persona was used
                        if request.persona_id:
                            message_metadata.update({
                                "persona_id": request.persona_id,
                                "persona_applied": bool(request.persona_system_prompt),
                                "persona_model_settings_applied": bool(request.persona_model_settings)
                            })
                        
                        db_message = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conversation.id,
                            sender="llm",
                            message=full_response,
                            message_metadata=message_metadata
                        )
                        db.add(db_message)
                        
                        # Update the conversation's updated_at timestamp
                        conversation.updated_at = db_message.created_at
                        
                        await db.commit()

                        if approval_request_payload:
                            approval_event = {
                                "type": "approval_required",
                                "approval_request": approval_request_payload,
                            }
                            yield f"data: {json.dumps(approval_event)}\n\n"

                        yield "data: [DONE]\n\n"
                    except Exception as stream_error:
                        print(f"Error in stream_generator: {stream_error}")
                        logger.error(f"Streaming error with persona_id {request.persona_id}: {stream_error}")
                        
                        # Enhanced error message for persona-related errors
                        error_message = f"Streaming error: {str(stream_error)}"
                        if request.persona_id:
                            error_message += f" (Persona ID: {request.persona_id})"
                        
                        error_json = json.dumps({
                            "error": True,
                            "message": error_message,
                            "persona_id": request.persona_id if request.persona_id else None
                        })
                        yield f"data: {error_json}\n\n"
                        yield "data: [DONE]\n\n"
                
                # Add headers to prevent buffering
                headers = {
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",  # Disable Nginx buffering
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream"
                }
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers=headers
                )
            
            # Handle non-streaming
            print(f"Starting non-streaming chat completion with model: {request.model}")
            start_time = time.time()
            print(f"Sending {len(combined_messages)} messages to chat_completion")
            for i, msg in enumerate(combined_messages):
                print(f"  Message {i+1}: role={msg.get('role', 'unknown')}, content={msg.get('content', '')[:50]}...")

            loop_messages = _apply_digest_schedule_prompt(
                messages=list(combined_messages),
                config=digest_schedule_config,
                conversation_id=str(conversation.id),
                tooling_metadata=mcp_tooling_metadata,
                seen_event_ids=history_digest_schedule_event_ids,
            )
            loop_messages = _apply_pre_compaction_flush_prompt(
                messages=loop_messages,
                config=pre_compaction_flush_config,
                conversation_id=str(conversation.id),
                tooling_metadata=mcp_tooling_metadata,
                seen_event_ids=history_pre_compaction_event_ids,
            )
            tool_loop_enabled = bool(resolved_tools)
            tool_loop_iterations = 0
            tool_loop_stop_reason = "provider_final_response"
            approval_request_payload: Optional[Dict[str, Any]] = None
            approval_resolution_payload: Optional[Dict[str, Any]] = None
            executed_tool_calls: List[Dict[str, Any]] = []
            if approval_resume_context and approval_resume_context.get("action") == "approve":
                prior_tool_calls = approval_resume_context.get("prior_tool_calls")
                if isinstance(prior_tool_calls, list):
                    executed_tool_calls.extend(
                        dict(item) for item in prior_tool_calls if isinstance(item, dict)
                    )
            result: Dict[str, Any] = {}
            provider_call_latencies_ms: List[int] = []
            provider_timeout_count = 0
            forced_tool_calls: List[Dict[str, Any]] = []

            mcp_runtime_service = MCPRegistryService(
                db,
                call_timeout_seconds=mcp_tool_timeout_seconds,
            )
            if approval_resume_context and approval_resume_context.get("action") == "reject":
                approval_resolution_payload = {
                    "type": "approval_resolution",
                    "status": "rejected",
                    "request_id": approval_resume_context.get("request_id"),
                    "tool": approval_resume_context.get("tool"),
                    "summary": approval_resume_context.get("summary"),
                }
                tool_loop_stop_reason = "approval_rejected"
                result = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "Understood. I did not run mutating tool "
                                    f"'{approval_resume_context.get('tool')}'."
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            else:
                if approval_resume_context and approval_resume_context.get("action") == "approve":
                    resume_tool_name = str(approval_resume_context.get("tool") or "").strip()
                    resume_synthetic_reason = (
                        str(approval_resume_context.get("synthetic_reason") or "").strip()
                        if isinstance(approval_resume_context, dict)
                        else ""
                    )
                    resume_synthetic_reason = resume_synthetic_reason or None
                    resume_tool_arguments = (
                        approval_resume_context.get("arguments")
                        if isinstance(approval_resume_context.get("arguments"), dict)
                        else {}
                    )
                    resume_tool_arguments = _normalize_capture_tool_arguments(
                        conversation_type=effective_conversation_type,
                        mcp_scope=mcp_scope,
                        tool_name=resume_tool_name,
                        tool_arguments=resume_tool_arguments,
                    )
                    resume_tool_arguments = _normalize_owner_profile_tool_arguments(
                        latest_user_message=capture_intent_message_hint,
                        tool_name=resume_tool_name,
                        tool_arguments=resume_tool_arguments,
                        synthetic_reason=resume_synthetic_reason,
                    )
                    resume_request_id = approval_resume_context.get("request_id")
                    resume_call_id = (
                        f"resume_{resume_request_id}"
                        if isinstance(resume_request_id, str) and resume_request_id
                        else f"resume_{resume_tool_name}"
                    )
                    approval_resolution_payload = {
                        "type": "approval_resolution",
                        "status": "approved",
                        "request_id": resume_request_id,
                        "tool": resume_tool_name,
                        "summary": approval_resume_context.get("summary"),
                    }

                    if resume_tool_name:
                        loop_messages.append(
                            {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": resume_call_id,
                                        "type": "function",
                                        "function": {
                                            "name": resume_tool_name,
                                            "arguments": json.dumps(
                                                resume_tool_arguments,
                                                ensure_ascii=True,
                                            ),
                                        },
                                    }
                                ],
                            }
                        )
                        resume_tool_record = await mcp_runtime_service.get_enabled_tool(
                            user_id or "current",
                            resume_tool_name,
                        )
                        resume_guard_error = _capture_task_guard_error(
                            conversation_type=effective_conversation_type,
                            latest_user_message=capture_intent_message_hint,
                            tool_name=resume_tool_name,
                            executed_tool_calls=executed_tool_calls,
                        )
                        if resume_guard_error:
                            execution = {
                                "ok": False,
                                "error": resume_guard_error,
                            }
                        elif resume_tool_record is None:
                            execution = {
                                "ok": False,
                                "error": {
                                    "code": "TOOL_NOT_ALLOWED",
                                    "message": f"Tool '{resume_tool_name}' is not enabled.",
                                },
                            }
                        else:
                            context_allowed, context_error = _validate_mutating_orchestration_context(
                                tool_name=resume_tool_name,
                                safety_class=resume_tool_record.safety_class,
                                orchestration_context=orchestration_context_payload,
                            )
                            if not context_allowed:
                                execution = {
                                    "ok": False,
                                    "error": context_error,
                                }
                                tool_loop_stop_reason = "missing_orchestration_context"
                            else:
                                execution = await mcp_runtime_service.execute_tool_call(
                                    user_id or "current",
                                    resume_tool_name,
                                    resume_tool_arguments,
                                )
                        if execution.get("ok"):
                            tool_content = execution.get("data", {})
                            executed_tool_calls.append(
                                {
                                    "name": resume_tool_name,
                                    "status": "success",
                                    "latency_ms": execution.get("latency_ms"),
                                    "arguments": resume_tool_arguments,
                                    "result": tool_content,
                                    "resumed": True,
                                    "synthetic_reason": resume_synthetic_reason,
                                }
                            )
                        else:
                            tool_content = {
                                "ok": False,
                                "error": execution.get("error"),
                            }
                            execution_error = execution.get("error")
                            execution_error_code = (
                                str(execution_error.get("code") or "").strip()
                                if isinstance(execution_error, dict)
                                else ""
                            )
                            execution_status = (
                                "blocked_intent"
                                if execution_error_code.startswith("CAPTURE_TASK_")
                                else "error"
                            )
                            executed_tool_calls.append(
                                {
                                    "name": resume_tool_name,
                                    "status": execution_status,
                                    "arguments": resume_tool_arguments,
                                    "error": execution.get("error"),
                                    "resumed": True,
                                    "synthetic_reason": resume_synthetic_reason,
                                }
                            )

                        loop_messages.append(
                            _build_tool_result_message(
                                provider=request.provider,
                                tool_name=resume_tool_name,
                                tool_call_id=resume_call_id,
                                content=json.dumps(tool_content, ensure_ascii=True),
                            )
                        )
                        tool_loop_iterations += 1
                        if execution.get("ok"):
                            forced_tool_calls = _build_orchestration_followthrough_tool_calls(
                                conversation_type=effective_conversation_type,
                                latest_user_message=capture_intent_message_hint,
                                executed_tool_calls=executed_tool_calls,
                                available_tool_names=resolved_tool_names,
                                mcp_scope=mcp_scope,
                                digest_schedule_config=digest_schedule_config,
                            )

                for iteration in range(1, mcp_max_tool_iterations + 1):
                    if tool_loop_stop_reason == "missing_orchestration_context":
                        break

                    tool_loop_iterations = iteration
                    if forced_tool_calls:
                        result = {}
                        tool_calls = forced_tool_calls
                        forced_tool_calls = []
                    else:
                        provider_call_started_at = time.perf_counter()
                        try:
                            result = await asyncio.wait_for(
                                provider_instance.chat_completion(
                                    loop_messages,
                                    request.model,
                                    provider_params,
                                ),
                                timeout=mcp_provider_timeout_seconds,
                            )
                        except asyncio.TimeoutError:
                            provider_timeout_count += 1
                            tool_loop_stop_reason = "provider_timeout"
                            MODULE_LOGGER.warning(
                                "chat_non_stream_timeout provider=%s model=%s conversation_id=%s iteration=%s timeout=%s",
                                request.provider,
                                request.model,
                                conversation.id,
                                iteration,
                                mcp_provider_timeout_seconds,
                            )
                            result = {}
                            break
                        finally:
                            provider_call_latencies_ms.append(
                                int((time.perf_counter() - provider_call_started_at) * 1000)
                            )
                        if isinstance(result, dict) and result.get("error"):
                            error_value = result.get("error")
                            message_value = result.get("message")
                            error_detail = message_value if isinstance(message_value, str) and message_value.strip() else error_value
                            raise HTTPException(
                                status_code=502,
                                detail=f"Provider error: {error_detail}",
                            )

                        tool_calls = extract_response_tool_calls(result)
                        if tool_loop_enabled and not tool_calls:
                            tool_calls = _build_orchestration_followthrough_tool_calls(
                                conversation_type=effective_conversation_type,
                                latest_user_message=capture_intent_message_hint,
                                executed_tool_calls=executed_tool_calls,
                                available_tool_names=resolved_tool_names,
                                mcp_scope=mcp_scope,
                                digest_schedule_config=digest_schedule_config,
                            )
                        normalized_tool_calls: List[Dict[str, Any]] = []
                        for call_index, call in enumerate(tool_calls):
                            if not isinstance(call, dict):
                                continue
                            tool_name = call.get("name")
                            if not isinstance(tool_name, str) or not tool_name.strip():
                                continue
                            call_payload = dict(call)
                            call_id = str(call_payload.get("id") or "").strip()
                            if not call_id:
                                call_id = f"tool_call_{iteration}_{call_index + 1}"
                            call_payload["id"] = call_id
                            normalized_tool_calls.append(call_payload)
                        tool_calls = normalized_tool_calls
                        if _is_strict_native_provider(request.provider) and len(tool_calls) > 1:
                            mcp_tooling_metadata["strict_native_tool_call_limit_applied"] = True
                            mcp_tooling_metadata["strict_native_tool_call_limit_count"] = len(tool_calls)
                            tool_calls = tool_calls[:1]
                    if not tool_loop_enabled or not tool_calls:
                        tool_loop_stop_reason = (
                            "provider_final_response" if not tool_calls else "tools_disabled"
                        )
                        break

                    assistant_content = extract_response_content(result)
                    assistant_payload: Dict[str, Any] = {
                        "role": "assistant",
                        "content": assistant_content or "",
                    }
                    assistant_payload["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=True),
                            },
                        }
                        for call in tool_calls
                    ]
                    loop_messages.append(assistant_payload)

                    executed_in_iteration = False
                    for tool_call in tool_calls:
                        tool_call_id = str(tool_call.get("id") or "").strip()
                        if not tool_call_id:
                            tool_call_id = f"tool_call_{iteration}_{tool_call.get('name') or 'unknown'}"
                        tool_name = tool_call["name"]
                        synthetic_reason = (
                            str(tool_call.get("reason") or "").strip()
                            if isinstance(tool_call, dict)
                            else ""
                        )
                        synthetic_reason = synthetic_reason or None
                        tool_arguments = (
                            tool_call.get("arguments")
                            if isinstance(tool_call.get("arguments"), dict)
                            else {}
                        )
                        tool_arguments = _normalize_capture_tool_arguments(
                            conversation_type=effective_conversation_type,
                            mcp_scope=mcp_scope,
                            tool_name=tool_name,
                            tool_arguments=tool_arguments,
                        )
                        (
                            tool_name,
                            tool_arguments,
                            synthetic_reason,
                        ) = _maybe_override_capture_provider_tool_call(
                            conversation_type=effective_conversation_type,
                            latest_user_message=capture_intent_message_hint,
                            tool_name=tool_name,
                            tool_arguments=tool_arguments,
                            synthetic_reason=synthetic_reason,
                            executed_tool_calls=executed_tool_calls,
                            available_tool_names=resolved_tool_names,
                        )
                        (
                            tool_name,
                            tool_arguments,
                            synthetic_reason,
                        ) = _maybe_override_new_page_provider_tool_call(
                            conversation_type=effective_conversation_type,
                            latest_user_message=capture_intent_message_hint,
                            tool_name=tool_name,
                            tool_arguments=tool_arguments,
                            synthetic_reason=synthetic_reason,
                            executed_tool_calls=executed_tool_calls,
                            available_tool_names=resolved_tool_names,
                            mcp_scope=mcp_scope,
                        )
                        (
                            tool_name,
                            tool_arguments,
                            synthetic_reason,
                        ) = _maybe_override_compound_edit_provider_tool_call(
                            conversation_type=effective_conversation_type,
                            latest_user_message=capture_intent_message_hint,
                            tool_name=tool_name,
                            tool_arguments=tool_arguments,
                            synthetic_reason=synthetic_reason,
                            executed_tool_calls=executed_tool_calls,
                            available_tool_names=resolved_tool_names,
                            mcp_scope=mcp_scope,
                        )
                        tool_arguments = _normalize_owner_profile_tool_arguments(
                            latest_user_message=capture_intent_message_hint,
                            tool_name=tool_name,
                            tool_arguments=tool_arguments,
                            synthetic_reason=synthetic_reason,
                        )
                        guard_error = _capture_task_guard_error(
                            conversation_type=effective_conversation_type,
                            latest_user_message=capture_intent_message_hint,
                            tool_name=tool_name,
                            executed_tool_calls=executed_tool_calls,
                        )
                        if guard_error:
                            error_payload = {
                                "ok": False,
                                "error": guard_error,
                            }
                            loop_messages.append(
                                _build_tool_result_message(
                                    provider=request.provider,
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id,
                                    content=json.dumps(error_payload, ensure_ascii=True),
                                )
                            )
                            executed_tool_calls.append(
                                {
                                    "id": tool_call_id,
                                    "name": tool_name,
                                    "status": "blocked_intent",
                                    "arguments": tool_arguments,
                                    "error": guard_error,
                                    "synthetic_reason": synthetic_reason,
                                }
                            )
                            continue
                        tool_record = await mcp_runtime_service.get_enabled_tool(user_id or "current", tool_name)
                        if tool_record is None:
                            error_payload = {
                                "ok": False,
                                "error": {
                                    "code": "TOOL_NOT_ALLOWED",
                                    "message": f"Tool '{tool_name}' is not enabled.",
                                },
                            }
                            loop_messages.append(
                                _build_tool_result_message(
                                    provider=request.provider,
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id,
                                    content=json.dumps(error_payload, ensure_ascii=True),
                                )
                            )
                            executed_tool_calls.append(
                                {
                                    "id": tool_call_id,
                                    "name": tool_name,
                                    "status": "denied",
                                    "arguments": tool_arguments,
                                    "reason": "tool_not_enabled",
                                    "synthetic_reason": synthetic_reason,
                                }
                            )
                            continue

                        context_allowed, context_error = _validate_mutating_orchestration_context(
                            tool_name=tool_name,
                            safety_class=tool_record.safety_class,
                            orchestration_context=orchestration_context_payload,
                        )
                        if not context_allowed:
                            error_payload = {
                                "ok": False,
                                "error": context_error,
                            }
                            loop_messages.append(
                                _build_tool_result_message(
                                    provider=request.provider,
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id,
                                    content=json.dumps(error_payload, ensure_ascii=True),
                                )
                            )
                            executed_tool_calls.append(
                                {
                                    "id": tool_call_id,
                                    "name": tool_name,
                                    "status": "blocked_context",
                                    "arguments": tool_arguments,
                                    "error": context_error,
                                    "synthetic_reason": synthetic_reason,
                                }
                            )
                            tool_loop_stop_reason = "missing_orchestration_context"
                            break

                        if (
                            tool_record.safety_class == "mutating"
                            and tool_name not in approved_mutating_tools
                        ):
                            approval_context = await _build_mutating_approval_context(
                                runtime_service=mcp_runtime_service,
                                mcp_user_id=user_id or "current",
                                tool_name=tool_name,
                                tool_arguments=tool_arguments,
                            )
                            approval_request_payload = _build_approval_request_payload(
                                tool=tool_name,
                                safety_class=tool_record.safety_class,
                                arguments=tool_arguments,
                                summary=approval_context.get("summary"),
                                scope=mcp_scope,
                                synthetic_reason=synthetic_reason,
                                origin_user_message=capture_intent_message_hint,
                            )
                            approval_preview = approval_context.get("preview")
                            if isinstance(approval_preview, dict):
                                approval_request_payload["preview"] = approval_preview
                            tool_loop_stop_reason = "approval_required"
                            break

                        execution = await mcp_runtime_service.execute_tool_call(
                            user_id or "current",
                            tool_name,
                            tool_arguments,
                        )
                        if execution.get("ok"):
                            tool_content = execution.get("data", {})
                            executed_tool_calls.append(
                                {
                                    "id": tool_call_id,
                                    "name": tool_name,
                                    "status": "success",
                                    "latency_ms": execution.get("latency_ms"),
                                    "arguments": tool_arguments,
                                    "result": tool_content,
                                    "synthetic_reason": synthetic_reason,
                                }
                            )
                        else:
                            tool_content = {
                                "ok": False,
                                "error": execution.get("error"),
                            }
                            executed_tool_calls.append(
                                {
                                    "id": tool_call_id,
                                    "name": tool_name,
                                    "status": "error",
                                    "arguments": tool_arguments,
                                    "error": execution.get("error"),
                                    "synthetic_reason": synthetic_reason,
                                }
                            )

                        loop_messages.append(
                            _build_tool_result_message(
                                provider=request.provider,
                                tool_name=tool_name,
                                tool_call_id=tool_call_id,
                                content=json.dumps(tool_content, ensure_ascii=True),
                            )
                        )
                        executed_in_iteration = True

                    if approval_request_payload:
                        break

                    if tool_loop_stop_reason == "missing_orchestration_context":
                        break

                    if not executed_in_iteration:
                        # Tool calls were returned but none could execute; feed tool errors back in next pass.
                        continue

                    forced_tool_calls = _build_orchestration_followthrough_tool_calls(
                        conversation_type=effective_conversation_type,
                        latest_user_message=capture_intent_message_hint,
                        executed_tool_calls=executed_tool_calls,
                        available_tool_names=resolved_tool_names,
                        mcp_scope=mcp_scope,
                        digest_schedule_config=digest_schedule_config,
                    )
                    if forced_tool_calls:
                        # Preserve deterministic synthetic chains (capture/digest/cross-pollination)
                        # before handing control back to the provider model.
                        continue
                else:
                    tool_loop_stop_reason = "max_tool_iterations_exceeded"

            if approval_request_payload:
                response_content = APPROVAL_REQUIRED_RESPONSE_TEXT
                result = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": response_content,
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "approval_required": True,
                    "approval_request": approval_request_payload,
                }
            elif approval_resolution_payload and approval_resolution_payload.get("status") == "rejected":
                response_content = (
                    "Understood. I did not run mutating tool "
                    f"'{approval_resolution_payload.get('tool')}'."
                )
                result = result or {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": response_content,
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            else:
                response_content = extract_response_content(result)
                if (
                    not response_content
                    and isinstance(approval_resolution_payload, dict)
                    and str(approval_resolution_payload.get("status") or "").strip().lower()
                    == "approved"
                ):
                    approval_success_message = _build_approval_execution_success_message(
                        executed_tool_calls=executed_tool_calls,
                        approval_resolution_payload=approval_resolution_payload,
                    )
                    if approval_success_message:
                        response_content = approval_success_message
                        result = _set_result_primary_content(result, response_content)
                elif not response_content and tool_loop_stop_reason == "missing_orchestration_context":
                    response_content = (
                        "I need canonical scope context before I can run mutating tools. "
                        "Please run scaffold/bootstrap steps or let me create missing required files first."
                    )
                    result = result or {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": response_content,
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                elif not response_content and tool_loop_stop_reason == "max_tool_iterations_exceeded":
                    response_content = "Tool execution stopped after reaching max iterations."
                    result = result or {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": response_content,
                                },
                                "finish_reason": "length",
                            }
                        ]
                    }
                elif not response_content and tool_loop_stop_reason == "provider_timeout":
                    response_content = _provider_timeout_fallback_response(
                        effective_conversation_type
                    )
                    result = result or {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": response_content,
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }

            (
                response_content,
                _citation_suffix,
                citation_paths,
                citation_appended,
            ) = _apply_grounding_citations_if_needed(
                response_content=response_content,
                executed_tool_calls=executed_tool_calls,
                latest_user_message=capture_intent_message_hint,
                tool_loop_stop_reason=tool_loop_stop_reason,
                default_scope_path=(
                    mcp_scope.get("mcp_project_slug")
                    if isinstance(mcp_scope, dict)
                    else None
                ),
            )
            if citation_appended:
                result = _set_result_primary_content(result, response_content)

            _finalize_pre_compaction_flush_status(
                tooling_metadata=mcp_tooling_metadata,
                tool_loop_stop_reason=tool_loop_stop_reason,
                tool_calls_executed_count=len(executed_tool_calls),
            )
            _finalize_digest_schedule_status(
                tooling_metadata=mcp_tooling_metadata,
                tool_loop_stop_reason=tool_loop_stop_reason,
                tool_calls_executed_count=len(executed_tool_calls),
            )
            mcp_tooling_metadata.update(
                {
                    "tool_loop_enabled": tool_loop_enabled or bool(executed_tool_calls),
                    "tool_loop_iterations": tool_loop_iterations,
                    "tool_loop_stop_reason": tool_loop_stop_reason,
                    "tool_calls_executed_count": len(executed_tool_calls),
                    "approval_required": bool(approval_request_payload),
                    "approval_resolved": bool(approval_resolution_payload),
                    **_build_provider_timing_metadata(
                        provider_timeout_seconds=mcp_provider_timeout_seconds,
                        provider_call_latencies_ms=provider_call_latencies_ms,
                        provider_timeout_count=provider_timeout_count,
                    ),
                }
            )
            if citation_paths:
                mcp_tooling_metadata["response_citations"] = citation_paths
                mcp_tooling_metadata["response_citations_appended"] = citation_appended

            delivery_handoff_payload = _build_digest_delivery_handoff_payload(
                conversation_type=effective_conversation_type,
                response_content=response_content,
                conversation_id=str(conversation.id),
                provider=request.provider,
                model=request.model,
                digest_schedule_config=digest_schedule_config,
                tooling_metadata=mcp_tooling_metadata,
            )
            if isinstance(delivery_handoff_payload, dict):
                delivery_handoff_payload = await _attach_digest_delivery_delivery_state(
                    handoff_payload=delivery_handoff_payload,
                    user_id=user_id,
                    tooling_metadata=mcp_tooling_metadata,
                    send_config=digest_delivery_send_config,
                )
                mcp_tooling_metadata["digest_delivery_handoff"] = delivery_handoff_payload

            elapsed_time = time.time() - start_time
            print(f"Chat completion result: {result}")
            
            # Estimate token count (this is a rough estimate)
            token_count = len(response_content.split()) * 1.3  # Rough estimate: words * 1.3
            tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0
            
            # Store the LLM response in the database with persona metadata
            message_metadata = {
                "token_count": int(token_count),
                "tokens_per_second": round(tokens_per_second, 1),
                "model": request.model,
                "temperature": provider_params.get("temperature", 0.7),
                "streaming": False,
                "mcp": {
                    **mcp_tooling_metadata,
                    "tools_passed_count": len(resolved_tools),
                    "tool_calls_executed": executed_tool_calls,
                },
            }
            if approval_request_payload:
                message_metadata["mcp"]["approval_request"] = approval_request_payload
            if approval_resolution_payload:
                message_metadata["mcp"]["approval_resolution"] = approval_resolution_payload
            
            # Add persona metadata if persona was used
            if request.persona_id:
                message_metadata.update({
                    "persona_id": request.persona_id,
                    "persona_applied": bool(request.persona_system_prompt),
                    "persona_model_settings_applied": bool(request.persona_model_settings)
                })
            
            db_message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                sender="llm",
                message=response_content,
                message_metadata=message_metadata
            )
            db.add(db_message)
            
            # Update the conversation's updated_at timestamp
            conversation.updated_at = db_message.created_at
            
            await db.commit()
            
            # Add conversation_id to the result
            result["conversation_id"] = conversation.id
            result["tooling_state"] = {
                **mcp_tooling_metadata,
                "tools_passed_count": len(resolved_tools),
            }
            if approval_request_payload:
                result["approval_required"] = True
                result["approval_request"] = approval_request_payload
            if approval_resolution_payload:
                result["approval_resolution"] = approval_resolution_payload
            if isinstance(delivery_handoff_payload, dict):
                result["delivery_handoff"] = delivery_handoff_payload
            
            return result
    except HTTPException:
        # Re-raise HTTP exceptions with their original status codes and details
        raise
    except Exception as e:
        logger.error(f"Exception in chat_completion: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error in chat completion: {str(e)}. Please try again later or contact support."
        )


# Test routes for direct testing (disabled in production)
if TEST_ROUTES_ENABLED:
    test_router = APIRouter(prefix="/test", tags=["ai_test"])
    
    @test_router.post("/ollama/generate")
    async def test_ollama_generate(
        prompt: str = Body(..., description="Text prompt"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        """Test route for Ollama text generation."""
        print(f"Test route called with: prompt={prompt}, model={model}, stream={stream}, server_url={server_url}")
        try:
            # Create provider instance directly
            provider = OllamaProvider()
            await provider.initialize({
                "server_url": server_url,
                "api_key": "",  # No API key for local testing
                "server_name": "Test Ollama Server"
            })
            
            # Set up parameters
            params = {
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            print(f"Initialized provider with server_url={server_url}, calling with params={params}")
            
            # Handle streaming vs. non-streaming
            if stream:
                async def stream_generator():
                    try:
                        async for chunk in provider.generate_stream(prompt, model, params):
                            print(f"Streaming chunk: {chunk}")
                            yield f"data: {json.dumps(chunk)}\n\n"
                            
                            # Add an explicit flush marker
                            yield ""
                        yield "data: [DONE]\n\n"
                    except Exception as stream_error:
                        print(f"Error in stream_generator: {stream_error}")
                        error_json = json.dumps({
                            "error": True,
                            "message": f"Streaming error: {str(stream_error)}"
                        })
                        yield f"data: {error_json}\n\n"
                        yield "data: [DONE]\n\n"
                
                # Add headers to prevent buffering
                headers = {
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",  # Disable Nginx buffering
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream"
                }
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers=headers
                )
            else:
                print(f"Calling generate_text with prompt={prompt}, model={model}")
                result = await provider.generate_text(prompt, model, params)
                print(f"Result from generate_text: {result}")
                return result
        except Exception as e:
            print(f"Exception in test_ollama_generate: {e}")
            import traceback
            traceback.print_exc()
            return {
                "error": True,
                "message": f"Test route error: {str(e)}"
            }
    
    @test_router.post("/ollama/llmchat")
    async def test_ollama_chat(
        messages: List[Dict[str, Any]] = Body(..., description="Chat messages"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        from langchain_community.llms import Ollama
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        try:
            # Fallback input from messages
            user_input = messages[0].get("content", "Hello") if messages else "Hello"

            # Build chain using LangChain
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a helpful assistant."),
                ("human", "{input}")
            ])
            llm = Ollama(model=model, base_url=server_url)
            chain = prompt | llm | StrOutputParser()

            # Streaming generator
            async def stream_generator():
                for chunk in chain.stream({"input": user_input}):
                    print("[🔹] Streaming chunk:", chunk)
                    yield f"data: {chunk}\n\n"
                    await asyncio.sleep(0.01)

                yield "data: [DONE]\n\n"

            if stream:
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream"
                    }
                )
            else:
                # For non-streaming response
                result = chain.invoke({"input": user_input})
                return {"answer": result}

        except Exception as e:
            print("❌ Exception in test_ollama_chat:", e)
            return {
                "error": True,
                "message": str(e)
            }


    @test_router.post("/ollama/chat")
    async def test_ollama_chat(
        messages: List[Dict[str, Any]] = Body(..., description="Chat messages"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        print(f"Test chat route called with: messages={messages}, model={model}, stream={stream}, server_url={server_url}")
        try:
            provider = OllamaProvider()
            await provider.initialize({
                "server_url": server_url,
                "api_key": "",
                "server_name": "Test Ollama Server"
            })

            params = {
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            if stream:
                async def stream_generator():
                    try:
                        async for chunk in provider.chat_completion_stream(messages, model, params):
                            # content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            # print(f"Streaming chunk: {content}")
                            # yield f"data: {content}\n\n"
                            yield f"data: {json.dumps(chunk)}\n\n"
                            await asyncio.sleep(0.01)
                        yield "data: [DONE]\n\n"

                    except Exception as stream_error:
                        print(f"Streaming error: {stream_error}")
                        yield "data: [DONE]\n\n"


                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream"
                    }
                )
            else:
                result = await provider.chat_completion(messages, model, params)
                return result

        except Exception as e:
            print(f"Exception in chat handler: {e}")
            return {
                "error": True,
                "message": str(e)
            }

        
    @test_router.get("/test/stream")
    async def minimal_stream_test():
        async def event_stream():
            for i in range(5):
                yield f"data: chunk {i} at {time.time()}\n\n"
                print(f"Yielded chunk {i}")
                await asyncio.sleep(1)

            yield "data: [DONE]\n\n"

        headers = {
            "Cache-Control": "no-cache",
            "Content-Type": "text/event-stream",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Important if using nginx
        }

        return StreamingResponse(event_stream(), headers=headers)


    @test_router.get("/test/stream-ollama-direct")
    async def stream_ollama_direct_test():
        from app.ai_providers.ollama import OllamaProvider

        async def stream():
            provider = OllamaProvider()
            await provider.initialize({"server_url": "http://localhost:11434"})
            async for chunk in provider._stream_ollama_api("Give me 5 dragon Names", "hf.co/Triangle104/Dolphin3.0-R1-Mistral-24B-Q6_K-GGUF:latest", {"temperature": 0.7}):
                print("STREAM CHUNK:", chunk)
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.01)
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive"
            }
        )
    
    # Include the test router in the main router
    router.include_router(test_router)
