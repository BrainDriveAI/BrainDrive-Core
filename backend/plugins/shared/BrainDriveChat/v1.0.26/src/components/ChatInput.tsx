import React from 'react';
import { ModelInfo, RagCollection, LibraryProject, LibraryScope } from '../types';
import { CheckIcon, ChevronRightIcon, DatabaseIcon, LibraryIcon, PersonaIcon, PlusIcon, SearchIcon, SendIcon, StopIcon, UploadIcon } from '../icons';

interface ChatInputProps {
  inputText: string;
  isLoading: boolean;
  isLoadingHistory: boolean;
  isStreaming: boolean;
  selectedModel: ModelInfo | null;
  promptQuestion?: string;
  onInputChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyPress: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSendMessage: () => void;
  onStopGeneration: () => void;
  onFileUpload: () => void;

  onToggleWebSearch: () => void;
  useWebSearch: boolean;
  webSearchDisabled?: boolean;
  inputRef: React.RefObject<HTMLTextAreaElement>;

  // RAG (collections) props
  ragEnabled?: boolean;
  ragCollections: RagCollection[];
  ragCollectionsLoading: boolean;
  ragCollectionsError: string | null;
  selectedRagCollectionId: string | null;
  onRagSelectCollection: (collectionId: string | null) => void;
  onRagCreateCollection: () => void;
  onRagManageDocuments: (collectionId: string) => void;
  onRagRefreshCollections?: () => void;
  
  // Persona props
  personas: any[];
  selectedPersona: any;
  onPersonaChange: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  onPersonaToggle?: () => void;
  showPersonaSelection: boolean;

  // Library props
  libraryScope?: LibraryScope;
  libraryProjects?: LibraryProject[];
  onLibraryToggle?: () => void;
  onLibrarySelectProject?: (project: LibraryProject | null) => void;
}

interface ChatInputState {
  isMenuOpen: boolean;
  showPersonaSelector: boolean;
  isMultiline: boolean;
  isRagMenuOpen: boolean;
  openRagCollectionId: string | null;
  isLibraryMenuOpen: boolean;
}

class ChatInput extends React.Component<ChatInputProps, ChatInputState> {
  private menuRef = React.createRef<HTMLDivElement>();

  constructor(props: ChatInputProps) {
    super(props);
    this.state = {
      isMenuOpen: false,
      showPersonaSelector: false,
      isMultiline: false,
      isRagMenuOpen: false,
      openRagCollectionId: null,
      isLibraryMenuOpen: false,
    };
  }

  componentDidMount() {
    document.addEventListener('mousedown', this.handleClickOutside);
    
    // Initialize local persona selector state based on main component's persona state
    console.log(`🎭 ChatInput mounted - showPersonaSelection: ${this.props.showPersonaSelection}, selectedPersona: ${this.props.selectedPersona?.name || 'null'}`);
    
    // The persona selector should be disabled by default and only shown when user toggles it
    // Don't automatically enable it even if there's a selected persona

    // Initialize multiline state
    this.updateMultilineState();
  }

  componentDidUpdate(prevProps: ChatInputProps) {
    // Ensure local persona selector state stays in sync with main component
    if (prevProps.selectedPersona !== this.props.selectedPersona) {
      console.log(`🎭 ChatInput: Persona changed from ${prevProps.selectedPersona?.name || 'null'} to ${this.props.selectedPersona?.name || 'null'}`);
    }
    
    // If showPersonaSelection prop changes, sync local state
    if (prevProps.showPersonaSelection !== this.props.showPersonaSelection) {
      console.log(`🎭 ChatInput: showPersonaSelection changed from ${prevProps.showPersonaSelection} to ${this.props.showPersonaSelection}`);
      
      // If personas are globally disabled, ensure local selector is also off
      if (!this.props.showPersonaSelection && this.state.showPersonaSelector) {
        console.log(`🎭 ChatInput: Syncing local state - turning off persona selector because personas are globally disabled`);
        this.setState({ showPersonaSelector: false });
      }
    }

    // Update multiline state when text changes
    if (prevProps.inputText !== this.props.inputText) {
      this.updateMultilineState();
    }
  }

  componentWillUnmount() {
    document.removeEventListener('mousedown', this.handleClickOutside);
  }

  handleClickOutside = (event: MouseEvent) => {
    if (this.menuRef.current && !this.menuRef.current.contains(event.target as Node)) {
      this.setState({ isMenuOpen: false, isRagMenuOpen: false, openRagCollectionId: null, isLibraryMenuOpen: false });
    }
  };

  // Determine if the textarea has grown beyond one line to adjust button alignment
  updateMultilineState = () => {
    const ta = this.props.inputRef?.current;
    if (!ta) return;
    const computed = window.getComputedStyle(ta);
    const lineHeight = parseFloat(computed.lineHeight || '0') || 24;
    const isMulti = ta.scrollHeight > lineHeight * 1.6; // a bit above 1 line to avoid flicker
    if (isMulti !== this.state.isMultiline) {
      this.setState({ isMultiline: isMulti });
    }
  };

  handleInputChangeProxy = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    // Call upstream handler first
    this.props.onInputChange(e);
    // Then recompute alignment in the next frame
    requestAnimationFrame(this.updateMultilineState);
  };

  toggleMenu = () => {
    this.setState(prevState => ({
      isMenuOpen: !prevState.isMenuOpen,
      isRagMenuOpen: false,
      openRagCollectionId: null,
    }), () => {
      if (this.state.isMenuOpen && this.props.onRagRefreshCollections) {
        this.props.onRagRefreshCollections();
      }
    });
  };

  handleFileUpload = () => {
    if (this.props.onFileUpload) {
      this.props.onFileUpload();
    }
    this.setState({ isMenuOpen: false, isRagMenuOpen: false, openRagCollectionId: null, isLibraryMenuOpen: false });
  };

  handleWebSearchToggle = () => {
    if (this.props.webSearchDisabled) {
      this.setState({ isMenuOpen: false, isRagMenuOpen: false, openRagCollectionId: null, isLibraryMenuOpen: false });
      return;
    }
    if (this.props.onToggleWebSearch) {
      this.props.onToggleWebSearch();
    }
    this.setState({ isMenuOpen: false, isRagMenuOpen: false, openRagCollectionId: null, isLibraryMenuOpen: false });
  };

  handlePersonaToggle = () => {
    this.setState(prevState => {
      const newShowPersonaSelector = !prevState.showPersonaSelector;
      
      // If turning off persona selector, reset the persona
      if (!newShowPersonaSelector && this.props.onPersonaToggle) {
        this.props.onPersonaToggle();
      }
      
      console.log(`🎭 ChatInput: Persona toggle - newShowPersonaSelector: ${newShowPersonaSelector}`);
      
      return {
        showPersonaSelector: newShowPersonaSelector,
        isMenuOpen: false,
        isRagMenuOpen: false,
        openRagCollectionId: null,
      };
    });
  };

  openRagMenu = () => {
    if (this.props.ragEnabled === false) return;
    this.setState({ isRagMenuOpen: true, openRagCollectionId: null });
    if (this.props.onRagRefreshCollections) {
      this.props.onRagRefreshCollections();
    }
  };

  openRagCollectionMenu = (collectionId: string) => {
    if (this.props.ragEnabled === false) return;
    this.setState({ isRagMenuOpen: true, openRagCollectionId: collectionId });
  };

  closeAllMenus = () => {
    this.setState({ isMenuOpen: false, isRagMenuOpen: false, openRagCollectionId: null, isLibraryMenuOpen: false });
  };

  openLibraryMenu = () => {
    this.setState({ isLibraryMenuOpen: true, isRagMenuOpen: false, openRagCollectionId: null });
  };

  handleLibrarySelectAll = () => {
    if (this.props.onLibrarySelectProject) {
      this.props.onLibrarySelectProject(null); // null = "All"
    }
    if (this.props.onLibraryToggle && !this.props.libraryScope?.enabled) {
      this.props.onLibraryToggle();
    }
    this.closeAllMenus();
  };

  handleLibrarySelectProject = (project: LibraryProject) => {
    if (this.props.onLibrarySelectProject) {
      this.props.onLibrarySelectProject(project);
    }
    if (this.props.onLibraryToggle && !this.props.libraryScope?.enabled) {
      this.props.onLibraryToggle();
    }
    this.closeAllMenus();
  };

  handleLibraryDisable = () => {
    if (this.props.onLibraryToggle && this.props.libraryScope?.enabled) {
      this.props.onLibraryToggle();
    }
    this.closeAllMenus();
  };

  handleRagClearSelection = () => {
    this.props.onRagSelectCollection(null);
    this.closeAllMenus();
  };

  handleRagCreateCollection = () => {
    this.props.onRagCreateCollection();
    this.closeAllMenus();
  };

  handleRagManageDocuments = (collectionId: string) => {
    this.props.onRagManageDocuments(collectionId);
    this.closeAllMenus();
  };

  handleRagSelectCollection = (collectionId: string) => {
    this.props.onRagSelectCollection(collectionId);
    this.closeAllMenus();
  };

  render() {
    const {
      inputText,
      isLoading,
      isLoadingHistory,
      isStreaming,
      selectedModel,
      promptQuestion,
      onInputChange,
      onKeyPress,
      onSendMessage,
      onStopGeneration,
      useWebSearch,
      webSearchDisabled,
      inputRef,
      ragEnabled,
      ragCollections,
      ragCollectionsLoading,
      ragCollectionsError,
      selectedRagCollectionId,
      personas,
      selectedPersona,
      onPersonaChange,
      showPersonaSelection
    } = this.props;

    // Local dropdown state retained for future menu use; not used in current layout
    const isRagEnabled = ragEnabled !== false;

    const selectedRagCollection = selectedRagCollectionId
      ? ragCollections.find((collection) => collection.id === selectedRagCollectionId) || null
      : null;
    const ragMenuSubtext = !isRagEnabled
      ? 'RAG unavailable'
      : selectedRagCollection
        ? `Selected: ${selectedRagCollection.name}`
        : selectedRagCollectionId
          ? 'Selected collection'
          : 'Select a collection to enable RAG';
    
    return (
      <div className="chat-input-container">
        <div className="chat-input-wrapper">
          <div className="input-with-buttons">
            <div className={`chat-input-row ${this.state.isMultiline ? 'multiline' : ''}`}>
              {/* Left feature action */}
              <div className="menu-container" ref={this.menuRef}>
                <button
                  type="button"
                  className="input-button icon-button feature-button"
                  onClick={this.toggleMenu}
                  aria-label="Open feature menu"
                  aria-expanded={this.state.isMenuOpen}
                  disabled={isLoading || isLoadingHistory}
                >
                  <PlusIcon />
                </button>

                {this.state.isMenuOpen && (
                  <div className="dropdown-menu feature-menu">
                    <button
                      className="menu-item menu-item-has-submenu"
                      onClick={this.openRagMenu}
                      disabled={isLoading || isLoadingHistory || !isRagEnabled}
                    >
                      <DatabaseIcon />
                      <div className="menu-item-text">
                        <span className="menu-item-title">RAG</span>
                        <span className="menu-item-subtext">{ragMenuSubtext}</span>
                      </div>
                      <span className="menu-item-right">
                        <ChevronRightIcon />
                      </span>
                    </button>
                    <button className="menu-item" onClick={this.handleFileUpload} disabled={isLoading || isLoadingHistory}>
                      <UploadIcon />
                      <div className="menu-item-text">
                        <span className="menu-item-title">Attach file</span>
                        <span className="menu-item-subtext">Upload docs to ground responses</span>
                      </div>
                    </button>
                    <button className="menu-item" onClick={this.handleWebSearchToggle} disabled={isLoading || isLoadingHistory || !!webSearchDisabled}>
                      <SearchIcon isActive={useWebSearch} />
                      <div className="menu-item-text">
                        <span className="menu-item-title">Web search</span>
                        <span className="menu-item-subtext">
                          {webSearchDisabled ? 'Disabled for now' : `${useWebSearch ? 'Disable' : 'Enable'} live search for answers`}
                        </span>
                      </div>
                    </button>
                    {showPersonaSelection && (
                      <button className="menu-item" onClick={this.handlePersonaToggle} disabled={isLoading || isLoadingHistory}>
                        <PersonaIcon />
                        <div className="menu-item-text">
                          <span className="menu-item-title">Personas</span>
                          <span className="menu-item-subtext">
                            {this.state.showPersonaSelector ? 'Hide selector' : 'Choose a voice'}
                          </span>
                        </div>
                      </button>
                    )}
                    <button
                      className="menu-item menu-item-has-submenu"
                      onClick={this.openLibraryMenu}
                      disabled={isLoading || isLoadingHistory}
                    >
                      <LibraryIcon />
                      <div className="menu-item-text">
                        <span className="menu-item-title">Library</span>
                        <span className="menu-item-subtext">
                          {this.props.libraryScope?.enabled
                            ? `Scope: ${this.props.libraryScope.project?.name || 'All'}`
                            : 'Access your local Library'}
                        </span>
                      </div>
                      <span className="menu-item-right">
                        <ChevronRightIcon />
                      </span>
                    </button>

                    {/* Library submenu */}
                    {this.state.isLibraryMenuOpen && (
                      <div className="dropdown-menu feature-menu menu-submenu" role="menu">
                        <button className="menu-item" onClick={this.handleLibrarySelectAll} disabled={isLoading || isLoadingHistory}>
                          <span className="menu-item-title">All</span>
                          <span className="menu-item-right">
                            {this.props.libraryScope?.enabled && !this.props.libraryScope?.project && (
                              <span className="menu-item-check"><CheckIcon /></span>
                            )}
                          </span>
                        </button>
                        <div className="menu-divider" />
                        {(this.props.libraryProjects || []).map((project) => {
                          const isSelected = this.props.libraryScope?.enabled && this.props.libraryScope?.project?.slug === project.slug;
                          return (
                            <button
                              key={project.slug}
                              className="menu-item"
                              onClick={() => this.handleLibrarySelectProject(project)}
                              disabled={isLoading || isLoadingHistory}
                              title={project.name}
                            >
                              <span className="menu-item-title">{project.name}</span>
                              <span className="menu-item-right">
                                {isSelected && <span className="menu-item-check"><CheckIcon /></span>}
                              </span>
                            </button>
                          );
                        })}
                        {this.props.libraryScope?.enabled && (
                          <>
                            <div className="menu-divider" />
                            <button className="menu-item" onClick={this.handleLibraryDisable} disabled={isLoading || isLoadingHistory}>
                              <span className="menu-item-title">Disable Library</span>
                            </button>
                          </>
                        )}
                      </div>
                    )}

                    {/* RAG submenu */}
                    {isRagEnabled && this.state.isRagMenuOpen && (
                      <div className="dropdown-menu feature-menu menu-submenu" role="menu">
                        <button className="menu-item" onClick={this.handleRagClearSelection} disabled={isLoading || isLoadingHistory}>
                          <span className="menu-item-title">No collection</span>
                          <span className="menu-item-right">
                            {!selectedRagCollectionId && <span className="menu-item-check"><CheckIcon /></span>}
                          </span>
                        </button>

                        <button className="menu-item" onClick={this.handleRagCreateCollection} disabled={isLoading || isLoadingHistory}>
                          <span className="menu-item-title">Create Collection…</span>
                        </button>

                        <div className="menu-divider" />

                        {ragCollectionsLoading && (
                          <button className="menu-item" disabled>
                            <span className="menu-item-title">Loading collections…</span>
                          </button>
                        )}

                        {!ragCollectionsLoading && ragCollectionsError && (
                          <button className="menu-item" disabled title={ragCollectionsError}>
                            <div className="menu-item-text">
                              <span className="menu-item-title">Unable to load collections</span>
                              <span className="menu-item-subtext">{ragCollectionsError}</span>
                            </div>
                          </button>
                        )}

                        {!ragCollectionsLoading && !ragCollectionsError && ragCollections.length === 0 && (
                          <button className="menu-item" disabled>
                            <div className="menu-item-text">
                              <span className="menu-item-title">No collections yet</span>
                              <span className="menu-item-subtext">Create one to enable RAG</span>
                            </div>
                          </button>
                        )}

                        {!ragCollectionsLoading && !ragCollectionsError && ragCollections.map((collection) => {
                          const isSelected = selectedRagCollectionId === collection.id;
                          return (
                            <button
                              key={collection.id}
                              className="menu-item menu-item-has-submenu"
                              onClick={() => this.openRagCollectionMenu(collection.id)}
                              disabled={isLoading || isLoadingHistory}
                              title={collection.description || collection.name}
                            >
                              <span className="menu-item-title">{collection.name}</span>
                              <span className="menu-item-right">
                                {isSelected && <span className="menu-item-check"><CheckIcon /></span>}
                                <ChevronRightIcon />
                              </span>
                            </button>
                          );
                        })}

                        {/* Collection submenu */}
                        {this.state.openRagCollectionId && (
                          <div className="dropdown-menu feature-menu menu-submenu" role="menu">
                            <button
                              className="menu-item"
                              onClick={() => this.handleRagManageDocuments(this.state.openRagCollectionId!)}
                              disabled={isLoading || isLoadingHistory}
                            >
                              <span className="menu-item-title">Manage Documents…</span>
                            </button>
                            <button
                              className="menu-item"
                              onClick={() => this.handleRagSelectCollection(this.state.openRagCollectionId!)}
                              disabled={isLoading || isLoadingHistory}
                            >
                              <span className="menu-item-title">Select</span>
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Persona Selector - optional inline control */}
              {showPersonaSelection && (
                <select
                  value={selectedPersona?.id || ''}
                  onChange={onPersonaChange}
                  className="persona-selector"
                  disabled={isLoading || isLoadingHistory}
                  title="Select persona"
                >
                  <option value="">No Persona</option>
                  {personas.map((persona: any) => (
                    <option key={persona.id} value={persona.id}>
                      {persona.name}
                    </option>
                  ))}
                </select>
              )}

              <textarea
                ref={inputRef}
                value={inputText}
                onChange={this.handleInputChangeProxy}
                onKeyDown={(e) => {
                  onKeyPress(e);
                  // Also recompute alignment after key handling (e.g., Enter)
                  requestAnimationFrame(this.updateMultilineState);
                }}
                placeholder={promptQuestion || "Type your message here..."}
                className="chat-input"
                disabled={isLoading || isLoadingHistory}
                rows={1}
              />

              {/* Send/Stop Button - right aligned */}
              <button
                onClick={isStreaming ? onStopGeneration : onSendMessage}
                disabled={(!inputText.trim() && !isStreaming) || isLoadingHistory || !selectedModel}
                className={`input-button send-button ${isStreaming ? 'stop-button' : ''}`}
                title={isStreaming ? "Stop generation" : "Send message"}
                type="button"
              >
                {isStreaming ? <StopIcon /> : <SendIcon />}
              </button>
            </div>
            {this.props.libraryScope?.enabled && (
              <div className="library-scope-indicator" data-testid="library-scope-indicator">
                <LibraryIcon />
                <span>Library: {this.props.libraryScope.project?.name || 'All'}</span>
                <button
                  className="library-scope-close"
                  onClick={this.handleLibraryDisable}
                  title="Disable Library"
                  type="button"
                >
                  &times;
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }
}

export default ChatInput;
