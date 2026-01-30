/**
 * ChatDocumentHandler - Manages document processing and file uploads
 * Handles file validation, processing, and context integration
 */

import { DocumentService, DocumentProcessingResult } from '../services';
import { ChatMessage } from '../types';
import { generateId } from '../utils';
import { FILE_CONFIG, ERROR_MESSAGES } from '../constants';

export class ChatDocumentHandler {
  private documentService: DocumentService | null;
  private stateManager: any;
  private messageHandler: any;

  constructor(
    documentService: DocumentService | null,
    stateManager: any,
    messageHandler: any
  ) {
    this.documentService = documentService;
    this.stateManager = stateManager;
    this.messageHandler = messageHandler;
  }

  /**
   * Handle file upload and processing
   */
  async handleFileUpload(files: FileList): Promise<DocumentProcessingResult[]> {
    if (!files || files.length === 0) {
      return [];
    }

    if (!this.documentService) {
      throw new Error(ERROR_MESSAGES.DOCUMENT_SERVICE_UNAVAILABLE);
    }

    this.stateManager.updateLoadingStates({ isProcessingDocuments: true });

    try {
      const fileArray = Array.from(files);
      const results: DocumentProcessingResult[] = [];

      // Process each file
      for (const file of fileArray) {
        try {
          // Validate file
          const validation = await this.validateFile(file);
          if (!validation.valid) {
            this.messageHandler.addSystemMessage(
              `File ${file.name}: ${validation.error}`,
              'error'
            );
            continue;
          }

          // Process file
          const result = await this.documentService.processDocument(file);
          if (result.processing_success) {
            results.push(result);
          } else {
            this.messageHandler.addSystemMessage(
              `Failed to process ${file.name}: ${result.error}`,
              'error'
            );
          }
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Unknown error';
          this.messageHandler.addSystemMessage(
            `Error processing ${file.name}: ${errorMessage}`,
            'error'
          );
        }
      }

      if (results.length > 0) {
        await this.processDocumentResults(results);
      }

      return results;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      this.messageHandler.addSystemMessage(
        `Error processing documents: ${errorMessage}`,
        'error'
      );
      throw error;
    } finally {
      this.stateManager.updateLoadingStates({ isProcessingDocuments: false });
    }
  }

  /**
   * Validate file before processing
   */
  async validateFile(file: File): Promise<{ valid: boolean; error?: string }> {
    if (!this.documentService) {
      return { valid: false, error: 'Document service not available' };
    }

    try {
      return await this.documentService.validateFile(file);
    } catch (error) {
      return {
        valid: false,
        error: error instanceof Error ? error.message : 'Validation failed'
      };
    }
  }

  /**
   * Process document results and add to context
   */
  private async processDocumentResults(results: DocumentProcessingResult[]): Promise<void> {
    if (!this.documentService) return;

    // Format document context for chat
    let documentContext = '';
    if (results.length === 1) {
      documentContext = this.documentService.formatTextForChatContext(results[0]);
    } else {
      documentContext = this.documentService.formatMultipleTextsForChatContext(results);
    }

    // Add document context to state
    this.stateManager.setState({ documentContext });

    // Add a message to show the documents were processed
    const documentMessage: ChatMessage = this.messageHandler.createMessage('ai', '', {
      isDocumentContext: true,
      documentData: {
        results,
        context: documentContext
      }
    });

    this.messageHandler.addMessage(documentMessage);
  }

  /**
   * Get supported file types
   */
  async getSupportedFileTypes(): Promise<any> {
    if (!this.documentService) {
      return null;
    }

    try {
      return await this.documentService.getSupportedFileTypes();
    } catch (error) {
      console.error('Error getting supported file types:', error);
      return null;
    }
  }

  /**
   * Clear document context
   */
  clearDocumentContext(): void {
    this.stateManager.setState({ documentContext: '' });
  }

  /**
   * Get current document context
   */
  getDocumentContext(): string {
    const state = this.stateManager.getState();
    return state.documentContext || '';
  }

  /**
   * Check if documents are currently being processed
   */
  isProcessingDocuments(): boolean {
    const state = this.stateManager.getState();
    return state.isProcessingDocuments || false;
  }

  /**
   * Format file size for display
   */
  formatFileSize(bytes: number): string {
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    if (bytes === 0) return '0 Bytes';
    
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
  }

  /**
   * Get file extension from filename
   */
  getFileExtension(filename: string): string {
    return filename.slice((filename.lastIndexOf('.') - 1 >>> 0) + 2).toLowerCase();
  }

  /**
   * Check if file type is supported
   */
  isFileTypeSupported(filename: string): boolean {
    const extension = this.getFileExtension(filename);
    const supportedExtensions = FILE_CONFIG.ACCEPTED_EXTENSIONS
      .split(',')
      .map(ext => ext.trim().replace('.', ''));
    
    return supportedExtensions.includes(extension);
  }

  /**
   * Get document processing statistics
   */
  getDocumentStats(): {
    totalDocuments: number;
    successfullyProcessed: number;
    failedProcessing: number;
    totalSizeBytes: number;
  } {
    const state = this.stateManager.getState();
    const documentMessages = state.messages.filter((msg: ChatMessage) => msg.isDocumentContext);
    
    let totalDocuments = 0;
    let successfullyProcessed = 0;
    let failedProcessing = 0;
    let totalSizeBytes = 0;

    documentMessages.forEach((msg: ChatMessage) => {
      if (msg.documentData && msg.documentData.results) {
        const results = msg.documentData.results;
        totalDocuments += results.length;
        
        results.forEach((result: DocumentProcessingResult) => {
          if (result.processing_success) {
            successfullyProcessed++;
          } else {
            failedProcessing++;
          }
          
          if (result.file_size) {
            totalSizeBytes += result.file_size;
          }
        });
      }
    });

    return {
      totalDocuments,
      successfullyProcessed,
      failedProcessing,
      totalSizeBytes
    };
  }

  /**
   * Export document context for sharing
   */
  exportDocumentContext(format: 'json' | 'text' = 'text'): string {
    const context = this.getDocumentContext();
    
    if (format === 'json') {
      const state = this.stateManager.getState();
      const documentMessages = state.messages.filter((msg: ChatMessage) => msg.isDocumentContext);
      
      return JSON.stringify({
        context,
        documents: documentMessages.map((msg: ChatMessage) => msg.documentData)
      }, null, 2);
    }

    return context;
  }

  /**
   * Create file upload button trigger
   */
  triggerFileUpload(): void {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.accept = FILE_CONFIG.ACCEPTED_EXTENSIONS;
    fileInput.style.display = 'none';
    
    fileInput.onchange = async (event) => {
      const files = (event.target as HTMLInputElement).files;
      if (files) {
        await this.handleFileUpload(files);
      }
    };

    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
  }
}
