import re
from typing import List

from app.services.documents.types import DocumentContent, ExtractionOptions


class BaseExtractor:
    """Base class for document extractors."""

    canonical_types: set[str] = set()
    mime_types: set[str] = set()
    extensions: set[str] = set()
    max_output_chars: int = 300_000

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:  # pragma: no cover - interface
        raise NotImplementedError

    def normalize_whitespace(self, text: str) -> str:
        """Collapse excessive whitespace and normalize newlines."""
        cleaned = re.sub(r"\r\n?|\n", "\n", text or "")
        lines = [line.rstrip() for line in cleaned.split("\n")]
        normalized = "\n".join(lines)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def trim_text(self, text: str, warnings: List[str]) -> str:
        if self.max_output_chars and len(text) > self.max_output_chars:
            warnings.append(
                f"Extracted text truncated to {self.max_output_chars} characters to protect downstream processing."
            )
            return text[: self.max_output_chars]
        return text
