import io
import logging
import re

import pdfplumber
import PyPDF2

from app.services.documents.exceptions import ExtractionError
from app.services.documents.extractors.base import BaseExtractor
from app.services.documents.types import DocumentContent, ExtractionOptions

logger = logging.getLogger(__name__)


class PdfExtractor(BaseExtractor):
    canonical_types: set[str] = {"pdf"}
    mime_types: set[str] = {"application/pdf"}
    extensions: set[str] = {"pdf"}

    def _join_soft_hyphens(self, text: str) -> str:
        text = re.sub(r"-\n(?=[a-z])", "", text)
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        return text

    def extract(
        self,
        data: bytes,
        filename: str | None,
        content_type: str | None,
        options: ExtractionOptions,
    ) -> DocumentContent:
        warnings: list[str] = []
        metadata: dict[str, int] = {}
        text = ""

        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if getattr(pdf, "is_encrypted", False):
                    try:
                        pdf.decrypt("")
                    except Exception as decrypt_error:  # pragma: no cover - rare path
                        raise ExtractionError("Encrypted PDF files are not supported") from decrypt_error

                page_chunks: list[str] = []
                metadata["pages"] = len(pdf.pages)

                for idx, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text(layout=options.preserve_layout) or ""
                    page_text = self._join_soft_hyphens(self.normalize_whitespace(page_text))
                    if page_text:
                        page_chunks.append(f"--- Page {idx} ---\n{page_text}")

                text = "\n\n".join(page_chunks).strip()
        except Exception as primary_error:
            warnings.append(f"pdfplumber fallback: {primary_error}")
            text = self._fallback_pypdf(data, metadata, warnings)

        text = self.trim_text(text, warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="pdf",
            content_type=content_type or "application/pdf",
            warnings=warnings,
        )

    def _fallback_pypdf(self, data: bytes, metadata: dict[str, int], warnings: list[str]) -> str:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception as decrypt_error:  # pragma: no cover - rare path
                    raise ExtractionError("Encrypted PDF files are not supported") from decrypt_error

            page_texts: list[str] = []
            metadata["pages"] = len(getattr(reader, "pages", []))
            for idx, page in enumerate(reader.pages, start=1):
                try:
                    extracted = page.extract_text() or ""
                except Exception as e:  # pragma: no cover - parser edge case
                    warnings.append(f"Skipped page {idx} due to error: {e}")
                    continue
                cleaned = self._join_soft_hyphens(self.normalize_whitespace(extracted))
                if cleaned:
                    page_texts.append(f"--- Page {idx} ---\n{cleaned}")

            return "\n\n".join(page_texts).strip()
        except Exception as fallback_error:
            logger.error("Failed to parse PDF via pdfplumber and PyPDF2: %s", fallback_error)
            raise ExtractionError(f"Unable to parse PDF: {fallback_error}") from fallback_error
