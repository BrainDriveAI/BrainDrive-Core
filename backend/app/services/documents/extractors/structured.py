import io
import json
import logging
from email import policy
from email.parser import BytesParser
from typing import List

from bs4 import BeautifulSoup
from ebooklib import epub
from html2text import HTML2Text

from app.services.documents.exceptions import ExtractionError
from app.services.documents.extractors.base import BaseExtractor
from app.services.documents.extractors.textual import _html_to_text
from app.services.documents.types import DocumentContent, ExtractionOptions

logger = logging.getLogger(__name__)


class JsonExtractor(BaseExtractor):
    canonical_types: set[str] = {"json"}
    mime_types: set[str] = {"application/json", "text/json"}
    extensions: set[str] = {"json"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        try:
            parsed = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception as exc:
            raise ExtractionError(f"Unable to parse JSON: {exc}") from exc

        text = json.dumps(parsed, indent=2, ensure_ascii=False)
        metadata = {"type": type(parsed).__name__}
        if isinstance(parsed, list):
            metadata["items"] = len(parsed)
        if isinstance(parsed, dict):
            metadata["keys"] = list(parsed.keys())

        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="json",
            content_type=content_type or "application/json",
            warnings=warnings,
        )


class EmailExtractor(BaseExtractor):
    canonical_types: set[str] = {"eml"}
    mime_types: set[str] = {"message/rfc822"}
    extensions: set[str] = {"eml"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        try:
            message = BytesParser(policy=policy.default).parsebytes(data)
        except Exception as exc:
            raise ExtractionError(f"Unable to parse email: {exc}") from exc

        parts: List[str] = []
        attachments = 0

        if message.is_multipart():
            for part in message.iter_parts():
                if part.is_attachment():
                    attachments += 1
                    continue
                content_type_part = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload = None
                if payload:
                    if content_type_part == "text/html":
                        html_payload = str(payload)
                        if options.strip_boilerplate:
                            soup = BeautifulSoup(html_payload, "html.parser")
                            for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
                                tag.decompose()
                            html_payload = str(soup)
                        parts.append(_html_to_text(html_payload))
                    else:
                        parts.append(str(payload))
        else:
            try:
                payload = message.get_content()
                if message.get_content_type() == "text/html":
                    html_payload = str(payload)
                    if options.strip_boilerplate:
                        soup = BeautifulSoup(html_payload, "html.parser")
                        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
                            tag.decompose()
                        html_payload = str(soup)
                    parts.append(_html_to_text(html_payload))
                elif payload:
                    parts.append(str(payload))
            except Exception as exc:  # pragma: no cover - rare path
                warnings.append(f"Failed to read email body: {exc}")

        metadata = {
            "subject": message.get("subject"),
            "from": message.get("from"),
            "to": message.get("to"),
            "attachments": attachments,
        }

        text = "\n\n".join([p for p in parts if p])
        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="eml",
            content_type=content_type or "message/rfc822",
            warnings=warnings,
        )


class EpubExtractor(BaseExtractor):
    canonical_types: set[str] = {"epub"}
    mime_types: set[str] = {"application/epub+zip"}
    extensions: set[str] = {"epub"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        try:
            book = epub.read_epub(io.BytesIO(data))
        except Exception as exc:
            raise ExtractionError(f"Unable to parse EPUB: {exc}") from exc

        html_converter = HTML2Text()
        html_converter.ignore_links = False
        html_converter.body_width = 0

        chapters: List[str] = []
        for item in book.get_items_of_type(epub.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")
            html_text = html_converter.handle(str(soup))
            if html_text:
                chapters.append(html_text)

        metadata = {"chapters": len(chapters)}
        text = "\n\n".join(chapters)
        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="epub",
            content_type=content_type or "application/epub+zip",
            warnings=warnings,
        )
