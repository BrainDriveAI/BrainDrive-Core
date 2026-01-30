import React from 'react';
import type { RagCollection, RagCreateCollectionInput } from '../types';
import { CloseIcon } from '../icons';

interface CreateRagCollectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (payload: RagCreateCollectionInput) => Promise<RagCollection>;
}

interface CreateRagCollectionModalState {
  name: string;
  description: string;
  color: string;
  isSaving: boolean;
  error: string | null;
}

class CreateRagCollectionModal extends React.Component<CreateRagCollectionModalProps, CreateRagCollectionModalState> {
  state: CreateRagCollectionModalState = {
    name: '',
    description: '',
    color: '#3B82F6',
    isSaving: false,
    error: null,
  };

  componentDidUpdate(prevProps: CreateRagCollectionModalProps) {
    if (this.props.isOpen && !prevProps.isOpen) {
      this.setState({
        name: '',
        description: '',
        color: '#3B82F6',
        isSaving: false,
        error: null,
      });
    }
  }

  handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (this.state.isSaving) return;

    const name = this.state.name.trim();
    if (!name) {
      this.setState({ error: 'Collection name is required' });
      return;
    }

    const description = this.state.description.trim();
    if (!description) {
      this.setState({ error: 'Collection description is required' });
      return;
    }

    this.setState({ isSaving: true, error: null });
    try {
      await this.props.onCreate({
        name,
        description,
        color: this.state.color || '#3B82F6',
      });
      this.props.onClose();
    } catch (error) {
      this.setState({
        error: error instanceof Error ? error.message : 'Failed to create collection',
      });
    } finally {
      this.setState({ isSaving: false });
    }
  };

  handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      this.props.onClose();
    }
  };

  render() {
    if (!this.props.isOpen) return null;

    return (
      <div className="modal-overlay" onMouseDown={this.handleOverlayClick} role="dialog" aria-modal="true">
        <div className="modal-content">
          <div className="modal-header">
            <h3 className="modal-title">Create RAG Collection</h3>
            <button type="button" className="modal-close" onClick={this.props.onClose} aria-label="Close">
              <CloseIcon />
            </button>
          </div>

          <div className="modal-body">
            <form className="modal-form" onSubmit={this.handleSubmit}>
              <input
                className="modal-input"
                value={this.state.name}
                onChange={(e) => this.setState({ name: e.target.value })}
                placeholder="Collection name"
                autoFocus
                disabled={this.state.isSaving}
              />

              <input
                className="modal-input"
                value={this.state.description}
                onChange={(e) => this.setState({ description: e.target.value })}
                placeholder="Description (required)"
                disabled={this.state.isSaving}
              />

              <div className="rag-color-row">
                <label className="rag-color-label" htmlFor="rag-collection-color">
                  Color
                </label>
                <input
                  id="rag-collection-color"
                  type="color"
                  value={this.state.color}
                  onChange={(e) => this.setState({ color: e.target.value })}
                  disabled={this.state.isSaving}
                  className="rag-color-input"
                />
              </div>

              {this.state.error && <div className="modal-error">{this.state.error}</div>}

              <div className="modal-actions">
                <button type="button" className="modal-btn modal-btn-secondary" onClick={this.props.onClose} disabled={this.state.isSaving}>
                  Cancel
                </button>
                <button
                  type="submit"
                  className="modal-btn modal-btn-primary"
                  disabled={this.state.isSaving || !this.state.name.trim() || !this.state.description.trim()}
                >
                  {this.state.isSaving ? 'Creating…' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    );
  }
}

export default CreateRagCollectionModal;
