from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DocumentChunk:
    index: int
    text: str
    char_count: int


@dataclass
class DocumentContent:
    text: str
    metadata: Dict[str, Any]
    file_type: str
    content_type: str
    warnings: List[str] = field(default_factory=list)
    chunks: Optional[List[DocumentChunk]] = None
    detected_type: Optional[str] = None
    source_bytes: Optional[int] = None
    chunk_metadata: Optional[Dict[str, Any]] = None


@dataclass
class ExtractionOptions:
    preserve_layout: bool = False
    strip_boilerplate: bool = True
    max_chars: Optional[int] = None
    include_chunks: bool = False


@dataclass
class ChunkConfig:
    max_total_chars: int = 60000
    max_segments: int = 25
    max_chars_per_segment: int = 2000
    overlap: int = 200
