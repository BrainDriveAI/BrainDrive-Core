import logging
from typing import Optional

from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from html2text import HTML2Text
import markdown as md

from app.services.documents.exceptions import ExtractionError
from app.services.documents.extractors.base import BaseExtractor
from app.services.documents.types import DocumentContent, ExtractionOptions

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    converter = HTML2Text()
    converter.ignore_images = True
    converter.ignore_links = False
    converter.body_width = 0
    converter.skip_internal_links = True
    return converter.handle(html)


class TextExtractor(BaseExtractor):
    canonical_types: set[str] = {"txt"}
    mime_types: set[str] = {"text/plain"}
    extensions: set[str] = {"txt", "text", "log"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        encoding = self._detect_encoding(data)
        try:
            text = data.decode(encoding or "utf-8", errors="ignore")
        except Exception as exc:  # pragma: no cover - decode edge
            raise ExtractionError(f"Unable to decode text: {exc}") from exc

        metadata = {"encoding": encoding or "utf-8"}
        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="txt",
            content_type=content_type or "text/plain",
            warnings=warnings,
        )

    def _detect_encoding(self, data: bytes) -> Optional[str]:
        result = from_bytes(data).best()
        return result.encoding if result else None


class MarkdownExtractor(BaseExtractor):
    canonical_types: set[str] = {"md"}
    mime_types: set[str] = {"text/markdown", "text/x-markdown"}
    extensions: set[str] = {"md", "markdown"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        encoding = from_bytes(data).best()
        text_content = data.decode(encoding.encoding if encoding else "utf-8", errors="ignore")
        html = md.markdown(text_content)
        plain = _html_to_text(html)
        metadata = {"encoding": encoding.encoding if encoding else "utf-8"}
        plain = self.trim_text(self.normalize_whitespace(plain), warnings)
        return DocumentContent(
            text=plain,
            metadata=metadata,
            file_type="md",
            content_type=content_type or "text/markdown",
            warnings=warnings,
        )


class HtmlExtractor(BaseExtractor):
    canonical_types: set[str] = {"html"}
    mime_types: set[str] = {"text/html", "application/xhtml+xml"}
    extensions: set[str] = {"html", "htm", "xhtml"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        encoding = from_bytes(data).best()
        html_content = data.decode(encoding.encoding if encoding else "utf-8", errors="ignore")
        soup = BeautifulSoup(html_content, "html.parser")

        if options.strip_boilerplate:
            for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
                tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else None
        text = _html_to_text(str(soup))
        metadata = {"title": title} if title else {}

        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata=metadata,
            file_type="html",
            content_type=content_type or "text/html",
            warnings=warnings,
        )


class XmlExtractor(BaseExtractor):
    canonical_types: set[str] = {"xml"}
    mime_types: set[str] = {"text/xml", "application/xml"}
    extensions: set[str] = {"xml"}

    def extract(self, data: bytes, filename: str | None, content_type: str | None, options: ExtractionOptions) -> DocumentContent:
        warnings: list[str] = []
        try:
            soup = BeautifulSoup(data, "xml")
            text = soup.get_text("\n")
        except Exception as exc:
            raise ExtractionError(f"Unable to parse XML: {exc}") from exc

        text = self.trim_text(self.normalize_whitespace(text), warnings)
        return DocumentContent(
            text=text,
            metadata={"root": soup.name if soup else "xml"},
            file_type="xml",
            content_type=content_type or "text/xml",
            warnings=warnings,
        )
