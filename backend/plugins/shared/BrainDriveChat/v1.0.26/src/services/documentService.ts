/**
 * Document Processing Service
 *
 * This service handles document uploads and text extraction for chat context.
 * It provides methods to process various file types and extract text content.
 */

import { ApiService } from '../types';

export interface DocumentContextSegment {
  index: number;
  text: string;
  char_count: number;
}

export interface DocumentProcessingResult {
  filename: string | null;
  file_type: string;
  content_type: string;
  file_size: number | null;
  extracted_text: string;
  text_length: number;
  processing_success: boolean;
  detected_type?: string;
  metadata?: Record<string, any>;
  warnings?: string[];
  chunks?: DocumentContextSegment[];
  chunk_metadata?: {
    truncated?: boolean;
    total_chars?: number;
  };
  error?: string;
}

export interface MultipleDocumentProcessingResult {
  results: DocumentProcessingResult[];
  total_files: number;
  successful_files: number;
  failed_files: number;
}

export interface SupportedFileTypes {
  supported_types: Record<string, string>;
  max_file_size_mb: number;
  max_files_per_request: number;
  canonical_types?: string[];
  extensions?: string[];
}

export interface DocumentContextResult {
  filename: string | null;
  file_type: string;
  content_type: string;
  file_size: number | null;
  total_input_chars: number;
  segments: DocumentContextSegment[];
  segment_count: number;
  truncated?: boolean;
  truncation_notice?: string;
  max_total_chars: number;
  max_segments: number;
  max_chars_per_segment: number;
  overlap_chars?: number;
  warnings?: string[];
  processing_success: boolean;
}

export interface DocumentProcessOptions {
  includeChunks?: boolean;
  maxChars?: number;
  preserveLayout?: boolean;
  stripBoilerplate?: boolean;
}

export class DocumentService {
  private apiService: ApiService | null;

  constructor(apiService?: ApiService) {
    this.apiService = apiService || null;
  }

  /**
   * Set API service for authenticated requests
   */
  setApiService(apiService: ApiService): void {
    this.apiService = apiService;
  }

  /**
   * Get current API service
   */
  getApiService(): ApiService | null {
    return this.apiService;
  }

  private buildProcessParams(options?: DocumentProcessOptions): Record<string, any> {
    if (!options) return {};
    const params: Record<string, any> = {
      include_chunks: options.includeChunks,
      max_chars: options.maxChars,
      preserve_layout: options.preserveLayout,
      strip_boilerplate: options.stripBoilerplate,
    };

    Object.keys(params).forEach((key) => {
      if (params[key] === undefined) {
        delete params[key];
      }
    });

    return params;
  }

  private normalizeExtensions(extensions: string[] | undefined): string[] {
    if (!extensions || extensions.length === 0) return [];
    return extensions.map((ext) => (ext.startsWith('.') ? ext : `.${ext}`));
  }

  /**
   * Get supported file types and limits
   */
  async getSupportedFileTypes(): Promise<SupportedFileTypes> {
    if (!this.apiService) {
      throw new Error('API service not available');
    }

    try {
      const response = await this.apiService.get('/api/v1/documents/supported-types');
      return response.data || response;
    } catch (error) {
      console.error('Error getting supported file types:', error);
      throw error;
    }
  }

  /**
   * Process a single document and extract text
   */
  async processDocument(file: File, options?: DocumentProcessOptions): Promise<DocumentProcessingResult> {
    if (!this.apiService) {
      throw new Error('API service not available');
    }

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await this.apiService.post('/api/v1/documents/process', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        params: this.buildProcessParams(options),
      });

      return response.data || response;
    } catch (error) {
      console.error(`❌ Error processing document ${file.name}:`, error);
      throw error;
    }
  }

  /**
   * Process a text/markdown document specifically for context seeding.
   * Limits supported types to text-based files.
   */
  async processTextContext(file: File): Promise<DocumentContextResult> {
    if (!this.apiService) {
      throw new Error('API service not available');
    }

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await this.apiService.post('/api/v1/documents/process-text-context', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      return response.data || response;
    } catch (error) {
      console.error(`Error processing text context ${file.name}:`, error);
      throw error;
    }
  }

  /**
   * Process multiple documents and extract text
   */
  async processMultipleDocuments(files: File[], options?: DocumentProcessOptions): Promise<MultipleDocumentProcessingResult> {
    if (!this.apiService) {
      throw new Error('API service not available');
    }

    if (files.length === 0) {
      throw new Error('No files provided');
    }

    if (files.length > 10) {
      throw new Error('Too many files. Maximum is 10 files');
    }

    try {
      const formData = new FormData();
      files.forEach(file => {
        formData.append('files', file);
      });

      const response = await this.apiService.post('/api/v1/documents/process-multiple', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        params: this.buildProcessParams(options),
      });

      const result = response.data || response;
      return result;
    } catch (error) {
      console.error('❌ Error processing multiple documents:', error);
      throw error;
    }
  }

  /**
   * Check if a file type is supported
   */
  async isFileTypeSupported(file: File): Promise<boolean> {
    try {
      const supportedTypes = await this.getSupportedFileTypes();
      const extension = this.getFileExtension(file.name);
      if (supportedTypes.extensions && supportedTypes.extensions.length > 0) {
        const normalized = this.normalizeExtensions(supportedTypes.extensions).map((ext) => ext.replace('.', ''));
        return normalized.includes(extension);
      }
      return file.type in supportedTypes.supported_types;
    } catch (error) {
      console.error('Error checking file type support:', error);
      return false;
    }
  }

  /**
   * Check if file size is within limits
   */
  async isFileSizeValid(file: File): Promise<boolean> {
    try {
      const supportedTypes = await this.getSupportedFileTypes();
      const maxSizeBytes = supportedTypes.max_file_size_mb * 1024 * 1024;
      return file.size <= maxSizeBytes;
    } catch (error) {
      console.error('Error checking file size:', error);
      return false;
    }
  }

  /**
   * Validate file before processing
   */
  async validateFile(file: File): Promise<{ valid: boolean; error?: string }> {
    try {
      const typeSupported = await this.isFileTypeSupported(file);
      if (!typeSupported) {
        const supportedTypes = await this.getSupportedFileTypes();
        const extensions = this.normalizeExtensions(supportedTypes.extensions || []);
        const supportedTypesList = extensions.length > 0
          ? extensions.join(', ')
          : Object.keys(supportedTypes.supported_types).join(', ');
        return {
          valid: false,
          error: `Unsupported file type. Supported types: ${supportedTypesList}`
        };
      }

      const sizeValid = await this.isFileSizeValid(file);
      if (!sizeValid) {
        const supportedTypes = await this.getSupportedFileTypes();
        return {
          valid: false,
          error: `File too large. Maximum size is ${supportedTypes.max_file_size_mb}MB`
        };
      }

      return { valid: true };
    } catch (error) {
      return {
        valid: false,
        error: `Error validating file: ${error instanceof Error ? error.message : 'Unknown error'}`
      };
    }
  }

  /**
   * Format extracted text for chat context
   */
  formatTextForChatContext(result: DocumentProcessingResult): string {
    const { filename, file_type, extracted_text, text_length } = result;

    let context = `[DOCUMENT CONTEXT - ${String(filename).toUpperCase()}]\n`;
    context += `File Type: ${file_type}\n`;
    context += `Text Length: ${text_length} characters\n`;
    context += `Content:\n\n${extracted_text}\n\n`;
    context += `[END DOCUMENT CONTEXT]`;

    return context;
  }

  /**
   * Format multiple document results for chat context
   */
  formatMultipleTextsForChatContext(results: DocumentProcessingResult[]): string {
    let context = `[MULTIPLE DOCUMENTS CONTEXT]\n`;
    context += `Total Documents: ${results.length}\n\n`;

    results.forEach((result, index) => {
      context += `--- Document ${index + 1}: ${result.filename} ---\n`;
      context += `Type: ${result.file_type}\n`;
      context += `Length: ${result.text_length} characters\n`;
      context += `Content:\n${result.extracted_text}\n\n`;
    });

    context += `[END MULTIPLE DOCUMENTS CONTEXT]`;

    return context;
  }

  /**
   * Format segmented context for system prompt injection.
   */
  formatSegmentsForChatContext(result: DocumentContextResult): string {
    const header = `[DOCUMENT CONTEXT - ${result.filename}]\nSegments: ${result.segment_count}\nTotal Input Chars: ${result.total_input_chars}\n`;
    const body = result.segments.map((seg: DocumentContextSegment) => {
      return `### Segment ${seg.index}\n${seg.text}`;
    }).join('\n\n');

    const footer = result.truncated ? `\n\n[TRUNCATED to fit limits]` : '';
    return `${header}\n${body}${footer}\n[END DOCUMENT CONTEXT]`;
  }

  /**
   * Extract file extension.
   */
  private getFileExtension(filename: string): string {
    return filename.slice((filename.lastIndexOf('.') - 1 >>> 0) + 2).toLowerCase();
  }
}
