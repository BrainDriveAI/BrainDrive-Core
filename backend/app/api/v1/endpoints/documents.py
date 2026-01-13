"""
API endpoints for document processing and text extraction.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.core.rate_limit_deps import rate_limit_user
from app.models.user import User
from app.services.documents import (
    ExtractionOptions,
    ExtractionError,
    SizeExceeded,
    UnsupportedType,
    get_document_processor,
)
from app.services.documents.types import DocumentContent

router = APIRouter()
logger = logging.getLogger(__name__)

document_processor = get_document_processor()
MAX_FILE_SIZE = document_processor.max_file_size
TEXT_CONTEXT_MAX_TOTAL_CHARS = document_processor.chunk_config.max_total_chars
TEXT_CONTEXT_MAX_SEGMENTS = document_processor.chunk_config.max_segments
TEXT_CONTEXT_MAX_CHARS_PER_SEGMENT = document_processor.chunk_config.max_chars_per_segment
TEXT_CONTEXT_OVERLAP = document_processor.chunk_config.overlap


def _serialize_content(filename: str | None, upload_content_type: Optional[str], content: DocumentContent) -> Dict[str, Any]:
    response_data: Dict[str, Any] = {
        "filename": filename,
        "file_type": content.file_type,
        "content_type": content.content_type or upload_content_type,
        "file_size": content.source_bytes,
        "extracted_text": content.text,
        "text_length": len(content.text),
        "processing_success": True,
    }

    if content.detected_type:
        response_data["detected_type"] = content.detected_type
    if content.metadata:
        response_data["metadata"] = content.metadata
    if content.warnings:
        response_data["warnings"] = content.warnings
    if content.chunks:
        response_data["chunks"] = [chunk.__dict__ for chunk in content.chunks]
    if content.chunk_metadata:
        response_data["chunk_metadata"] = content.chunk_metadata

    return response_data


@router.post("/process")
async def process_document(
    file: UploadFile = File(...),
    include_chunks: bool = Query(False, description="Include chunked segments in the response"),
    max_chars: Optional[int] = Query(None, ge=1, le=document_processor.max_output_chars, description="Optional output character cap"),
    preserve_layout: bool = Query(False, description="Preserve PDF layout when possible"),
    strip_boilerplate: bool = Query(True, description="Strip boilerplate for HTML inputs"),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
    _: None = Depends(rate_limit_user(limit=20, window_seconds=60))
):
    options = ExtractionOptions(
        preserve_layout=preserve_layout,
        strip_boilerplate=strip_boilerplate,
        max_chars=max_chars,
        include_chunks=include_chunks,
    )

    try:
        data = await file.read()
        content = await run_in_threadpool(
            document_processor.process_bytes,
            data,
            file.filename,
            file.content_type,
            options,
        )
        logger.info("Successfully processed document: %s (%s)", file.filename, content.file_type)
        return JSONResponse(content=_serialize_content(file.filename, file.content_type, content))
    except SizeExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except UnsupportedType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - unexpected path
        logger.error("Unexpected error processing document %s: %s", file.filename, exc)
        raise HTTPException(status_code=500, detail="Error processing document") from exc


@router.post("/process-multiple")
async def process_multiple_documents(
    files: List[UploadFile] = File(...),
    include_chunks: bool = Query(False),
    max_chars: Optional[int] = Query(None, ge=1, le=document_processor.max_output_chars),
    preserve_layout: bool = Query(False),
    strip_boilerplate: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
    _: None = Depends(rate_limit_user(limit=10, window_seconds=60))
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Too many files. Maximum is 10 files")

    options = ExtractionOptions(
        preserve_layout=preserve_layout,
        strip_boilerplate=strip_boilerplate,
        max_chars=max_chars,
        include_chunks=include_chunks,
    )

    results = []
    for file in files:
        try:
            data = await file.read()
            content = await run_in_threadpool(
                document_processor.process_bytes,
                data,
                file.filename,
                file.content_type,
                options,
            )
            results.append(_serialize_content(file.filename, file.content_type, content))
        except SizeExceeded as exc:
            results.append({
                "filename": file.filename,
                "error": str(exc),
                "processing_success": False,
            })
        except (UnsupportedType, ExtractionError) as exc:
            results.append({
                "filename": file.filename,
                "error": str(exc),
                "processing_success": False,
            })
        except Exception as exc:  # pragma: no cover - unexpected path
            logger.error("Error processing file %s: %s", file.filename, exc)
            results.append({
                "filename": file.filename,
                "error": str(exc),
                "processing_success": False,
            })

    return JSONResponse(content={
        "results": results,
        "total_files": len(files),
        "successful_files": len([r for r in results if r.get("processing_success", False)]),
        "failed_files": len([r for r in results if not r.get("processing_success", False)]),
    })


@router.post("/process-text-context")
async def process_text_context(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
    _: None = Depends(rate_limit_user(limit=20, window_seconds=60))
):
    try:
        data = await file.read()
        content = document_processor.process_bytes(
            data=data,
            filename=file.filename,
            content_type=file.content_type,
            options=ExtractionOptions(),
        )
    except SizeExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except UnsupportedType:
        raise HTTPException(status_code=400, detail="Unsupported file type for context seeding.")
    except ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    chunk_result = document_processor.chunk_text(content.text)

    total_input_chars = len(content.text)
    truncated_input = total_input_chars > TEXT_CONTEXT_MAX_TOTAL_CHARS

    response_data = {
        "filename": file.filename,
        "file_type": content.file_type,
        "content_type": file.content_type,
        "file_size": content.source_bytes,
        "total_input_chars": total_input_chars,
        "segments": [chunk.__dict__ for chunk in chunk_result["segments"]],
        "segment_count": chunk_result.get("segment_count", len(chunk_result["segments"])),
        "truncated": chunk_result["truncated"] or truncated_input,
        "max_total_chars": TEXT_CONTEXT_MAX_TOTAL_CHARS,
        "max_segments": TEXT_CONTEXT_MAX_SEGMENTS,
        "max_chars_per_segment": TEXT_CONTEXT_MAX_CHARS_PER_SEGMENT,
        "overlap_chars": TEXT_CONTEXT_OVERLAP,
        "processing_success": True,
    }

    if response_data["truncated"]:
        response_data["truncation_notice"] = (
            "Document was truncated to fit context limits. Only the first segments are included."
        )

    if content.warnings:
        response_data["warnings"] = content.warnings

    logger.info(
        "Processed text context file %s into %s segments (total chars: %s, truncated: %s)",
        file.filename,
        response_data["segment_count"],
        total_input_chars,
        response_data["truncated"],
    )

    return JSONResponse(content=response_data)


@router.get("/supported-types")
async def get_supported_file_types():
    supported = document_processor.supported_types()
    return {
        "supported_types": supported["supported_types"],
        "extensions": supported["extensions"],
        "canonical_types": supported["canonical_types"],
        "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
        "max_files_per_request": 10,
    }
