import React from 'react';
import type { RagCollection, RagDocument } from '../types';
import type { RagService } from '../services';
import { CloseIcon, DeleteIcon, UploadIcon } from '../icons';
import { FILE_CONFIG } from '../constants';

interface ManageRagDocumentsModalProps {
  isOpen: boolean;
  onClose: () => void;
  ragService: RagService | null;
  collection: RagCollection | null;
}

interface ManageRagDocumentsModalState {
  documents: RagDocument[];
  loading: boolean;
  uploading: boolean;
  error: string | null;
  deletingIds: string[];
}

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 60;
const MAX_TRANSIENT_ERRORS = 5;

class ManageRagDocumentsModal extends React.Component<ManageRagDocumentsModalProps, ManageRagDocumentsModalState> {
  private fileInputRef = React.createRef<HTMLInputElement>();
  private pollingIntervals = new Map<string, number>();
  private pollingAttempts = new Map<string, number>();
  private pollingErrors = new Map<string, number>();

  state: ManageRagDocumentsModalState = {
    documents: [],
    loading: false,
    uploading: false,
    error: null,
    deletingIds: [],
  };

  componentDidMount() {
    if (this.props.isOpen) {
      this.loadDocuments();
    }
  }

  componentDidUpdate(prevProps: ManageRagDocumentsModalProps) {
    const opened = this.props.isOpen && !prevProps.isOpen;
    const closed = !this.props.isOpen && prevProps.isOpen;
    const collectionChanged = this.props.collection?.id && prevProps.collection?.id && this.props.collection.id !== prevProps.collection.id;

    if (opened || (this.props.isOpen && collectionChanged)) {
      this.loadDocuments();
    }

    if (closed) {
      this.stopAllPolling();
      this.setState({ documents: [], error: null, loading: false, uploading: false, deletingIds: [] });
    }
  }

  componentWillUnmount() {
    this.stopAllPolling();
  }

  handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      this.props.onClose();
    }
  };

  private ensureServiceAndCollection(): { ragService: RagService; collection: RagCollection } | null {
    if (!this.props.ragService || !this.props.collection) return null;
    return { ragService: this.props.ragService, collection: this.props.collection };
  }

  private shouldPollStatus(status: string): boolean {
    return status === 'processing' || status === 'uploaded';
  }

  private startPolling = (documentId: string) => {
    if (this.pollingIntervals.has(documentId)) return;
    const deps = this.ensureServiceAndCollection();
    if (!deps) return;

    this.pollingAttempts.set(documentId, 0);
    this.pollingErrors.set(documentId, 0);

    const intervalId = window.setInterval(async () => {
      const attempts = (this.pollingAttempts.get(documentId) || 0) + 1;
      this.pollingAttempts.set(documentId, attempts);

      try {
        const updated = await deps.ragService.getDocument(documentId);
        this.pollingErrors.set(documentId, 0);

        this.setState((prev) => ({
          documents: prev.documents.map((d) => (d.id === updated.id ? updated : d)),
        }));

        if (!this.shouldPollStatus(updated.status)) {
          this.stopPolling(documentId);
          return;
        }
      } catch (error) {
        const errCount = (this.pollingErrors.get(documentId) || 0) + 1;
        this.pollingErrors.set(documentId, errCount);

        if (errCount >= MAX_TRANSIENT_ERRORS) {
          this.stopPolling(documentId);
        }
      }

      if (attempts >= MAX_POLL_ATTEMPTS) {
        this.stopPolling(documentId);
      }
    }, POLL_INTERVAL_MS);

    this.pollingIntervals.set(documentId, intervalId);
  };

  private stopPolling = (documentId: string) => {
    const intervalId = this.pollingIntervals.get(documentId);
    if (intervalId) {
      window.clearInterval(intervalId);
      this.pollingIntervals.delete(documentId);
    }
    this.pollingAttempts.delete(documentId);
    this.pollingErrors.delete(documentId);
  };

  private stopAllPolling = () => {
    this.pollingIntervals.forEach((id) => window.clearInterval(id));
    this.pollingIntervals.clear();
    this.pollingAttempts.clear();
    this.pollingErrors.clear();
  };

  private syncPolling = (documents: RagDocument[]) => {
    const shouldPoll = new Set(documents.filter((d) => this.shouldPollStatus(d.status)).map((d) => d.id));
    const currentlyPolling = new Set(this.pollingIntervals.keys());

    // Start new pollers
    shouldPoll.forEach((id) => {
      if (!currentlyPolling.has(id)) {
        this.startPolling(id);
      }
    });

    // Stop pollers no longer needed
    currentlyPolling.forEach((id) => {
      if (!shouldPoll.has(id)) {
        this.stopPolling(id);
      }
    });
  };

  loadDocuments = async () => {
    const deps = this.ensureServiceAndCollection();
    if (!deps) return;

    this.setState({ loading: true, error: null });
    try {
      const documents = await deps.ragService.listDocuments(deps.collection.id);
      this.setState({ documents, loading: false, error: null });
      this.syncPolling(documents);
    } catch (error) {
      this.setState({
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load documents',
      });
    }
  };

  triggerFilePicker = () => {
    this.fileInputRef.current?.click();
  };

  handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const deps = this.ensureServiceAndCollection();
    if (!deps) return;

    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length === 0) return;

    this.setState({ uploading: true, error: null });

    try {
      for (const file of files) {
        const uploaded = await deps.ragService.uploadDocument(deps.collection.id, file);
        this.setState((prev) => ({
          documents: [uploaded, ...prev.documents.filter((d) => d.id !== uploaded.id)],
        }));
        this.startPolling(uploaded.id);
      }
    } catch (error) {
      this.setState({
        error: error instanceof Error ? error.message : 'Upload failed',
      });
    } finally {
      this.setState({ uploading: false });
      if (this.fileInputRef.current) {
        this.fileInputRef.current.value = '';
      }
    }
  };

  handleDelete = async (doc: RagDocument) => {
    const deps = this.ensureServiceAndCollection();
    if (!deps) return;

    const confirmed = window.confirm(`Delete "${doc.original_filename}"? This cannot be undone.`);
    if (!confirmed) return;

    this.setState((prev) => ({ deletingIds: [...prev.deletingIds, doc.id], error: null }));
    try {
      await deps.ragService.deleteDocument(doc.id);
      this.stopPolling(doc.id);
      this.setState((prev) => ({
        documents: prev.documents.filter((d) => d.id !== doc.id),
      }));
    } catch (error) {
      this.setState({
        error: error instanceof Error ? error.message : 'Delete failed',
      });
    } finally {
      this.setState((prev) => ({ deletingIds: prev.deletingIds.filter((id) => id !== doc.id) }));
    }
  };

  private renderStatusBadge(status: string) {
    const normalized = (status || '').toLowerCase();
    const className = ['rag-status-badge', `rag-status-${normalized}`].join(' ');
    return <span className={className}>{status || 'unknown'}</span>;
  }

  render() {
    if (!this.props.isOpen) return null;
    if (!this.props.collection) return null;

    const { documents, loading, uploading, error, deletingIds } = this.state;

    return (
      <div className="modal-overlay" onMouseDown={this.handleOverlayClick} role="dialog" aria-modal="true">
        <div className="modal-content modal-lg">
          <div className="modal-header">
            <h3 className="modal-title">Manage Documents — {this.props.collection.name}</h3>
            <button type="button" className="modal-close" onClick={this.props.onClose} aria-label="Close">
              <CloseIcon />
            </button>
          </div>

          <div className="modal-body">
            <div className="rag-docs-toolbar">
              <div className="rag-docs-meta">
                <div className="rag-docs-count">{documents.length} document{documents.length === 1 ? '' : 's'}</div>
                <div className="rag-docs-subtext">Uploads are processed in the background.</div>
              </div>

              <div className="rag-docs-actions">
                <input
                  ref={this.fileInputRef}
                  type="file"
                  multiple
                  accept={FILE_CONFIG.ACCEPTED_EXTENSIONS}
                  onChange={this.handleFileSelect}
                  style={{ display: 'none' }}
                  disabled={uploading}
                />
                <button type="button" className="modal-btn modal-btn-primary" onClick={this.triggerFilePicker} disabled={uploading}>
                  <span className="rag-docs-upload-btn">
                    <UploadIcon />
                    {uploading ? 'Uploading…' : 'Upload'}
                  </span>
                </button>
                <button type="button" className="modal-btn modal-btn-secondary" onClick={this.loadDocuments} disabled={loading || uploading}>
                  Refresh
                </button>
              </div>
            </div>

            {error && <div className="modal-error">{error}</div>}

            {loading ? (
              <div className="rag-docs-loading">Loading documents…</div>
            ) : (
              <div className="rag-docs-list">
                {documents.length === 0 ? (
                  <div className="rag-docs-empty">
                    <div className="rag-docs-empty-title">No documents in this collection</div>
                    <div className="rag-docs-empty-sub">Upload files to enable retrieval.</div>
                  </div>
                ) : (
                  documents.map((doc) => {
                    const isDeleting = deletingIds.includes(doc.id);
                    return (
                      <div key={doc.id} className="rag-doc-row">
                        <div className="rag-doc-meta">
                          <div className="rag-doc-filename" title={doc.original_filename}>
                            {doc.original_filename}
                          </div>
                          <div className="rag-doc-sub">
                            {this.renderStatusBadge(doc.status)}{' '}
                            {doc.chunk_count ? `• ${doc.chunk_count} chunks` : ''}
                            {doc.error_message ? `• ${doc.error_message}` : ''}
                          </div>
                        </div>

                        <div className="rag-doc-actions">
                          <button
                            type="button"
                            className="modal-btn modal-btn-danger"
                            onClick={() => this.handleDelete(doc)}
                            disabled={isDeleting}
                            title="Delete document"
                          >
                            <span className="rag-doc-delete-btn">
                              <DeleteIcon />
                              {isDeleting ? 'Deleting…' : 'Delete'}
                            </span>
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="modal-btn modal-btn-secondary" onClick={this.props.onClose}>
                Close
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }
}

export default ManageRagDocumentsModal;

