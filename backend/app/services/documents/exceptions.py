class DocumentProcessingError(Exception):
    """Base class for document processing errors."""


class UnsupportedType(DocumentProcessingError):
    """Raised when the uploaded file type cannot be processed."""


class SizeExceeded(DocumentProcessingError):
    """Raised when the uploaded file is too large."""


class ExtractionError(DocumentProcessingError):
    """Raised when extraction fails due to file corruption or parser errors."""
