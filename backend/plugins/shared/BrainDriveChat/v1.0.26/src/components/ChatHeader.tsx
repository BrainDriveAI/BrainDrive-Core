import React from 'react';
import { ModelInfo, ConversationInfo, PersonaInfo } from '../types';
import { formatRelativeTime } from '../utils';
import { ComposeIcon, SettingsIcon, EditIcon, DeleteIcon, UploadIcon } from '../icons';
import SearchableDropdown, {
  DropdownOption
} from './SearchableDropdown';

interface ChatHeaderProps {
  // Model selection props
  models: ModelInfo[];
  selectedModel: ModelInfo | null;
  isLoadingModels: boolean;
  onModelChange: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  showModelSelection: boolean;
  
  // Persona selection props
  personas: PersonaInfo[];
  selectedPersona: PersonaInfo | null;
  onPersonaChange: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  showPersonaSelection: boolean;
  
  // Conversation history props
  conversations: ConversationInfo[];
  selectedConversation: ConversationInfo | null;
  onConversationSelect: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  onNewChatClick: () => void;
  onUploadClick: () => void;
  showConversationHistory: boolean;
  // Conversation actions
  onRenameSelectedConversation?: (id: string) => void;
  onDeleteSelectedConversation?: (id: string) => void;
  
  // Loading states
  isLoading: boolean;
  isLoadingHistory: boolean;
  uploadDisabled?: boolean;
}

interface ChatHeaderState {
  isMenuOpen: boolean;
}

class ChatHeader extends React.Component<ChatHeaderProps, ChatHeaderState> {
  private menuButtonRef: HTMLButtonElement | null = null;
  private menuRef: HTMLDivElement | null = null;
  constructor(props: ChatHeaderProps) {
    super(props);
    this.state = { isMenuOpen: false };
  }

  componentDidMount(): void {
    document.addEventListener('mousedown', this.handleDocumentClick);
  }

  componentWillUnmount(): void {
    document.removeEventListener('mousedown', this.handleDocumentClick);
  }

  handleDocumentClick = (e: MouseEvent) => {
    const target = e.target as Node;
    if (
      this.state.isMenuOpen &&
      target &&
      !this.menuRef?.contains(target) &&
      !this.menuButtonRef?.contains(target as Node)
    ) {
      this.setState({ isMenuOpen: false });
    }
  };

  private emitSelectEvent(value: string) {
    return {
      target: { value } as unknown as EventTarget & HTMLSelectElement,
      currentTarget: { value } as unknown as EventTarget & HTMLSelectElement
    } as React.ChangeEvent<HTMLSelectElement>;
  }

  private handleModelSelect = (value: string) => {
    const { onModelChange } = this.props;
    if (!onModelChange) {
      return;
    }

    onModelChange(this.emitSelectEvent(value));
  };

  private handlePersonaSelect = (value: string) => {
    const { onPersonaChange } = this.props;
    if (!onPersonaChange) {
      return;
    }

    onPersonaChange(this.emitSelectEvent(value));
  };

  private handleConversationSelect = (value: string) => {
    const { onConversationSelect, onNewChatClick } = this.props;

    if (!value) {
      onNewChatClick();
      this.setState({ isMenuOpen: false });
      return;
    }

    onConversationSelect(this.emitSelectEvent(value));
    this.setState({ isMenuOpen: false });
  };

  render() {
    const {
      models,
      selectedModel,
      isLoadingModels,
      onModelChange,
      showModelSelection,
      personas,
      selectedPersona,
      onPersonaChange,
      showPersonaSelection,
      conversations,
      selectedConversation,
      onConversationSelect,
      onNewChatClick,
      showConversationHistory,
      isLoading,
      isLoadingHistory
    } = this.props;

    const modelOptions: DropdownOption[] = models.map(model => ({
      value: `${model.provider}_${model.serverId}_${model.name}`,
      label: `${model.name} (${model.serverName})`,
      keywords: [model.provider, model.serverName, model.name]
    }));

    const personaOptions: DropdownOption[] = [
      {
        value: '',
        label: 'No Persona'
      },
      ...personas.map(persona => ({
        value: persona.id,
        label: persona.name,
        keywords: [persona.description || '', persona.name]
      }))
    ];

    const conversationOptions: DropdownOption[] = [
      {
        value: '',
        label: 'Start New Chat'
      },
      ...conversations.map(conv => ({
        value: conv.id,
        label: conv.title || 'Untitled',
        description:
          conv.updated_at || conv.created_at
            ? formatRelativeTime(conv.updated_at || conv.created_at)
            : undefined,
        keywords: [conv.title || '', conv.id]
      }))
    ];

    const selectedModelValue = selectedModel
      ? `${selectedModel.provider}_${selectedModel.serverId}_${selectedModel.name}`
      : '';

    const selectedPersonaValue = selectedPersona?.id || '';

    const selectedConversationValue = selectedConversation?.id || '';

    return (
      <>
        <div className="chat-header-redesigned">
          {/* Left Section - Model Selection */}
          {showModelSelection && (
            <div className="header-model-section">
              <label className="header-label">Models</label>
              <SearchableDropdown
                id="model-selection"
                value={selectedModelValue}
                options={modelOptions}
                onSelect={this.handleModelSelect}
                placeholder={models.length === 0 ? 'No models available' : 'Select model'}
                searchPlaceholder="Search models"
                noResultsText="No models found"
                disabled={models.length === 0}
                loading={isLoadingModels}
                triggerClassName="header-select"
              />
            </div>
          )}

          {/* Middle Section - Persona Selection */}
          {showPersonaSelection && (
            <div className="header-persona-section">
              <label className="header-label">Persona</label>
              <SearchableDropdown
                id="persona-selection"
                value={selectedPersonaValue}
                options={personaOptions}
                onSelect={this.handlePersonaSelect}
                placeholder="No Persona"
                searchPlaceholder="Search personas"
                noResultsText="No personas found"
                disabled={this.props.isLoading || this.props.isLoadingHistory}
                triggerClassName="header-select"
              />
            </div>
          )}

          {/* History Section - Conversation dropdown */}
          {showConversationHistory && (
            <div className="header-history-section">
              <label className="header-label">History</label>
              <SearchableDropdown
                id="conversation-selection"
                value={selectedConversationValue}
                options={conversationOptions}
                onSelect={this.handleConversationSelect}
                placeholder="Start New Chat"
                searchPlaceholder="Search conversations"
                noResultsText="No conversations found"
                disabled={isLoading || isLoadingHistory}
                loading={isLoadingHistory}
                triggerClassName="header-select header-select-history"
                renderOption={(option, { isSelected }) => (
                  <div className="searchable-dropdown-option-content">
                    <span className="searchable-dropdown-option-label">
                      {option.label}
                      {isSelected ? ' (current)' : ''}
                    </span>
                    {option.description && (
                      <span className="searchable-dropdown-option-description">
                        {option.description}
                      </span>
                    )}
                  </div>
                )}
                renderValue={(selected) =>
                  selected
                    ? selected.description
                      ? `${selected.label} • ${selected.description}`
                      : selected.label
                    : 'Start New Chat'
                }
              />
              <div className="history-actions-wrapper" style={{ position: 'relative' }}>
                <button
                  className="header-icon-only"
                  ref={(el) => (this.menuButtonRef = el)}
                  onClick={() => this.setState({ isMenuOpen: !this.state.isMenuOpen })}
                  title="Conversation actions"
                  disabled={!selectedConversation || isLoading || isLoadingHistory}
                  aria-haspopup="menu"
                  aria-expanded={this.state.isMenuOpen}
                >
                  <SettingsIcon />
                </button>
                {this.state.isMenuOpen && selectedConversation && (
                  <div
                    className="conversation-menu"
                    ref={(el) => (this.menuRef = el)}
                    role="menu"
                    style={{ top: 'calc(100% + 6px)' }}
                  >
                    <div className="conversation-menu-item datetime">
                      {selectedConversation.updated_at
                        ? `Updated ${formatRelativeTime(selectedConversation.updated_at)}`
                        : `Created ${formatRelativeTime(selectedConversation.created_at)}`}
                    </div>
                    <button
                      className="conversation-menu-item"
                      role="menuitem"
                      onClick={() => {
                        this.setState({ isMenuOpen: false });
                        this.props.onRenameSelectedConversation?.(selectedConversation.id);
                      }}
                    >
                      <EditIcon />
                      <span>Rename</span>
                    </button>
                    <button
                      className="conversation-menu-item danger"
                      role="menuitem"
                      onClick={() => {
                        this.setState({ isMenuOpen: false });
                        this.props.onDeleteSelectedConversation?.(selectedConversation.id);
                      }}
                    >
                      <DeleteIcon />
                      <span>Delete</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Right Section - New Chat Button */}
          <div className="header-actions-section">
            <button
              className="header-new-chat-button"
              onClick={this.props.onUploadClick}
              disabled={isLoading || this.props.uploadDisabled}
              title="Upload document context"
            >
              <UploadIcon />
            </button>
            <button
              className="header-new-chat-button"
              onClick={onNewChatClick}
              disabled={isLoading}
              title="Start New Chat"
            >
              <ComposeIcon />
            </button>
          </div>
        </div>
      </>
    );
  }
}

export default ChatHeader;
