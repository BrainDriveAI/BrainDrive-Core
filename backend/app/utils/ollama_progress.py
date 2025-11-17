from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

DEFAULT_DIGEST_KEY = "__overall__"
MIN_PROGRESS_PERCENT = 0
MAX_PROGRESS_PERCENT = 100


@dataclass
class OllamaProgressSnapshot:
    """Normalized progress information for an Ollama pull payload."""

    percent: Optional[int]
    stage: Optional[str]
    message: Optional[str]
    bucket: Optional[int]
    bucket_changed: bool
    completed_bytes: Optional[int]
    total_bytes: Optional[int]
    payload: Dict[str, Any]


class OllamaPullTracker:
    """Track Ollama pull progress and compute aggregate metrics."""

    def __init__(self, bucket_size: int = 5) -> None:
        self.bucket_size = max(1, bucket_size)
        self._digest_totals: Dict[str, int] = {}
        self._digest_completed: Dict[str, int] = {}
        self._last_bucket: Optional[int] = None
        self._last_payload: Optional[Dict[str, Any]] = None
        self._last_completed_bytes: Optional[int] = None
        self._last_total_bytes: Optional[int] = None
        self._last_percent: Optional[int] = None

    def process_payload(self, payload: Dict[str, Any]) -> OllamaProgressSnapshot:
        """Normalize a raw Ollama payload into a structured snapshot."""
        digest = _normalize_str(payload.get("digest"))
        digest_key = digest or DEFAULT_DIGEST_KEY

        total_value = _safe_int(payload.get("total"))
        if total_value is not None:
            previous_total = self._digest_totals.get(digest_key, 0)
            if total_value > previous_total:
                self._digest_totals[digest_key] = total_value

        completed_value = _safe_int(payload.get("completed"))
        if completed_value is not None:
            previous_completed = self._digest_completed.get(digest_key, 0)
            if completed_value > previous_completed:
                self._digest_completed[digest_key] = completed_value

        total_bytes = self._compute_total_bytes()
        completed_bytes = self._compute_completed_bytes()

        per_event_total = total_value
        per_event_completed = completed_value

        derived_percent = self._percent_from_bytes(completed_bytes, total_bytes)
        explicit_percent = _coerce_percent(payload.get("progress"))
        percent = derived_percent if derived_percent is not None else explicit_percent
        if percent is None:
            percent = self._percent_from_bytes(per_event_completed, per_event_total)
            if percent is not None and per_event_total is not None and per_event_completed is not None:
                completed_bytes = per_event_completed
                total_bytes = per_event_total

        bucket = self.bucket_for_percent(percent)
        bucket_changed = bucket is not None and bucket != self._last_bucket
        if bucket is not None:
            self._last_bucket = bucket

        status = _normalize_str(payload.get("status"))
        detail = _normalize_str(payload.get("detail"))
        message = status or detail or "Processing model download"
        stage = derive_stage(status or detail)

        normalized_payload: Dict[str, Any] = dict(payload)
        normalized_payload["status"] = status
        normalized_payload["detail"] = detail
        normalized_payload["digest"] = digest
        normalized_payload["message"] = message
        if total_bytes is not None:
            normalized_payload["total_bytes"] = total_bytes
        if completed_bytes is not None:
            normalized_payload["completed_bytes"] = completed_bytes
        if percent is not None:
            normalized_payload["progress_percent"] = percent
        if bucket is not None:
            normalized_payload["progress_bucket"] = bucket
        if stage:
            normalized_payload["stage"] = stage

        snapshot = OllamaProgressSnapshot(
            percent=percent,
            stage=stage,
            message=message,
            bucket=bucket,
            bucket_changed=bucket_changed,
            completed_bytes=completed_bytes,
            total_bytes=total_bytes,
            payload=normalized_payload,
        )

        self._last_payload = normalized_payload
        self._last_completed_bytes = completed_bytes
        self._last_total_bytes = total_bytes
        self._last_percent = percent

        return snapshot

    def build_progress_payload(
        self,
        *,
        percent: Optional[int],
        stage: Optional[str],
        message: Optional[str],
    ) -> Dict[str, Any]:
        """Return the latest payload merged with supplied summary details."""
        payload: Dict[str, Any] = dict(self._last_payload or {})
        if percent is not None:
            payload["progress_percent"] = percent
            bucket = self.bucket_for_percent(percent)
            if bucket is not None:
                payload["progress_bucket"] = bucket
        if self._last_completed_bytes is not None:
            payload["completed_bytes"] = self._last_completed_bytes
        if self._last_total_bytes is not None:
            payload["total_bytes"] = self._last_total_bytes
        if stage:
            payload["stage"] = stage
        if message:
            payload["message"] = message
        return payload

    def bucket_for_percent(self, percent: Optional[int]) -> Optional[int]:
        if percent is None:
            return None
        return max(0, percent) // self.bucket_size

    def _compute_total_bytes(self) -> Optional[int]:
        if not self._digest_totals:
            return None
        total = sum(self._digest_totals.values())
        return total or None

    def _compute_completed_bytes(self) -> Optional[int]:
        if not self._digest_totals:
            return None
        completed = 0
        for digest, total in self._digest_totals.items():
            completed_for_digest = min(self._digest_completed.get(digest, 0), total)
            completed += completed_for_digest
        return completed

    def _percent_from_bytes(
        self,
        completed_bytes: Optional[int],
        total_bytes: Optional[int],
    ) -> Optional[int]:
        if completed_bytes is None or total_bytes in (None, 0):
            return None
        ratio = completed_bytes / float(total_bytes)
        ratio = max(0.0, min(1.0, ratio))
        percent = int(ratio * 100)
        return _clamp_progress_percent(percent)


def derive_stage(status_text: Optional[str]) -> str:
    """Map Ollama status strings to user-friendly stages."""
    if not status_text:
        return "downloading"
    text = status_text.lower()
    if any(token in text for token in ("queued", "waiting", "pending", "prepare")):
        return "queued"
    if any(token in text for token in ("verify", "integrity")):
        return "verifying"
    if any(token in text for token in ("extract", "final", "unpack", "writing", "load")):
        return "finalizing"
    if "success" in text or "complete" in text:
        return "finalizing"
    return "downloading"


def _normalize_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_percent(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 1.0:
        numeric *= 100.0
    return _clamp_progress_percent(int(numeric))


def _clamp_progress_percent(percent: int) -> int:
    return max(MIN_PROGRESS_PERCENT, min(MAX_PROGRESS_PERCENT, percent))


__all__ = ["OllamaPullTracker", "OllamaProgressSnapshot", "derive_stage"]
