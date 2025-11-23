"""
API endpoints for document processing and text extraction.
"""
import os
import json
import csv
import io
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import PyPDF2
import pandas as pd

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

# Supported file types and their MIME types
SUPPORTED_FILE_TYPES = {
    'application/pdf': 'pdf',
    'text/plain': 'txt',
    'text/csv': 'csv',
    'application/json': 'json',
    'text/json': 'json',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-excel': 'xls',
    'text/markdown': 'md',
    'text/xml': 'xml',
    'text/html': 'html',
    'text/x-markdown': 'md'
}

# File size limit (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024
TEXT_CONTEXT_MAX_TOTAL_CHARS = 60000
TEXT_CONTEXT_MAX_SEGMENTS = 25
TEXT_CONTEXT_MAX_CHARS_PER_SEGMENT = 2000
TEXT_CONTEXT_OVERLAP = 200


def _chunk_text(text: str, max_chars: int, max_segments: int, overlap: int) -> Dict[str, Any]:
    """
    Split text into manageable chunks using paragraph/line boundaries where possible.
    """
    cleaned = text.replace('\r\n', '\n').replace('\r', '\n')
    paragraphs = [p.strip() for p in cleaned.split('\n\n') if p.strip()]
    
    segments = []
    truncated = False
    total_chars = 0
    
    for para in paragraphs:
        remaining = para
        while remaining:
            # Respect global total limit
            if total_chars >= TEXT_CONTEXT_MAX_TOTAL_CHARS:
                truncated = True
                break
            
            # Slice a chunk
            chunk = remaining[:max_chars]
            if len(remaining) > max_chars:
                truncated = True
            remaining = remaining[max_chars - overlap:] if len(remaining) > max_chars else ""
            
            segments.append(chunk)
            total_chars += len(chunk)
            
            if len(segments) >= max_segments:
                truncated = True
                break
        if truncated:
            break
    
    # Fallback if no paragraphs were split
    if not paragraphs and cleaned:
        base_text = cleaned[:max_chars]
        segments.append(base_text)
        total_chars = len(base_text)
        truncated = len(cleaned) > len(base_text)
    
    return {
        "segments": [
            {
                "index": idx + 1,
                "text": seg,
                "char_count": len(seg)
            }
            for idx, seg in enumerate(segments)
        ],
        "total_chars": total_chars,
        "truncated": truncated,
    }


def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from PDF file content."""
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing PDF file: {str(e)}")


def extract_text_from_csv(file_content: bytes) -> str:
    """Extract text from CSV file content."""
    try:
        # Try to read as CSV
        df = pd.read_csv(io.BytesIO(file_content))
        
        # Convert to string representation
        text = df.to_string(index=False)
        
        # Also add column names and basic stats
        text += f"\n\nColumns: {', '.join(df.columns.tolist())}"
        text += f"\nRows: {len(df)}"
        text += f"\nShape: {df.shape}"
        
        return text
    except Exception as e:
        logger.error(f"Error extracting text from CSV: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing CSV file: {str(e)}")


def extract_text_from_json(file_content: bytes) -> str:
    """Extract text from JSON file content."""
    try:
        data = json.loads(file_content.decode('utf-8'))
        
        # Pretty print the JSON
        text = json.dumps(data, indent=2, ensure_ascii=False)
        
        # Add basic structure info
        if isinstance(data, dict):
            text += f"\n\nKeys: {', '.join(data.keys())}"
        elif isinstance(data, list):
            text += f"\n\nArray length: {len(data)}"
            if len(data) > 0:
                text += f"\nFirst item type: {type(data[0]).__name__}"
        
        return text
    except Exception as e:
        logger.error(f"Error extracting text from JSON: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing JSON file: {str(e)}")


def extract_text_from_excel(file_content: bytes) -> str:
    """Extract text from Excel file content."""
    try:
        # Read all sheets
        excel_file = pd.ExcelFile(io.BytesIO(file_content))
        text = ""
        
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(io.BytesIO(file_content), sheet_name=sheet_name)
            text += f"\n\n=== Sheet: {sheet_name} ===\n"
            text += df.to_string(index=False)
            text += f"\nColumns: {', '.join(df.columns.tolist())}"
            text += f"\nRows: {len(df)}"
        
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from Excel: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing Excel file: {str(e)}")


def extract_text_from_text(file_content: bytes) -> str:
    """Extract text from plain text file content."""
    try:
        # Try different encodings
        encodings = ['utf-8', 'latin-1', 'cp1252']
        
        for encoding in encodings:
            try:
                text = file_content.decode(encoding)
                return text
            except UnicodeDecodeError:
                continue
        
        # If all encodings fail, use utf-8 with error handling
        return file_content.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Error extracting text from text file: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing text file: {str(e)}")


@router.post("/process")
async def process_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Process uploaded document and extract text content.
    
    Supported file types:
    - PDF (.pdf)
    - Text files (.txt, .md, .xml, .html)
    - CSV files (.csv)
    - JSON files (.json)
    - Excel files (.xlsx, .xls)
    """
    
    # Validate file size
    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413, 
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"
        )
    
    # Validate file type
    if file.content_type not in SUPPORTED_FILE_TYPES:
        supported_types = ", ".join(SUPPORTED_FILE_TYPES.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Supported types: {supported_types}"
        )
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Extract text based on file type
        file_type = SUPPORTED_FILE_TYPES[file.content_type]
        
        if file_type == 'pdf':
            extracted_text = extract_text_from_pdf(file_content)
        elif file_type == 'csv':
            extracted_text = extract_text_from_csv(file_content)
        elif file_type == 'json':
            extracted_text = extract_text_from_json(file_content)
        elif file_type in ['xlsx', 'xls']:
            extracted_text = extract_text_from_excel(file_content)
        else:  # text files
            extracted_text = extract_text_from_text(file_content)
        
        # Prepare response
        response_data = {
            "filename": file.filename,
            "file_type": file_type,
            "content_type": file.content_type,
            "file_size": len(file_content),
            "extracted_text": extracted_text,
            "text_length": len(extracted_text),
            "processing_success": True
        }
        
        logger.info(f"Successfully processed document: {file.filename} ({file_type})")
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing document {file.filename}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing document: {str(e)}"
        )


@router.post("/process-multiple")
async def process_multiple_documents(
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Process multiple uploaded documents and extract text content.
    """
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > 10:  # Limit to 10 files at once
        raise HTTPException(status_code=400, detail="Too many files. Maximum is 10 files")
    
    results = []
    
    for file in files:
        try:
            # Validate file size
            if file.size and file.size > MAX_FILE_SIZE:
                results.append({
                    "filename": file.filename,
                    "error": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB",
                    "processing_success": False
                })
                continue
            
            # Validate file type
            if file.content_type not in SUPPORTED_FILE_TYPES:
                supported_types = ", ".join(SUPPORTED_FILE_TYPES.keys())
                results.append({
                    "filename": file.filename,
                    "error": f"Unsupported file type. Supported types: {supported_types}",
                    "processing_success": False
                })
                continue
            
            # Read file content
            file_content = await file.read()
            
            # Extract text based on file type
            file_type = SUPPORTED_FILE_TYPES[file.content_type]
            
            if file_type == 'pdf':
                extracted_text = extract_text_from_pdf(file_content)
            elif file_type == 'csv':
                extracted_text = extract_text_from_csv(file_content)
            elif file_type == 'json':
                extracted_text = extract_text_from_json(file_content)
            elif file_type in ['xlsx', 'xls']:
                extracted_text = extract_text_from_excel(file_content)
            else:  # text files
                extracted_text = extract_text_from_text(file_content)
            
            results.append({
                "filename": file.filename,
                "file_type": file_type,
                "content_type": file.content_type,
                "file_size": len(file_content),
                "extracted_text": extracted_text,
                "text_length": len(extracted_text),
                "processing_success": True
            })
            
        except Exception as e:
            logger.error(f"Error processing file {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "error": str(e),
                "processing_success": False
            })
    
    return JSONResponse(content={
        "results": results,
        "total_files": len(files),
        "successful_files": len([r for r in results if r.get("processing_success", False)]),
        "failed_files": len([r for r in results if not r.get("processing_success", False)])
    })


@router.post("/process-text-context")
async def process_text_context(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Process a text/markdown file specifically for chat context seeding.
    
    Splits content into bounded segments and returns metadata for UI display.
    """
    allowed_types = {"text/plain", "text/markdown", "text/x-markdown", "application/octet-stream"}
    allowed_extensions = {"txt", "md", "markdown"}
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""

    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # Accept text-ish uploads even if content_type is missing/unknown as long as extension matches
    if file.content_type not in allowed_types and not file.content_type.startswith("text/") and ext not in allowed_extensions:
        logger.warning(f"Rejected text context upload due to content_type={file.content_type}, ext={ext}")
        raise HTTPException(
            status_code=400,
            detail="Only plain text or Markdown files are supported for context seeding."
        )

    try:
        file_content = await file.read()
        extracted_text = extract_text_from_text(file_content)

        chunk_result = _chunk_text(
            extracted_text,
            max_chars=TEXT_CONTEXT_MAX_CHARS_PER_SEGMENT,
            max_segments=TEXT_CONTEXT_MAX_SEGMENTS,
            overlap=TEXT_CONTEXT_OVERLAP
        )

        total_input_chars = len(extracted_text)
        truncated_input = total_input_chars > TEXT_CONTEXT_MAX_TOTAL_CHARS

        response_data = {
            "filename": file.filename,
            "file_type": "text",
            "content_type": file.content_type,
            "file_size": len(file_content),
            "total_input_chars": total_input_chars,
            "segments": chunk_result["segments"],
            "segment_count": len(chunk_result["segments"]),
            "truncated": chunk_result["truncated"] or truncated_input,
            "max_total_chars": TEXT_CONTEXT_MAX_TOTAL_CHARS,
            "max_segments": TEXT_CONTEXT_MAX_SEGMENTS,
            "max_chars_per_segment": TEXT_CONTEXT_MAX_CHARS_PER_SEGMENT,
            "processing_success": True
        }

        if response_data["truncated"]:
            response_data["truncation_notice"] = (
                "Document was truncated to fit context limits. "
                "Only the first segments are included."
            )

        logger.info(
            f"Processed text context file {file.filename} into {len(chunk_result['segments'])} segments "
            f"(total chars: {total_input_chars}, truncated: {response_data['truncated']})"
        )

        return JSONResponse(content=response_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing text context {file.filename}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing text context: {str(e)}"
        )


@router.get("/supported-types")
async def get_supported_file_types():
    """
    Get list of supported file types for document processing.
    """
    return {
        "supported_types": SUPPORTED_FILE_TYPES,
        "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
        "max_files_per_request": 10
    }
