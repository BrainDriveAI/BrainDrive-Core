import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import filetype
from fastapi import UploadFile

from app.services.documents.exceptions import ExtractionError, SizeExceeded, UnsupportedType
from app.services.documents.extractors.base import BaseExtractor
from app.services.documents.extractors.office import CsvExtractor, DocxExtractor, PptxExtractor, RtfExtractor, SpreadsheetExtractor
from app.services.documents.extractors.pdf import PdfExtractor
from app.services.documents.extractors.structured import EmailExtractor, EpubExtractor, JsonExtractor
from app.services.documents.extractors.textual import HtmlExtractor, MarkdownExtractor, TextExtractor, XmlExtractor
from app.services.documents.types import ChunkConfig, DocumentChunk, DocumentContent, ExtractionOptions

logger = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(
        self,
        extractors: Iterable[BaseExtractor],
        max_file_size: int = 10 * 1024 * 1024,
        chunk_config: ChunkConfig | None = None,
        max_output_chars: int = 500_000,
    ) -> None:
        self.max_file_size = max_file_size
        self.chunk_config = chunk_config or ChunkConfig()
        self.max_output_chars = max_output_chars
        self.registry: Dict[str, BaseExtractor] = {}
        self.mime_lookup: Dict[str, str] = {}
        self.extension_lookup: Dict[str, str] = {}

        for extractor in extractors:
            primary_type = getattr(extractor, "primary_type", None) or next(iter(extractor.canonical_types))
            for canonical in extractor.canonical_types:
                self.registry[canonical] = extractor
            for mime in extractor.mime_types:
                self.mime_lookup[mime] = primary_type
            for ext in extractor.extensions:
                mapped_type = ext if ext in extractor.canonical_types else primary_type
                self.extension_lookup[ext] = mapped_type

    async def process_upload(self, upload: UploadFile, options: ExtractionOptions) -> DocumentContent:
        data = await upload.read()
        return self.process_bytes(data=data, filename=upload.filename, content_type=upload.content_type, options=options)

    def process_bytes(
        self, data: bytes, filename: Optional[str], content_type: Optional[str], options: ExtractionOptions
    ) -> DocumentContent:
        if len(data) > self.max_file_size:
            raise SizeExceeded(f"File too large. Maximum size is {self.max_file_size // (1024 * 1024)}MB")

        detected_type, detection_warnings, detected_content_type = self.detect_type(filename, content_type, data)
        extractor = self.registry.get(detected_type)
        if not extractor:
            raise UnsupportedType(f"Unsupported file type: {detected_type}")

        content = extractor.extract(data, filename, detected_content_type or content_type, options)
        content.warnings = detection_warnings + content.warnings
        content.detected_type = detected_type
        content.source_bytes = len(data)

        if options.max_chars and len(content.text) > options.max_chars:
            content.warnings.append(f"Text truncated to {options.max_chars} characters per request limit")
            content.text = content.text[: options.max_chars]

        if self.max_output_chars and len(content.text) > self.max_output_chars:
            content.warnings.append(f"Text truncated to {self.max_output_chars} characters to maintain performance")
            content.text = content.text[: self.max_output_chars]

        if options.include_chunks:
            chunk_result = self.chunk_text(content.text)
            content.chunks = chunk_result["segments"]
            content.chunk_metadata = {
                "truncated": chunk_result["truncated"],
                "total_chars": chunk_result["total_chars"],
                "segment_count": chunk_result.get("segment_count"),
            }

        return content

    def detect_type(self, filename: Optional[str], content_type: Optional[str], data: bytes) -> Tuple[str, List[str], Optional[str]]:
        warnings: List[str] = []
        detected_mime: Optional[str] = None
        # 1) Magic detection
        try:
            kind = filetype.guess(data)
            if kind:
                detected_mime = kind.mime
                by_magic = self.mime_lookup.get(kind.mime) or self.extension_lookup.get(kind.extension)
                if by_magic:
                    return by_magic, warnings, detected_mime
        except Exception as exc:  # pragma: no cover - library edge
            warnings.append(f"Signature detection failed: {exc}")

        # 2) Content-Type header
        if content_type and content_type in self.mime_lookup:
            return self.mime_lookup[content_type], warnings, content_type

        # 3) Extension heuristics
        ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
        if ext and ext in self.extension_lookup:
            return self.extension_lookup[ext], warnings, content_type

        # 4) Lightweight sniffing
        if self._looks_like_json(data):
            return "json", warnings, content_type
        if self._looks_like_csv(data):
            return "csv", warnings, content_type
        if self._looks_like_text(data):
            return "txt", warnings, content_type

        raise UnsupportedType("Unsupported or unknown file type")

    def chunk_text(self, text: str) -> Dict[str, Any]:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]

        segments: List[DocumentChunk] = []
        truncated = False
        total_chars = 0

        for para in paragraphs:
            remaining = para
            while remaining:
                if total_chars >= self.chunk_config.max_total_chars or len(segments) >= self.chunk_config.max_segments:
                    truncated = True
                    break

                chunk = remaining[: self.chunk_config.max_chars_per_segment]
                has_more_in_para = len(remaining) > self.chunk_config.max_chars_per_segment
                remaining = (
                    remaining[
                        self.chunk_config.max_chars_per_segment - self.chunk_config.overlap :
                    ]
                    if has_more_in_para
                    else ""
                )

                segments.append(
                    DocumentChunk(
                        index=len(segments) + 1,
                        text=chunk,
                        char_count=len(chunk),
                    )
                )
                total_chars += len(chunk)

                if len(segments) >= self.chunk_config.max_segments or total_chars >= self.chunk_config.max_total_chars:
                    truncated = True
                    break
            if truncated and (total_chars >= self.chunk_config.max_total_chars or len(segments) >= self.chunk_config.max_segments):
                break

        if not paragraphs and cleaned:
            base_text = cleaned[: self.chunk_config.max_chars_per_segment]
            segments.append(DocumentChunk(index=1, text=base_text, char_count=len(base_text)))
            total_chars = len(base_text)
            truncated = len(cleaned) > len(base_text)

        return {
            "segments": segments,
            "segment_count": len(segments),
            "total_chars": total_chars,
            "truncated": truncated,
        }

    def supported_types(self) -> Dict[str, Any]:
        return {
            "supported_types": self.mime_lookup,
            "extensions": sorted(set(self.extension_lookup.keys())),
            "canonical_types": sorted(set(self.registry.keys())),
        }

    def _looks_like_json(self, data: bytes) -> bool:
        sample = data.strip()[:1024].lstrip()
        if not sample:
            return False
        if sample[:1] in (b"{", b"["):
            try:
                json.loads(sample.decode("utf-8", errors="ignore"))
                return True
            except Exception:
                return False
        return False

    def _looks_like_csv(self, data: bytes) -> bool:
        sample = data.splitlines()[:5]
        comma_lines = [line for line in sample if b"," in line]
        return len(comma_lines) >= 2

    def _looks_like_text(self, data: bytes) -> bool:
        try:
            data.decode("utf-8")
            return True
        except Exception:
            return False


def build_document_processor() -> DocumentProcessor:
    extractors: List[BaseExtractor] = [
        PdfExtractor(),
        DocxExtractor(),
        RtfExtractor(),
        PptxExtractor(),
        SpreadsheetExtractor(),
        CsvExtractor(),
        JsonExtractor(),
        TextExtractor(),
        MarkdownExtractor(),
        HtmlExtractor(),
        XmlExtractor(),
        EmailExtractor(),
        EpubExtractor(),
    ]
    return DocumentProcessor(extractors=extractors)


def get_document_processor() -> DocumentProcessor:
    global _DOCUMENT_PROCESSOR
    try:
        processor = _DOCUMENT_PROCESSOR
    except NameError:
        processor = build_document_processor()
        _DOCUMENT_PROCESSOR = processor
    return processor
