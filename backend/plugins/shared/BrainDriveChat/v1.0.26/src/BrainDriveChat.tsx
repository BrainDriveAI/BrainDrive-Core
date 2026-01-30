import React from 'react';
import './BrainDriveChat.css';
import {
  BrainDriveChatProps,
  BrainDriveChatState,
  ChatMessage,
  ModelInfo,
  PersonaInfo,
  ConversationWithPersona,
  DocumentProcessingResult,
  DocumentContextResult,
  RagCollection,
  RagCreateCollectionInput,
  LibraryProject,
  LibraryScope
} from './types';
import {
  generateId
} from './utils';

// Import constants
import {
  SETTINGS_KEYS,
  UI_CONFIG,
  API_CONFIG,
  ERROR_MESSAGES,
  SUCCESS_MESSAGES,
  PROVIDER_SETTINGS_ID_MAP,
  FILE_CONFIG
} from './constants';

// Import modular components
import {
  ChatHeader,
  ChatHistory,
  ChatInput,
  LoadingStates,
  CreateRagCollectionModal,
  ManageRagDocumentsModal
} from './components';

// Import services
import { AIService, SearchService, DocumentService, RagService, LibraryService } from './services';

// Import icons
// Icons previously used in the bottom history panel are no longer needed here

type ScrollToBottomOptions = {
  behavior?: ScrollBehavior;
  manual?: boolean;
  force?: boolean;
};

/**
 * Unified BrainDriveChat component that combines AI chat, model selection, and conversation history
 */
class BrainDriveChat extends React.Component<BrainDriveChatProps, BrainDriveChatState> {
  private chatHistoryRef = React.createRef<HTMLDivElement>();
  private inputRef = React.createRef<HTMLTextAreaElement>();
  private themeChangeListener: ((theme: string) => void) | null = null;
  private pageContextUnsubscribe: (() => void) | null = null;
  private currentPageContext: any = null;
  private readonly STREAMING_SETTING_KEY = SETTINGS_KEYS.STREAMING;
  private initialGreetingAdded = false;
  private debouncedScrollToBottom: (options?: ScrollToBottomOptions) => void;
  private aiService: AIService | null = null;
  private searchService: SearchService | null = null;
  private documentService: DocumentService | null = null;
  private ragService: RagService | null = null;
  private libraryService: LibraryService | null = null;
  private libraryProjects: LibraryProject[] = [];
  private currentStreamingAbortController: AbortController | null = null;
  private menuButtonRef: HTMLButtonElement | null = null;
  // Keep the live edge comfortably in view instead of snapping flush bottom
  private readonly SCROLL_ANCHOR_OFFSET = 420;
  private readonly MIN_VISIBLE_LAST_MESSAGE_HEIGHT = 64;
  private readonly NEAR_BOTTOM_EPSILON = 24;
  private readonly STRICT_BOTTOM_THRESHOLD = 4;
  private readonly USER_SCROLL_INTENT_GRACE_MS = 300;
  private isProgrammaticScroll = false;
  private pendingAutoScrollTimeout: ReturnType<typeof setTimeout> | null = null;
  private lastUserScrollTs = 0;
  private pendingPersonaRequestId: string | null = null;
  private readonly WHITE_LABEL_DEFAULT_GREETING =
    "Welcome to your BrainDrive!\n\nRemember you always have your {white_label_settings:OWNERS_MANUAL} and {white_label_settings:COMMUNITY} available.\n\nhow can I help you today?";
  private readonly WHITE_LABEL_FALLBACK: Record<string, { label: string; url: string }> = {
    PRIMARY: { label: 'BrainDrive', url: 'https://tinyurl.com/4dx47m7p' },
    OWNERS_MANUAL: { label: "BrainDrive Owner's Manual", url: 'https://tinyurl.com/vd99cuex' },
    COMMUNITY: { label: 'BrainDrive Community', url: 'https://tinyurl.com/yc2u5v2a' },
    SUPPORT: { label: 'BrainDrive Support', url: 'https://tinyurl.com/4h4rtx2m' },
    DOCUMENTATION: { label: 'BrainDrive Docs', url: 'https://tinyurl.com/ewajc7k3' },
  };

  constructor(props: BrainDriveChatProps) {
    super(props);
    
    this.state = {
      // Chat state
      messages: [],
      inputText: '',
      isLoading: false,
      error: '',
      currentTheme: 'light',
      selectedModel: null,
      pendingModelKey: null,
      pendingModelSnapshot: null,
      useStreaming: true, // Always use streaming
      conversation_id: null,
      isLoadingHistory: false,
      currentUserId: null,
      isInitializing: true,
      
      // History state
      conversations: [],
      selectedConversation: null,
      isUpdating: false,
      
      // Model selection state
      models: [],
      isLoadingModels: true,
      
      // UI state
      showModelSelection: true,
      showConversationHistory: true,
      
      // Persona state
      personas: props.availablePersonas || [],
      selectedPersona: null, // Default to no persona
      pendingPersonaId: null,
      isLoadingPersonas: !props.availablePersonas,
      showPersonaSelection: true, // Always show persona selection
      
      // Web search state
      useWebSearch: false,
      isSearching: false,
      
      // User control state
      isStreaming: false,
      editingMessageId: null,
      editingContent: '',
      
      // Document processing state
      documentContext: '',
      documentContextMode: null,
      documentContextInjectedConversationId: null,
      documentContextInfo: null,
      isProcessingDocuments: false,

      // RAG (collections) state
      ragEnabled: true,
      ragCollections: [],
      ragCollectionsLoading: false,
      ragCollectionsError: null,
      selectedRagCollectionId: null,
      isCreateRagCollectionModalOpen: false,
      isManageRagDocumentsModalOpen: false,
      manageRagDocumentsCollectionId: null,
      
      // Library state
      libraryScope: { enabled: false, project: null },

      // Scroll state
      isNearBottom: true,
      showScrollToBottom: false,
      isAutoScrollLocked: false,
      
      // History UI state
      showAllHistory: false,
      openConversationMenu: null,
      isHistoryExpanded: true, // History accordion state      
    };
    
    // Bind methods
    this.debouncedScrollToBottom = (options?: ScrollToBottomOptions) => {
      const requestedAt = Date.now();

      if (this.pendingAutoScrollTimeout) {
        clearTimeout(this.pendingAutoScrollTimeout);
      }

      this.pendingAutoScrollTimeout = setTimeout(() => {
        this.pendingAutoScrollTimeout = null;
        if (this.canAutoScroll(requestedAt)) {
          this.scrollToBottom(options);
        } else {
          this.updateScrollState();
        }
      }, UI_CONFIG.SCROLL_DEBOUNCE_DELAY);
    };
    
    // Initialize AI service
    this.aiService = new AIService(props.services);
    
    // Initialize Search service with authenticated API service
    this.searchService = new SearchService(props.services.api);
    
    // Initialize Document service with authenticated API service
    this.documentService = new DocumentService(props.services.api);

    // Initialize RAG service (collections + retrieval) using shared services
    this.ragService = new RagService(props.services);

    // Initialize Library service
    this.libraryService = new LibraryService(props.services.api);
  }

  /**
   * Fetch white-label settings payload (parsed) if available.
   */
  private async fetchWhiteLabelSettings(): Promise<Record<string, { label?: string; url?: string }>> {
    const settingsSvc = this.props.services?.settings;
    if (!settingsSvc) return {};

    try {
      const getter: any = (settingsSvc as any).getSetting || (settingsSvc as any).get;
      if (!getter) return {};

      const raw = await getter.call(settingsSvc, 'white_label_settings', { userId: 'current' });
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
      console.warn('Unable to load white-label settings', error);
      return {};
    }
  }

  /**
   * Resolve {white_label_settings:KEY} tokens to links or labels.
   */
  private resolveWhiteLabelTokens(template: string, payload: Record<string, { label?: string; url?: string }>): string {
    if (!template) return template;
    const map: Record<string, { label?: string; url?: string }> = {
      ...this.WHITE_LABEL_FALLBACK,
      ...(payload || {}),
    };

    return template.replace(/\{white_label_settings:([A-Z0-9_]+)\}/gi, (_match, rawKey) => {
      const key = (rawKey || '').toUpperCase();
      const entry = map[key];
      if (entry && typeof entry === 'object') {
        const label = entry.label || key;
        if (entry.url) {
          return `[${label}](${entry.url})`;
        }
        return label;
      }
      return key;
    });
  }

  /**
   * Resolve greeting template with white-label tokens substituted.
   */
  private async resolveGreetingTemplate(template: string): Promise<string> {
    const whiteLabel = await this.fetchWhiteLabelSettings();
    return this.resolveWhiteLabelTokens(template, whiteLabel);
  }

  /**
   * Build the greeting content with fallback and token resolution.
   * When no persona greeting is provided, always use the white-label default template.
   */
  private async buildInitialGreeting(personaGreeting?: string | null): Promise<string | null> {
    const baseGreeting = personaGreeting || this.WHITE_LABEL_DEFAULT_GREETING;

    if (!baseGreeting) return null;

    try {
      return await this.resolveGreetingTemplate(baseGreeting);
    } catch (error) {
      console.warn('Failed to resolve greeting template, falling back to base greeting', error);
      return baseGreeting;
    }
  }

  componentDidMount() {
    this.initializeThemeService();
    this.initializePageContextService();
    this.loadInitialData();
    this.loadSavedStreamingMode();
    this.loadPersonas();
    this.loadLibraryProjects();

    // Add global key event listener for ESC key
    document.addEventListener('keydown', this.handleGlobalKeyPress);
    
    // Add click outside listener to close conversation menu
    document.addEventListener('mousedown', this.handleClickOutside);
    
    // Initialize scroll state
    this.updateScrollState();

    // Set initialization timeout
    setTimeout(() => {
      (async () => {
        if (!this.state.conversation_id) {
          // Only use persona greeting if persona selection is enabled and a persona is selected
          // Ensure persona is null when personas are disabled
          const effectivePersona = this.state.showPersonaSelection ? this.state.selectedPersona : null;
          const personaGreeting = this.state.showPersonaSelection && effectivePersona?.sample_greeting;
          const greetingContent = await this.buildInitialGreeting(personaGreeting || null);
          
          if (greetingContent && !this.initialGreetingAdded) {
            this.initialGreetingAdded = true;
            const greetingMessage: ChatMessage = {
              id: generateId('greeting'),
              sender: 'ai',
              content: greetingContent,
              timestamp: new Date().toISOString()
            };

            this.setState(prevState => ({
              messages: [...prevState.messages, greetingMessage],
              isInitializing: false
            }));
          } else {
            this.setState({ isInitializing: false });
          }
        }
      })();
    }, UI_CONFIG.INITIAL_GREETING_DELAY);
  }

  componentDidUpdate(prevProps: BrainDriveChatProps, prevState: BrainDriveChatState) {
    if (
      prevState.models !== this.state.models ||
      prevState.pendingModelKey !== this.state.pendingModelKey ||
      prevState.pendingModelSnapshot !== this.state.pendingModelSnapshot ||
      prevState.selectedModel !== this.state.selectedModel
    ) {
      this.resolvePendingModelSelection();
    }

    if (
      prevState.personas !== this.state.personas ||
      prevState.pendingPersonaId !== this.state.pendingPersonaId ||
      prevState.selectedPersona !== this.state.selectedPersona ||
      prevState.showPersonaSelection !== this.state.showPersonaSelection
    ) {
      this.resolvePendingPersonaSelection();
    }

    const messagesChanged = prevState.messages !== this.state.messages;
    if (!messagesChanged) {
      return;
    }

    const messageCountIncreased = this.state.messages.length > prevState.messages.length;

    if (!this.state.isAutoScrollLocked && messageCountIncreased) {
      this.debouncedScrollToBottom();
    } else {
      this.updateScrollState();
    }
  }

  componentWillUnmount() {
    // Clean up theme listener
    if (this.themeChangeListener && this.props.services?.theme) {
      this.props.services.theme.removeThemeChangeListener(this.themeChangeListener);
    }
    
    // Clean up page context subscription
    if (this.pageContextUnsubscribe) {
      this.pageContextUnsubscribe();
    }
    
    // Clean up global key event listener
    document.removeEventListener('keydown', this.handleGlobalKeyPress);
    
    // Clean up click outside listener
    document.removeEventListener('mousedown', this.handleClickOutside);
    
    // Clean up any ongoing streaming
    if (this.currentStreamingAbortController) {
      this.currentStreamingAbortController.abort();
    }

    this.cancelPendingAutoScroll();
  }

  /**
   * Load initial data (models and conversations)
   */
  loadInitialData = async () => {
    await Promise.all([
      this.loadProviderSettings(),
      this.fetchConversations(),
      this.loadRagCollections({ silent: true })
    ]);
  }

  private isAbortError = (error: unknown): boolean => {
    if (!error) return false;
    if (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') {
      return true;
    }
    if (error instanceof Error && error.name === 'AbortError') return true;
    const message = error instanceof Error ? error.message : String(error);
    return /abort/i.test(message);
  };

  loadRagCollections = async (options: { silent?: boolean } = {}): Promise<void> => {
    if (!this.ragService) return;

    if (!options.silent) {
      this.setState({ ragCollectionsLoading: true, ragCollectionsError: null });
    }

    try {
      const collections = await this.ragService.listCollections();
      this.setState((prev) => {
        const selectedStillExists = prev.selectedRagCollectionId
          ? collections.some((c) => c.id === prev.selectedRagCollectionId)
          : true;

        return {
          ragEnabled: true,
          ragCollections: collections,
          ragCollectionsLoading: false,
          ragCollectionsError: null,
          selectedRagCollectionId: selectedStillExists ? prev.selectedRagCollectionId : null,
        };
      });
    } catch (error) {
      if (this.isAbortError(error)) {
        console.warn('RAG collections request aborted; disabling RAG', error);
        this.setState({
          ragEnabled: false,
          ragCollections: [],
          ragCollectionsLoading: false,
          ragCollectionsError: 'RAG service unavailable',
          selectedRagCollectionId: null,
          isCreateRagCollectionModalOpen: false,
          isManageRagDocumentsModalOpen: false,
          manageRagDocumentsCollectionId: null,
        });
        return;
      }

      console.error('Error loading RAG collections:', error);
      this.setState({
        ragCollectionsLoading: false,
        ragCollectionsError: error instanceof Error ? error.message : 'Unable to load collections',
      });
    }
  };

  private getSelectedRagCollection = (): RagCollection | null => {
    const id = this.state.selectedRagCollectionId;
    if (!id) return null;
    return this.state.ragCollections.find((c) => c.id === id) || null;
  };

  openCreateRagCollectionModal = (): void => {
    this.setState({ isCreateRagCollectionModalOpen: true });
  };

  closeCreateRagCollectionModal = (): void => {
    this.setState({ isCreateRagCollectionModalOpen: false });
  };

  openManageRagDocumentsModal = (collectionId: string): void => {
    this.setState({
      isManageRagDocumentsModalOpen: true,
      manageRagDocumentsCollectionId: collectionId,
    });
  };

  closeManageRagDocumentsModal = (): void => {
    this.setState({
      isManageRagDocumentsModalOpen: false,
      manageRagDocumentsCollectionId: null,
    });
  };

  selectRagCollection = (collectionId: string | null): void => {
    this.setState({ selectedRagCollectionId: collectionId });
  };

  handleCreateRagCollection = async (payload: RagCreateCollectionInput): Promise<RagCollection> => {
    if (!this.ragService) {
      throw new Error('RAG service not available');
    }

    const normalized: RagCreateCollectionInput = {
      name: (payload.name || '').trim(),
      description: payload.description?.trim() || '',
      color: payload.color || '#3B82F6',
    };

    if (!normalized.name) {
      throw new Error('Collection name is required');
    }

    if (!normalized.description) {
      throw new Error('Collection description is required');
    }

    const created = await this.ragService.createCollection(normalized);

    // Optimistic update + auto-select (product decision)
    this.setState((prev) => ({
      ragCollections: [created, ...prev.ragCollections.filter((c) => c.id !== created.id)],
      selectedRagCollectionId: created.id,
    }));

    // Refresh from source of truth (counts may change)
    this.loadRagCollections({ silent: true });

    return created;
  };

  /**
   * Get page-specific setting key with fallback to global
   */
  private getSettingKey(baseSetting: string): string {
    const pageContext = this.getCurrentPageContext();
    if (pageContext?.pageId) {
      return `page_${pageContext.pageId}_${baseSetting}`;
    }
    return baseSetting; // Fallback to global
  }

  /**
   * Get saved streaming mode from settings (page-specific with global fallback)
   */
  getSavedStreamingMode = async (): Promise<boolean | null> => {
    // Streaming is always on; skip settings lookups to avoid missing-definition errors
    return null;
  }

  /**
   * Load saved streaming mode from settings
   */
  loadSavedStreamingMode = async (): Promise<void> => {
    // Force streaming on by default and avoid settings service
    this.setState({ useStreaming: true });
  }

  /**
   * Save streaming mode to settings (page-specific)
   */
  saveStreamingMode = async (enabled: boolean): Promise<void> => {
    try {
      if (this.props.services?.settings?.setSetting) {
        // Save to page-specific setting key
        const pageSpecificKey = this.getSettingKey(this.STREAMING_SETTING_KEY);
        await this.props.services.settings.setSetting(pageSpecificKey, enabled);
      }
    } catch (error) {
      // Error saving streaming mode
    }
  }



  /**
   * Toggle web search mode and test connection
   */
  toggleWebSearchMode = async () => {
    const newWebSearchMode = !this.state.useWebSearch;
    this.setState({ useWebSearch: newWebSearchMode });
    
    // Test connection when enabling web search
    if (newWebSearchMode && this.searchService) {
      try {
        const healthCheck = await this.searchService.checkHealth();
        if (!healthCheck.accessible) {
          this.addMessageToChat({
            id: generateId('search-warning'),
            sender: 'ai',
            content: `⚠️ Web search enabled but the search service is not accessible. ${healthCheck.error || 'Please ensure SearXNG is running and the backend is connected.'}`,
            timestamp: new Date().toISOString()
          });
        } else {
          this.addMessageToChat({
            id: generateId('search-enabled'),
            sender: 'ai',
            content: '🔍 Web search enabled - I can now search the web to help answer your questions',
            timestamp: new Date().toISOString()
          });
        }
      } catch (error) {
        this.addMessageToChat({
          id: generateId('search-error'),
          sender: 'ai',
          content: '❌ Web search enabled but there was an error connecting to the search service',
          timestamp: new Date().toISOString()
        });
      }
    } else {
      this.addMessageToChat({
        id: generateId('search-disabled'),
        sender: 'ai',
        content: `🔍 Web search ${newWebSearchMode ? 'enabled' : 'disabled'}`,
        timestamp: new Date().toISOString()
      });
    }
  };

  /**
   * Initialize the theme service to listen for theme changes
   */
  initializeThemeService = () => {
    if (this.props.services?.theme) {
      try {
        // Get the current theme
        const currentTheme = this.props.services.theme.getCurrentTheme();
        this.setState({ currentTheme });
        
        // Set up theme change listener
        this.themeChangeListener = (newTheme: string) => {
          this.setState({ currentTheme: newTheme });
        };
        
        // Add the listener to the theme service
        this.props.services.theme.addThemeChangeListener(this.themeChangeListener);
      } catch (error) {
        // Error initializing theme service
      }
    }
  }

  /**
   * Initialize the page context service to listen for page changes
   */
  initializePageContextService = () => {
    if (this.props.services?.pageContext) {
      try {
        // Get initial page context
        this.currentPageContext = this.props.services.pageContext.getCurrentPageContext();
        
        // Subscribe to page context changes
        this.pageContextUnsubscribe = this.props.services.pageContext.onPageContextChange(
          (context) => {
            this.currentPageContext = context;
            // Reload conversations when page changes to show page-specific conversations
            this.fetchConversations();
          }
        );
      } catch (error) {
        // Error initializing page context service
        console.warn('Failed to initialize page context service:', error);
      }
    }
  }

  /**
   * Helper method to get current page context
   */
  private getCurrentPageContext() {
    if (this.props.services?.pageContext) {
      return this.props.services.pageContext.getCurrentPageContext();
    }
    return this.currentPageContext;
  }

  /**
   * Load personas from API or use provided personas
   */
  loadPersonas = async () => {
          if (this.props.availablePersonas) {
        // Use provided personas
        this.resolvePendingPersonaSelection();
        return;
      }

    this.setState({ isLoadingPersonas: true });
    
    try {
      if (this.props.services?.api) {
        const response = await this.props.services.api.get('/api/v1/personas');
        const personas = response.personas || [];
        this.setState({
          personas: personas,
          isLoadingPersonas: false
        }, () => {
          this.resolvePendingPersonaSelection();
        });
      } else {
        this.setState({ isLoadingPersonas: false }, () => {
          this.resolvePendingPersonaSelection();
        });
      }
    } catch (error) {
      console.error('Error loading personas:', error);
      this.setState({
        personas: [],
        isLoadingPersonas: false
      }, () => {
        this.resolvePendingPersonaSelection();
      });
    }
  };

  /**
   * Load provider settings and models
   */
  loadProviderSettings = async () => {
    this.setState({ isLoadingModels: true, error: '' });

    if (!this.props.services?.api) {
      this.setState({
        isLoadingModels: false,
        error: 'API service not available'
      });
      return;
    }

    try {
      const resp = await this.props.services.api.get('/api/v1/ai/providers/all-models');
      const raw = (resp && (resp as any).models)
        || (resp && (resp as any).data && (resp as any).data.models)
        || (Array.isArray(resp) ? resp : []);

      const allModels: ModelInfo[] = Array.isArray(raw)
        ? raw.map((m: any) => {
            const provider = m.provider || 'ollama';
            const providerId = PROVIDER_SETTINGS_ID_MAP[provider] || provider;
            const serverId = m.server_id || m.serverId || 'unknown';
            const serverName = m.server_name || m.serverName || 'Unknown Server';
            const name = m.name || m.id || '';
            return {
              name,
              provider,
              providerId,
              serverName,
              serverId,
            } as ModelInfo;
          })
        : [];

      const models = this.filterChatCapableModels(allModels);

      if (models.length > 0) {
        const previousSelectedKey = this.getModelKeyFromInfo(this.state.selectedModel);
        const shouldBroadcastDefault = !this.state.pendingModelKey && !this.state.selectedModel;

        this.setState(prevState => {
          const pendingModelNameFromKey = prevState.pendingModelKey?.includes(':::')
            ? prevState.pendingModelKey.split(':::').slice(1).join(':::')
            : prevState.pendingModelKey;
          const pendingModelName = prevState.pendingModelSnapshot?.name || pendingModelNameFromKey || '';
          const pendingIsEmbedding = Boolean(prevState.pendingModelKey && this.isEmbeddingModelName(pendingModelName));
          const hasPendingModel = Boolean(prevState.pendingModelKey && !pendingIsEmbedding);

          const nextSelectedModel = (() => {
            const candidate = prevState.selectedModel;
            if (hasPendingModel) {
              return candidate;
            }
            if (!candidate) {
              return models[0] || null;
            }
            if (this.isEmbeddingModelName(candidate.name)) {
              return models[0] || null;
            }

            const candidateKey = this.getModelKeyFromInfo(candidate);
            const candidateInList = models.some(model => this.getModelKeyFromInfo(model) === candidateKey);
            if (!candidateInList && !candidate.isTemporary) {
              return models[0] || null;
            }
            return candidate;
          })();

          if (hasPendingModel) {
            return {
              models,
              isLoadingModels: false,
              selectedModel: nextSelectedModel,
              pendingModelKey: prevState.pendingModelKey,
              pendingModelSnapshot: prevState.pendingModelSnapshot,
            };
          }

          return {
            models,
            isLoadingModels: false,
            selectedModel: nextSelectedModel,
            pendingModelKey: null,
            pendingModelSnapshot: null,
          };
        }, () => {
          if (this.state.pendingModelKey) {
            this.resolvePendingModelSelection();
          } else {
            const currentSelected = this.state.selectedModel;
            const currentSelectedKey = this.getModelKeyFromInfo(currentSelected);
            const selectionChanged = Boolean(
              currentSelectedKey && currentSelectedKey !== previousSelectedKey
            );
            if (currentSelected && !currentSelected.isTemporary && (shouldBroadcastDefault || selectionChanged)) {
              this.broadcastModelSelection(currentSelected);
            }
          }
        });

        return;
      }

      // Fallback: Try Ollama-only via settings + /api/v1/ollama/models
      try {
        const settingsResp = await this.props.services.api.get('/api/v1/settings/instances', {
          params: {
            definition_id: 'ollama_servers_settings',
            scope: 'user',
            user_id: 'current',
          },
        });

        let settingsData: any = null;
        if (Array.isArray(settingsResp) && settingsResp.length > 0) settingsData = settingsResp[0];
        else if (settingsResp && typeof settingsResp === 'object') {
          const obj = settingsResp as any;
          if (obj.data) settingsData = Array.isArray(obj.data) ? obj.data[0] : obj.data;
          else settingsData = settingsResp;
        }

        const fallbackModels: ModelInfo[] = [];
        if (settingsData && settingsData.value) {
          const parsedValue = typeof settingsData.value === 'string'
            ? JSON.parse(settingsData.value)
            : settingsData.value;
          const servers = Array.isArray(parsedValue?.servers) ? parsedValue.servers : [];
          for (const server of servers) {
            try {
              const params: Record<string, string> = {
                server_url: encodeURIComponent(server.serverAddress),
                settings_id: 'ollama_servers_settings',
                server_id: server.id,
              };
              if (server.apiKey) params.api_key = server.apiKey;
              const modelResponse = await this.props.services.api.get('/api/v1/ollama/models', { params });
              const serverModels = Array.isArray(modelResponse) ? modelResponse : [];
              for (const m of serverModels) {
                fallbackModels.push({
                  name: m.name,
                  provider: 'ollama',
                  providerId: 'ollama_servers_settings',
                  serverName: server.serverName,
                  serverId: server.id,
                });
              }
            } catch (innerErr) {
              console.error('Fallback: error loading Ollama models for server', server?.serverName, innerErr);
            }
          }
        }

        const filteredFallbackModels = this.filterChatCapableModels(fallbackModels);

        if (filteredFallbackModels.length > 0) {
          const previousSelectedKey = this.getModelKeyFromInfo(this.state.selectedModel);
          const shouldBroadcastDefault = !this.state.pendingModelKey && !this.state.selectedModel;

          this.setState(prevState => {
            const pendingModelNameFromKey = prevState.pendingModelKey?.includes(':::')
              ? prevState.pendingModelKey.split(':::').slice(1).join(':::')
              : prevState.pendingModelKey;
            const pendingModelName = prevState.pendingModelSnapshot?.name || pendingModelNameFromKey || '';
            const pendingIsEmbedding = Boolean(prevState.pendingModelKey && this.isEmbeddingModelName(pendingModelName));
            const hasPendingModel = Boolean(prevState.pendingModelKey && !pendingIsEmbedding);

            const nextSelectedModel = (() => {
              const candidate = prevState.selectedModel;
              if (hasPendingModel) {
                return candidate;
              }
              if (!candidate) {
                return filteredFallbackModels[0] || null;
              }
              if (this.isEmbeddingModelName(candidate.name)) {
                return filteredFallbackModels[0] || null;
              }

              const candidateKey = this.getModelKeyFromInfo(candidate);
              const candidateInList = filteredFallbackModels.some(
                model => this.getModelKeyFromInfo(model) === candidateKey
              );
              if (!candidateInList && !candidate.isTemporary) {
                return filteredFallbackModels[0] || null;
              }
              return candidate;
            })();

            if (hasPendingModel) {
              return {
                models: filteredFallbackModels,
                isLoadingModels: false,
                selectedModel: nextSelectedModel,
                pendingModelKey: prevState.pendingModelKey,
                pendingModelSnapshot: prevState.pendingModelSnapshot,
              };
            }

            return {
              models: filteredFallbackModels,
              isLoadingModels: false,
              selectedModel: nextSelectedModel,
              pendingModelKey: null,
              pendingModelSnapshot: null,
            };
          }, () => {
            if (this.state.pendingModelKey) {
              this.resolvePendingModelSelection();
            } else {
              const currentSelected = this.state.selectedModel;
              const currentSelectedKey = this.getModelKeyFromInfo(currentSelected);
              const selectionChanged = Boolean(
                currentSelectedKey && currentSelectedKey !== previousSelectedKey
              );
              if (currentSelected && !currentSelected.isTemporary && (shouldBroadcastDefault || selectionChanged)) {
                this.broadcastModelSelection(currentSelected);
              }
            }
          });

          return;
        }

        this.setState({
          models: filteredFallbackModels,
          isLoadingModels: false,
        }, () => {
          if (this.state.pendingModelKey) {
            this.resolvePendingModelSelection();
          }
        });
        return;
      } catch (fallbackErr) {
        console.error('Fallback: error loading Ollama settings/models:', fallbackErr);
        this.setState({ models: [], selectedModel: null, isLoadingModels: false });
      }
    } catch (error: any) {
      console.error('Error loading models from all providers:', error);
      this.setState({
        models: [],
        selectedModel: null,
        isLoadingModels: false,
        error: `Error loading models: ${error.message || 'Unknown error'}`,
      });
    }
  };

  /**
   * Refresh conversations list without interfering with current conversation
   */
  refreshConversationsList = async () => {
    if (!this.props.services?.api) {
      return;
    }
    
    try {
      // First, get the current user's information to get their ID
      const userResponse = await this.props.services.api.get('/api/v1/auth/me');
      
      // Extract the user ID from the response
      let userId = userResponse.id;
      
      if (!userId) {
        return;
      }
      
      // Get current page context for page-specific conversations
      const pageContext = this.getCurrentPageContext();
      const params: any = {
        skip: 0,
        limit: 50,
        conversation_type: this.props.conversationType || "chat"
      };
      
      // Add page_id if available for page-specific conversations
      if (pageContext?.pageId) {
        params.page_id = pageContext.pageId;
      }
      
      const response = await this.props.services.api.get(
        `/api/v1/users/${userId}/conversations`,
        { params }
      );
      
      let conversations = [];
      
      if (Array.isArray(response)) {
        conversations = response;
      } else if (response && response.data && Array.isArray(response.data)) {
        conversations = response.data;
      } else if (response) {
        try {
          if (typeof response === 'object') {
            if (response.id && response.user_id) {
              conversations = [response];
            }
          }
        } catch (parseError) {
          // Error parsing response
        }
      }
      
      if (conversations.length === 0) {
        this.setState({
          conversations: [],
          isLoadingHistory: false
        });
        return;
      }
      
      // Validate conversation objects
      const validConversations = conversations.filter((conv: any) => {
        return conv && typeof conv === 'object' && conv.id && conv.user_id;
      });
      
      const sortedConversations = this.sortConversationsByRecency(validConversations);
      
      // Update conversations list and select current conversation if it exists
      const currentConversation = this.state.conversation_id 
        ? sortedConversations.find(conv => conv.id === this.state.conversation_id)
        : null;
      
      this.setState({
        conversations: sortedConversations,
        selectedConversation: currentConversation || this.state.selectedConversation
      });
      
    } catch (error: any) {
      console.error('Error refreshing conversations list:', error);
    }
  };

  /**
   * Fetch conversations from the API
   */
  fetchConversations = async () => {
    if (!this.props.services?.api) {
      this.setState({
        isLoadingHistory: false,
        error: 'API service not available'
      });
      return;
    }
    
    try {
      this.setState({ isLoadingHistory: true, error: '' });
      
      // First, get the current user's information to get their ID
      const userResponse = await this.props.services.api.get('/api/v1/auth/me');
      
      // Extract the user ID from the response
      let userId = userResponse.id;
      
      if (!userId) {
        throw new Error('Could not get current user ID');
      }
      
      // Get current page context for page-specific conversations
      const pageContext = this.getCurrentPageContext();
      const params: any = {
        skip: 0,
        limit: 50, // Fetch up to 50 conversations
        conversation_type: this.props.conversationType || "chat" // Filter by conversation type
      };
      
      // Add page_id if available for page-specific conversations
      if (pageContext?.pageId) {
        params.page_id = pageContext.pageId;
      }
      
      // Use the user ID as is - backend now handles IDs with or without dashes
      const response = await this.props.services.api.get(
        `/api/v1/users/${userId}/conversations`,
        { params }
      );
      
      let conversations = [];
      
      if (Array.isArray(response)) {
        conversations = response;
      } else if (response && response.data && Array.isArray(response.data)) {
        conversations = response.data;
      } else if (response) {
        // Try to extract conversations from the response in a different way
        try {
          if (typeof response === 'object') {
            // Check if the response itself might be the conversations array
            if (response.id && response.user_id) {
              conversations = [response];
            }
          }
        } catch (parseError) {
          // Error parsing response
        }
      }
      
      if (conversations.length === 0) {
        // No conversations yet, but this is not an error
        this.setState({
          conversations: [],
          isLoadingHistory: false
        });
        
        return;
      }
      
      // Validate conversation objects
      const validConversations = conversations.filter((conv: any) => {
        return conv && typeof conv === 'object' && conv.id && conv.user_id;
      });
      
      const sortedConversations = this.sortConversationsByRecency(validConversations);

      // Auto-select the most recent conversation if available
      const mostRecentConversation = sortedConversations.length > 0 ? sortedConversations[0] : null;
      
      this.setState({
        conversations: sortedConversations,
        selectedConversation: mostRecentConversation,
        isLoadingHistory: false
      }, () => {
        // Only auto-load the most recent conversation if we don't have an active conversation
        // This prevents interference with ongoing message exchanges
        if (mostRecentConversation && !this.state.conversation_id) {
          this.loadConversationWithPersona(mostRecentConversation.id);
        }
      });
    } catch (error: any) {
      // Check if it's a 403 Forbidden error
      if (error.status === 403 || (error.response && error.response.status === 403)) {
        // Show empty state for better user experience
        this.setState({
          isLoadingHistory: false,
          conversations: [],
          error: '' // Don't show an error message to the user
        });
      } else if (error.status === 404 || (error.response && error.response.status === 404)) {
        // Handle 404 errors (no conversations found)
        this.setState({
          isLoadingHistory: false,
          conversations: [],
          error: '' // Don't show an error message to the user
        });
      } else {
        // Handle other errors
        this.setState({
          isLoadingHistory: false,
          error: `Error loading conversations: ${error.message || 'Unknown error'}`
        });
      }
    }
  }

  /**
   * Handle model selection change
   */
  handleModelChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const modelId = event.target.value;
    const selectedModel = this.state.models.find(model => 
      `${model.provider}_${model.serverId}_${model.name}` === modelId
    );
    
    if (selectedModel) {
      this.setState({
        selectedModel,
        pendingModelKey: null,
        pendingModelSnapshot: null
      }, () => {
        this.broadcastModelSelection(selectedModel);
      });
    }
  };

  /**
   * Broadcast model selection event
   */
  broadcastModelSelection = (model: ModelInfo) => {
    if (!this.props.services?.event) {
      return;
    }

    // Create model selection message
    const modelInfo = {
      type: 'model.selection',
      content: {
        model: {
          name: model.name,
          provider: model.provider,
          providerId: model.providerId,
          serverName: model.serverName,
          serverId: model.serverId
        },
        timestamp: new Date().toISOString()
      }
    };
    
    // Send to event system
    this.props.services.event.sendMessage('ai-prompt-chat', modelInfo.content);
  };

  private getModelKey(modelName?: string | null, serverName?: string | null) {
    const safeModel = (modelName || '').trim();
    const safeServer = (serverName || '').trim();
    return `${safeServer}:::${safeModel}`;
  }

  private isEmbeddingModelName(modelName?: string | null) {
    const normalized = (modelName || '').trim().toLowerCase();
    if (!normalized) {
      return false;
    }

    const embeddingMarkers = [
      'text-embedding',
      'embedding',
      'embed',
      'nomic-embed',
      'bge-',
      'e5-',
      'gte-',
      'instructor',
      'sentence-transformers',
      'rerank',
      'reranker',
      'cross-encoder',
      'colbert'
    ];

    return embeddingMarkers.some(marker => normalized.includes(marker));
  }

  private filterChatCapableModels(models: ModelInfo[]) {
    return models.filter(model => !this.isEmbeddingModelName(model.name));
  }

  private getModelKeyFromInfo(model: ModelInfo | null) {
    if (!model) {
      return '';
    }
    return this.getModelKey(model.name, model.serverName);
  }

  private resolvePendingModelSelection = () => {
    const { pendingModelKey, models, selectedModel, pendingModelSnapshot } = this.state;

    if (!pendingModelKey) {
      if (pendingModelSnapshot) {
        this.setState({ pendingModelSnapshot: null });
      }
      return;
    }

    const pendingModelNameFromKey = pendingModelKey.includes(':::')
      ? pendingModelKey.split(':::').slice(1).join(':::')
      : pendingModelKey;
    const pendingModelName = pendingModelSnapshot?.name || pendingModelNameFromKey;

    if (this.isEmbeddingModelName(pendingModelName)) {
      this.setState(prevState => {
        const nextState: Partial<BrainDriveChatState> = {
          pendingModelKey: null,
          pendingModelSnapshot: null
        };

        if (prevState.selectedModel && this.isEmbeddingModelName(prevState.selectedModel.name)) {
          nextState.selectedModel = prevState.models[0] || null;
        }

        return nextState as Pick<BrainDriveChatState, keyof BrainDriveChatState>;
      }, () => {
        if (this.state.selectedModel) {
          this.broadcastModelSelection(this.state.selectedModel);
        }
      });
      return;
    }

    const matchingModel = models.find(model => this.getModelKeyFromInfo(model) === pendingModelKey);

    if (matchingModel) {
      const selectedKey = this.getModelKeyFromInfo(selectedModel);
      const isSameKey = selectedKey === pendingModelKey;
      const selectedIsTemporary = Boolean(selectedModel?.isTemporary);
      const matchingIsTemporary = Boolean(matchingModel.isTemporary);

      if (!selectedModel || !isSameKey || (selectedIsTemporary && !matchingIsTemporary)) {
        this.setState({
          selectedModel: matchingModel,
          pendingModelKey: matchingIsTemporary ? pendingModelKey : null,
          pendingModelSnapshot: matchingIsTemporary ? pendingModelSnapshot : null
        }, () => {
          if (!matchingIsTemporary) {
            this.broadcastModelSelection(matchingModel);
          }
        });
        return;
      }

      if (!matchingIsTemporary) {
        this.setState({ pendingModelKey: null, pendingModelSnapshot: null });
      }

      return;
    }

    if (pendingModelSnapshot && !models.some(model => this.getModelKeyFromInfo(model) === pendingModelKey)) {
      this.setState(prevState => ({
        models: [...prevState.models, pendingModelSnapshot]
      }));
    }
  };

  private resolvePendingPersonaSelection = () => {
    const { pendingPersonaId, showPersonaSelection, personas, selectedPersona } = this.state;

    if (!showPersonaSelection) {
      if (pendingPersonaId) {
        this.setState({ pendingPersonaId: null });
      }
      return;
    }

    if (!pendingPersonaId) {
      return;
    }

    const normalizedPendingId = `${pendingPersonaId}`;

    if (selectedPersona && `${selectedPersona.id}` === normalizedPendingId) {
      this.setState({ pendingPersonaId: null });
      return;
    }

    const existingPersona = personas.find(persona => `${persona.id}` === normalizedPendingId);
    if (existingPersona) {
      this.setState({
        selectedPersona: existingPersona,
        pendingPersonaId: null
      });
      return;
    }

    if (!this.props.services?.api) {
      return;
    }

    if (this.pendingPersonaRequestId === normalizedPendingId) {
      return;
    }

    this.pendingPersonaRequestId = normalizedPendingId;

    this.fetchPersonaById(normalizedPendingId)
      .then(persona => {
        if (!persona) {
          return;
        }

        const personaId = `${persona.id}`;

        if (!this.state.pendingPersonaId || `${this.state.pendingPersonaId}` !== personaId) {
          return;
        }

        this.setState(prevState => {
          const alreadyExists = prevState.personas.some(p => `${p.id}` === personaId);
          const personasList = alreadyExists
            ? prevState.personas
            : [...prevState.personas, { ...persona, id: personaId }];

          return {
            personas: personasList,
            selectedPersona: personasList.find(p => `${p.id}` === personaId) || null,
            pendingPersonaId: null
          };
        });
      })
      .catch(error => {
        console.error('Error resolving pending persona:', error);
      })
      .finally(() => {
        this.pendingPersonaRequestId = null;
      });
  };

  private fetchPersonaById = async (personaId: string): Promise<PersonaInfo | null> => {
    if (!this.props.services?.api) {
      return null;
    }

    try {
      const response = await this.props.services.api.get(`/api/v1/personas/${personaId}`);
      const personaCandidate: any = response?.persona || response?.data || response;
      if (personaCandidate && personaCandidate.id) {
        return {
          ...personaCandidate,
          id: `${personaCandidate.id}`
        } as PersonaInfo;
      }
    } catch (error) {
      console.error('Error fetching persona by id:', error);
    }

    return null;
  };

  /**
   * Handle conversation selection
   */
  handleConversationSelect = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const conversationId = event.target.value;
    
    console.log(`📋 Conversation selected: ${conversationId || 'new chat'}`);
    
    if (!conversationId) {
      // New chat selected
      this.handleNewChatClick();
      return;
    }
    
    const selectedConversation = this.state.conversations.find(
      conv => conv.id === conversationId
    );
    
    if (selectedConversation) {
      console.log(`📂 Loading conversation: ${conversationId}`);
      this.setState({ selectedConversation }, () => {
        // Use the new persona-aware conversation loading method
        this.loadConversationWithPersona(conversationId);
      });
    }
  };

  /**
   * Handle persona selection
   */
  handlePersonaChange = async (event: React.ChangeEvent<HTMLSelectElement>) => {
    const personaId = event.target.value;
    const selectedPersona = personaId
      ? this.state.personas.find(p => p.id === personaId) || null
      : null;
    
    console.log(`🎭 Persona changed: ${selectedPersona?.name || 'none'} (ID: ${personaId || 'none'})`);
    
    this.setState({ selectedPersona, pendingPersonaId: null }, () => {
      console.log(`🎭 Persona state after change: selectedPersona=${this.state.selectedPersona?.name || 'null'}, showPersonaSelection=${this.state.showPersonaSelection}`);
    });

    // If we have an active conversation, update its persona
    if (this.state.conversation_id) {
      try {
        await this.updateConversationPersona(this.state.conversation_id, personaId || null);
      } catch (error) {
        console.error('Failed to update conversation persona:', error);
        // Could show a user-friendly error message here
      }
    }
  };

  /**
   * Handle persona toggle (when turning personas on/off)
   */
  handlePersonaToggle = () => {
    // Reset to no persona when toggling off
    console.log('🎭 Persona toggled off - resetting to no persona');
    this.setState({ selectedPersona: null, pendingPersonaId: null }, () => {
      console.log(`🎭 Persona state after toggle: selectedPersona=${this.state.selectedPersona?.name || 'null'}, showPersonaSelection=${this.state.showPersonaSelection}`);
    });
  };

  /**
   * Handle new chat button click
   */
  handleNewChatClick = () => {
    console.log(`🆕 Starting new chat - clearing conversation_id`);
    this.setState({
      selectedConversation: null,
      conversation_id: null,
      messages: [],
      // Reset persona to null when starting new chat (respects persona toggle state)
      selectedPersona: this.state.showPersonaSelection ? this.state.selectedPersona : null,
      pendingModelKey: null,
      pendingModelSnapshot: null,
      pendingPersonaId: null
    }, async () => {
      console.log(`✅ New chat started - conversation_id: ${this.state.conversation_id}`);
      // Only use persona greeting if persona selection is enabled and a persona is selected
      const personaGreeting = this.state.showPersonaSelection && this.state.selectedPersona?.sample_greeting;
      const greetingContent = await this.buildInitialGreeting(personaGreeting || null);
      
      console.log(`🎭 New chat greeting: showPersonaSelection=${this.state.showPersonaSelection}, selectedPersona=${this.state.selectedPersona?.name || 'none'}, using=${personaGreeting ? 'persona' : 'default'} greeting`);
      
      if (greetingContent) {
        this.initialGreetingAdded = true;
        this.addMessageToChat({
          id: generateId('greeting'),
          sender: 'ai',
          content: greetingContent,
          timestamp: new Date().toISOString()
        });
      }
    });
  };

  /**
   * Handle renaming a conversation
   */
  handleRenameConversation = async (conversationId: string, newTitle?: string) => {
    // Close menu first
    this.setState({ openConversationMenu: null });
    
    if (!newTitle) {
      const conversation = this.state.conversations.find(c => c.id === conversationId);
      const promptResult = prompt('Enter new name:', conversation?.title || 'Untitled');
      if (!promptResult) return; // User cancelled
      newTitle = promptResult;
    }
    
    if (!this.props.services?.api) {
      throw new Error('API service not available');
    }

    try {
      await this.props.services.api.put(
        `/api/v1/conversations/${conversationId}`,
        { title: newTitle }
      );

      // Update the conversation in state
      this.setState(prevState => {
        const updatedConversations = prevState.conversations.map(conv =>
          conv.id === conversationId
            ? { ...conv, title: newTitle }
            : conv
        );

        const updatedSelectedConversation = prevState.selectedConversation?.id === conversationId
          ? { ...prevState.selectedConversation, title: newTitle }
          : prevState.selectedConversation;

        return {
          conversations: updatedConversations,
          selectedConversation: updatedSelectedConversation
        };
      });

    } catch (error: any) {
      throw new Error(`Error renaming conversation: ${error.message || 'Unknown error'}`);
    }
  };

  /**
   * Toggle conversation menu
   */
  toggleConversationMenu = (conversationId: string, event?: React.MouseEvent<HTMLButtonElement>) => {
    console.log('🔍 toggleConversationMenu called:', { conversationId, hasEvent: !!event });
    
    const isOpening = this.state.openConversationMenu !== conversationId;
    console.log('🔍 isOpening:', isOpening);
    
    if (isOpening) {
      // Simple toggle - CSS handles all positioning
      this.setState({
        openConversationMenu: conversationId
      }, () => {
        console.log('🔍 Menu opened for conversation:', conversationId);
      });
    } else {
      console.log('🔍 Closing menu');
      this.setState({
        openConversationMenu: null
      });
    }
  };

  /**
   * Handle sharing a conversation
   */
  handleShareConversation = async (conversationId: string) => {
    // Close menu
    this.setState({ openConversationMenu: null });
    
    // For now, just copy the conversation URL to clipboard
    try {
      const url = `${window.location.origin}${window.location.pathname}?conversation=${conversationId}`;
      await navigator.clipboard.writeText(url);
      
      // Show a temporary success message
      this.addMessageToChat({
        id: generateId('share-success'),
        sender: 'ai',
        content: '📋 Conversation link copied to clipboard!',
        timestamp: new Date().toISOString()
      });
    } catch (error) {
      this.addMessageToChat({
        id: generateId('share-error'),
        sender: 'ai',
        content: '❌ Failed to copy conversation link',
        timestamp: new Date().toISOString()
      });
    }
  };

  /**
   * Handle deleting a conversation
   */
  handleDeleteConversation = async (conversationId: string) => {
    // Close menu first
    this.setState({ openConversationMenu: null });
    
    if (!this.props.services?.api) {
      throw new Error('API service not available');
    }

    try {
      await this.props.services.api.delete(`/api/v1/conversations/${conversationId}`);

      // Update state to remove the conversation
      this.setState(prevState => {
        const updatedConversations = prevState.conversations.filter(
          conv => conv.id !== conversationId
        );

        // If the deleted conversation was selected, clear selection and start new chat
        const wasSelected = prevState.selectedConversation?.id === conversationId;

        return {
          conversations: updatedConversations,
          selectedConversation: wasSelected ? null : prevState.selectedConversation,
          conversation_id: wasSelected ? null : prevState.conversation_id,
          messages: wasSelected ? [] : prevState.messages,
          // Reset persona to null when starting new chat (respects persona toggle state)
          selectedPersona: wasSelected ? (prevState.showPersonaSelection ? prevState.selectedPersona : null) : prevState.selectedPersona
        };
      }, () => {
        // If we deleted the selected conversation, add greeting if available
        if (this.state.selectedConversation === null) {
          // Only use persona greeting if persona selection is enabled and a persona is selected
          // Ensure persona is null when personas are disabled
          const effectivePersona = this.state.showPersonaSelection ? this.state.selectedPersona : null;
          const personaGreeting = this.state.showPersonaSelection && effectivePersona?.sample_greeting;
          const greetingContent = personaGreeting
            ? personaGreeting
            : null;
          
          (async () => {
            const resolvedGreeting = await this.buildInitialGreeting(greetingContent);
            if (resolvedGreeting) {
              this.initialGreetingAdded = true;
              this.addMessageToChat({
                id: generateId('greeting'),
                sender: 'ai',
                content: resolvedGreeting,
                timestamp: new Date().toISOString()
              });
            }
          })();
        }
      });

    } catch (error: any) {
      throw new Error(`Error deleting conversation: ${error.message || 'Unknown error'}`);
    }
  };

  /**
   * Parse a stored message to reconstruct document context cards when loading history.
   */
  parseDocumentContext = (content: string) => {
    if (!content) return null;
    const headerMatch = content.match(/\[DOCUMENT CONTEXT\s*-\s*(.+?)\]/i);
    if (!headerMatch) return null;

    const filename = headerMatch[1]?.trim();
    const segmentMatch = content.match(/Segments:\s*(\d+)/i);
    const totalCharsMatch = content.match(/Total Input Chars:\s*(\d+)/i);
    const truncated = /\[TRUNCATED/i.test(content);

    return {
      context: content.trim(),
      filename,
      segmentCount: segmentMatch ? parseInt(segmentMatch[1], 10) : undefined,
      totalChars: totalCharsMatch ? parseInt(totalCharsMatch[1], 10) : undefined,
      truncated,
    };
  };

  /**
   * Load conversation history from the API
   */
  loadConversationHistory = async (conversationId: string) => {
    console.log(`📚 Loading conversation history: ${conversationId}`);
    
    if (!this.props.services?.api) {
      this.setState({ error: 'API service not available', isInitializing: false });
      return;
    }
    
    try {
      // Clear current conversation without showing initial greeting
      console.log(`🧹 Clearing messages for conversation load: ${conversationId}`);
      this.setState({
        messages: [],
        conversation_id: null,
        isLoadingHistory: true,
        error: ''
      });
      
      // Fetch conversation with messages
      const response = await this.props.services.api.get(
        `/api/v1/conversations/${conversationId}/with-messages`
      );
      
      // Mark that we've loaded a conversation, so don't show initial greeting
      this.initialGreetingAdded = true;
      
      // Process messages
      const messages: ChatMessage[] = [];
      
      if (response && response.messages && Array.isArray(response.messages)) {
        // Convert API message format to ChatMessage format, including reconstructed document context cards
        messages.push(...response.messages.map((msg: any) => {
          const rawContent: string = msg.message || '';
          const parsedDoc = this.parseDocumentContext(rawContent);

          return {
            id: msg.id || generateId('history'),
            sender: msg.sender === 'llm' ? 'ai' : 'user' as 'ai' | 'user',
            content: parsedDoc ? rawContent.trim() : this.cleanMessageContent(rawContent),
            timestamp: msg.created_at,
            ...(parsedDoc ? { isDocumentContext: true, documentData: parsedDoc } : {})
          };
        }));
      }
      
      // Update state
      this.setState({
        messages,
        conversation_id: conversationId,
        isLoadingHistory: false,
        isInitializing: false
      });
      
      console.log(`✅ Conversation history loaded: ${conversationId}, ${messages.length} messages`);
      
      // Scroll to bottom after loading history so the latest reply is visible
      setTimeout(() => {
        this.scrollToBottom({ force: true });
      }, 100);
      
    } catch (error) {
      // Error loading conversation history
      this.setState({
        isLoadingHistory: false,
        error: 'Error loading conversation history',
        isInitializing: false
      });
    }
  }

  /**
   * Load conversation history with persona and model auto-selection
   */
  loadConversationWithPersona = async (conversationId: string) => {
    console.log(`🔄 Loading conversation with persona: ${conversationId}`);
    
    if (!this.props.services?.api || !this.aiService) {
      this.setState({ error: 'API service not available', isInitializing: false });
      return;
    }
    
    try {
      // Clear current conversation without showing initial greeting
      this.setState({
        messages: [],
        conversation_id: null,
        isLoadingHistory: true,
        error: ''
      });
      
      // Get the selected conversation from state to access model/server info
      const selectedConversation = this.state.selectedConversation;
      
      // Try to fetch conversation with persona details first
      let conversationWithPersona: ConversationWithPersona | null = null;
      try {
        conversationWithPersona = await this.aiService.loadConversationWithPersona(conversationId);
      } catch (error) {
        // If the new endpoint doesn't exist yet, fall back to regular conversation loading
        console.warn('Persona-aware conversation loading not available, falling back to regular loading');
        // Use the selected conversation data we already have
        conversationWithPersona = selectedConversation;
      }
      
      const showPersonaSelection = this.state.showPersonaSelection;
      const personaFromConversation = showPersonaSelection && conversationWithPersona?.persona
        ? { ...conversationWithPersona.persona, id: `${conversationWithPersona.persona.id}` }
        : null;
      const personaIdFromConversation = showPersonaSelection
        ? (personaFromConversation?.id
          || (conversationWithPersona?.persona_id ? `${conversationWithPersona.persona_id}` : null))
        : null;
      const pendingPersonaId = personaIdFromConversation && personaIdFromConversation.trim() !== ''
        ? personaIdFromConversation
        : null;

      const modelName = conversationWithPersona?.model?.trim();
      const serverName = conversationWithPersona?.server?.trim();
      const hasModelMetadata = Boolean(modelName && serverName && !this.isEmbeddingModelName(modelName));

      const pendingModelKey = hasModelMetadata
        ? this.getModelKey(modelName, serverName)
        : null;
      const matchingModel = pendingModelKey
        ? this.state.models.find(model => this.getModelKeyFromInfo(model) === pendingModelKey)
        : null;
      const pendingModelSnapshot = pendingModelKey && !matchingModel && hasModelMetadata
        ? {
            name: modelName!,
            provider: 'ollama',
            providerId: 'ollama_servers_settings',
            serverName: serverName!,
            serverId: 'unknown',
            isTemporary: true
          } as ModelInfo
        : null;

      const previousSelectedModelKey = this.getModelKeyFromInfo(this.state.selectedModel);

      this.setState(prevState => {
        const nextState: Partial<BrainDriveChatState> = {
          pendingModelKey,
          pendingModelSnapshot,
          pendingPersonaId,
        };

        if (matchingModel) {
          nextState.selectedModel = matchingModel;
        } else if (pendingModelSnapshot) {
          nextState.selectedModel = pendingModelSnapshot;
        } else if (!pendingModelKey) {
          nextState.pendingModelKey = null;
          nextState.pendingModelSnapshot = null;
        }

        if (showPersonaSelection) {
          if (personaFromConversation) {
            const existingPersona = prevState.personas.find(p => `${p.id}` === personaFromConversation.id);
            if (existingPersona) {
              nextState.selectedPersona = existingPersona;
            } else {
              nextState.personas = [...prevState.personas, personaFromConversation];
              nextState.selectedPersona = personaFromConversation;
            }
          } else if (pendingPersonaId) {
            nextState.pendingPersonaId = pendingPersonaId;
            const existingPersona = prevState.personas.find(p => `${p.id}` === pendingPersonaId);
            nextState.selectedPersona = existingPersona || null;
          } else {
            nextState.selectedPersona = null;
            nextState.pendingPersonaId = null;
          }
        } else {
          nextState.selectedPersona = null;
          nextState.pendingPersonaId = null;
        }

        return nextState as Pick<BrainDriveChatState, keyof BrainDriveChatState>;
      }, () => {
        const newSelectedModelKey = this.getModelKeyFromInfo(this.state.selectedModel);
        if (
          (matchingModel || pendingModelSnapshot) &&
          newSelectedModelKey &&
          newSelectedModelKey !== previousSelectedModelKey
        ) {
          const currentModel = this.state.selectedModel;
          if (currentModel) {
            this.broadcastModelSelection(currentModel);
          }
        }

        if (pendingModelKey) {
          this.resolvePendingModelSelection();
        }
        if (this.state.pendingPersonaId) {
          this.resolvePendingPersonaSelection();
        }
      });
      
      // Now load the conversation messages using the regular method
      await this.loadConversationHistory(conversationId);
      
      console.log(`✅ Conversation loaded successfully: ${conversationId}`);
      
    } catch (error) {
      console.error('Error loading conversation with persona:', error);
      // Fall back to regular conversation loading
      await this.loadConversationHistory(conversationId);
    }
  };

  /**
   * Update conversation's persona
   */
  updateConversationPersona = async (conversationId: string, personaId: string | null) => {
    if (!this.aiService) {
      throw new Error('AI service not available');
    }

    try {
      await this.aiService.updateConversationPersona(conversationId, personaId);
    } catch (error) {
      console.error('Error updating conversation persona:', error);
      throw error;
    }
  };

  /**
   * Stop ongoing generation
   */
  stopGeneration = async () => {
    console.log('🛑 stopGeneration called');
    
    // Abort the frontend request immediately
    if (this.currentStreamingAbortController) {
      this.currentStreamingAbortController.abort();
      this.currentStreamingAbortController = null;
    }
    
    // Try to cancel backend generation (best effort)
    if (this.aiService && this.state.conversation_id) {
      try {
        await this.aiService.cancelGeneration(this.state.conversation_id);
      } catch (error) {
        console.error('Error canceling backend generation:', error);
        // Continue anyway - the AbortController should handle the cancellation
      }
    }
    
    // Immediately update UI state - keep the partial response but mark it as stopped
    this.setState(prevState => {
      console.log('🛑 Updating message states, current messages:', prevState.messages.length);
      
      const updatedMessages = prevState.messages.map(message => {
        const shouldUpdate = message.isStreaming;
        if (shouldUpdate) {
          console.log(`🛑 Updating streaming message ${message.id} with canContinue: true, isCutOff: true`);
        }
        
        return {
          ...message,
          isStreaming: false,
          canRegenerate: true,
          // Only set canContinue and isCutOff for messages that are currently streaming
          canContinue: shouldUpdate ? true : message.canContinue,
          isCutOff: shouldUpdate ? true : message.isCutOff
        };
      });
      
      return {
        isStreaming: false,
        isLoading: false,
        messages: updatedMessages
      };
    }, () => {
      console.log('🛑 Message states updated, focusing input');
      // Focus the input after stopping
      this.focusInput();
    });
  };

  /**
   * Continue generation from where it left off by replacing the stopped message
   */
  continueGeneration = async () => {
    const lastAiMessage = this.state.messages
      .filter(msg => msg.sender === 'ai')
      .pop();
    
    if (lastAiMessage && lastAiMessage.canContinue) {
      // Find the last user message to get the original prompt
      const lastUserMessage = [...this.state.messages]
        .reverse()
        .find(msg => msg.sender === 'user');
      
      if (!lastUserMessage) return;
      
      // Remove the cut-off message
      this.setState(prevState => ({
        messages: prevState.messages.filter(msg => msg.id !== lastAiMessage.id)
      }), async () => {
        // Send the original prompt to continue generation
        await this.sendPromptToAI(lastUserMessage.content);
      });
    }
  };

  /**
   * Regenerate the last AI response
   */
  regenerateResponse = async () => {
    const lastUserMessage = this.state.messages
      .filter(msg => msg.sender === 'user')
      .pop();
    
    if (lastUserMessage) {
      // Remove the last AI response (all messages after the last user message)
      this.setState(prevState => {
        const lastUserIndex = prevState.messages.findIndex(msg => msg.id === lastUserMessage.id);
        return {
          messages: prevState.messages.slice(0, lastUserIndex + 1)
        };
      }, () => {
        // Regenerate the response
        this.sendPromptToAI(lastUserMessage.content);
      });
    }
  };

  /**
   * Start editing a user message
   */
  startEditingMessage = (messageId: string, content: string) => {
    this.setState({
      editingMessageId: messageId,
      editingContent: content
    });
  };

  /**
   * Cancel editing a message
   */
  cancelEditingMessage = () => {
    this.setState({
      editingMessageId: null,
      editingContent: ''
    });
  };

  /**
   * Toggle markdown view for a message
   */
  toggleMarkdownView = (messageId: string) => {
    this.setState(prevState => ({
      messages: prevState.messages.map(message => {
        if (message.id === messageId) {
          return {
            ...message,
            showRawMarkdown: !message.showRawMarkdown
          };
        }
        return message;
      })
    }));
  };

  /**
   * Save edited message and regenerate response
   */
  saveEditedMessage = async () => {
    const { editingMessageId, editingContent } = this.state;
    
    if (!editingMessageId || !editingContent.trim()) {
      return;
    }

    // Update the message content
    this.setState(prevState => ({
      messages: prevState.messages.map(message => {
        if (message.id === editingMessageId) {
          return {
            ...message,
            content: editingContent.trim(),
            isEdited: true,
            originalContent: message.originalContent || message.content
          };
        }
        return message;
      }),
      editingMessageId: null,
      editingContent: ''
    }), async () => {
      // Find the edited message and regenerate the response
      const editedMessage = this.state.messages.find(msg => msg.id === editingMessageId);
      if (editedMessage) {
        // Remove all messages after the edited message
        this.setState(prevState => ({
          messages: prevState.messages.slice(0, prevState.messages.findIndex(msg => msg.id === editingMessageId) + 1)
        }), () => {
          // Regenerate the response
          this.sendPromptToAI(editedMessage.content);
        });
      }
    });
  };

  /**
   * Handle file upload button click
   */
  handleFileUploadClick = () => {
    // Create a hidden file input and trigger it
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = false;
    fileInput.accept = FILE_CONFIG.ACCEPTED_EXTENSIONS;
    fileInput.style.display = 'none';
    
    fileInput.onchange = async (event) => {
      const files = (event.target as HTMLInputElement).files;
      if (!files || files.length === 0) return;

      if (!this.documentService) {
        this.setState({ error: 'Document service not available' });
        return;
      }

      this.setState({ isProcessingDocuments: true });

      try {
        const file = files[0];

        // Process the file with chunking
        const contextResult = await this.documentService!.processTextContext(file);

        this.promptDocumentMode(contextResult);
      } catch (error) {
        this.setState({ error: `Error processing documents: ${error instanceof Error ? error.message : 'Unknown error'}` });
      } finally {
        this.setState({ isProcessingDocuments: false });
      }
    };

    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
  };

  /**
   * Handle processed text/markdown context result
   */
  handleDocumentContextProcessed = (result: DocumentContextResult, mode: 'one-shot' | 'persist') => {
    const documentContext = this.documentService!.formatSegmentsForChatContext(result);
    const info = {
      filename: result.filename || '',
      segmentCount: result.segment_count,
      totalChars: result.total_input_chars,
      truncated: result.truncated,
      mode
    };

    this.setState({
      documentContext,
      documentContextMode: mode,
      documentContextInjectedConversationId: null,
      documentContextInfo: info
    }, () => {
      const documentMessage: ChatMessage = {
        id: generateId('documents'),
        sender: 'ai',
        content: '',
        timestamp: new Date().toISOString(),
        isDocumentContext: true,
        documentData: {
          context: documentContext,
          filename: result.filename || '',
          segmentCount: result.segment_count,
          totalChars: result.total_input_chars,
          truncated: result.truncated,
          mode
        }
      };

      this.addMessageToChat(documentMessage);
    });
  };

  /**
   * Prompt user for document mode with a custom dialog
   */
  promptDocumentMode = (contextResult: DocumentContextResult) => {
    // Render a lightweight modal overlay
    const overlay = document.createElement('div');
    overlay.className = 'bd-dialog-backdrop';

    const modal = document.createElement('div');
    modal.className = 'bd-dialog';

    const header = document.createElement('div');
    header.className = 'bd-dialog-header';
    header.innerText = 'Use this document as chat context?';

    const body = document.createElement('div');
    body.className = 'bd-dialog-body';
    body.innerHTML = `
      <div class="bd-dialog-meta">
        <div class="bd-dialog-filename">${contextResult.filename}</div>
        <div class="bd-dialog-details">
          ${contextResult.segment_count} segments • ${contextResult.total_input_chars} chars${contextResult.truncated ? ' • truncated' : ''}
        </div>
      </div>
      <p class="bd-dialog-copy">
        Choose how you want to use this context.
      </p>
      <div class="bd-dialog-options">
        <div class="bd-option">
          <div class="bd-option-title">Save to conversation</div>
          <div class="bd-option-desc">Reuse on reopen; adds a system message to history.</div>
        </div>
        <div class="bd-option">
          <div class="bd-option-title">One-shot only</div>
          <div class="bd-option-desc">Send once for this turn; not saved to history.</div>
        </div>
      </div>
    `;

    const footer = document.createElement('div');
    footer.className = 'bd-dialog-footer';

    const persistBtn = document.createElement('button');
    persistBtn.className = 'bd-dialog-btn primary';
    persistBtn.innerText = 'Save to conversation';

    const oneShotBtn = document.createElement('button');
    oneShotBtn.className = 'bd-dialog-btn ghost';
    oneShotBtn.innerText = 'One-shot only';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'bd-dialog-btn text';
    cancelBtn.innerText = 'Cancel';

    const cleanup = () => {
      document.body.removeChild(overlay);
    };

    persistBtn.onclick = () => {
      cleanup();
      this.handleDocumentContextProcessed(contextResult, 'persist');
    };

    oneShotBtn.onclick = () => {
      cleanup();
      this.handleDocumentContextProcessed(contextResult, 'one-shot');
    };

    cancelBtn.onclick = cleanup;

    footer.appendChild(cancelBtn);
    footer.appendChild(oneShotBtn);
    footer.appendChild(persistBtn);

    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(footer);
    overlay.appendChild(modal);

    document.body.appendChild(overlay);
  };

  /**
   * Handle document processing
   */
  handleDocumentsProcessed = (results: DocumentProcessingResult[]) => {
    if (results.length === 0) return;

    // Format document context for chat
    let documentContext = '';
    if (results.length === 1) {
      documentContext = this.documentService!.formatTextForChatContext(results[0]);
    } else {
      documentContext = this.documentService!.formatMultipleTextsForChatContext(results);
    }

    // Add document context to state
    this.setState({ documentContext }, () => {
      // Add a message to show the documents were processed
      const documentMessage: ChatMessage = {
        id: generateId('documents'),
        sender: 'ai',
        content: '',
        timestamp: new Date().toISOString(),
        isDocumentContext: true,
        documentData: {
          results,
          context: documentContext
        }
      };

      this.addMessageToChat(documentMessage);
    });
  };

  /**
   * Handle document processing errors
   */
  handleDocumentError = (error: string) => {
    this.setState({ error });
  };

  /**
   * Handle key press events for global shortcuts
   */
  handleGlobalKeyPress = (e: KeyboardEvent) => {
    // ESC key to stop generation
    if (e.key === 'Escape' && this.state.isStreaming) {
      e.preventDefault();
      this.stopGeneration();
    }
    
    // ESC key to close conversation menu
    if (e.key === 'Escape' && this.state.openConversationMenu) {
      e.preventDefault();
      this.setState({ openConversationMenu: null });
    }
  };

  /**
   * Handle click outside to close conversation menu
   */
  handleClickOutside = (e: MouseEvent) => {
    if (!this.state.openConversationMenu) return;
    
    const target = e.target as Element;
    
    // Don't close if clicking on the menu button or menu itself
    if (target.closest('.history-action-button') || target.closest('.conversation-menu')) {
      return;
    }
    
    // Close the menu
    this.setState({ openConversationMenu: null });
  };

  /**
   * Toggle history accordion
   */
  toggleHistoryAccordion = () => {
    this.setState(prevState => ({
      isHistoryExpanded: !prevState.isHistoryExpanded
    }));
  };

  /**
   * Auto-close accordions on first message
   */
  autoCloseAccordionsOnFirstMessage = () => {
    // Only close if this is the first user message in a new conversation
    const userMessages = this.state.messages.filter(msg => msg.sender === 'user');
    if (userMessages.length === 1 && !this.state.conversation_id) {
      this.setState({
        isHistoryExpanded: false
      });
    }
  };



  /**
   * Build comprehensive search context to inject into user prompt
   */
  buildSearchContextForPrompt = (searchResponse: any, scrapedContent: any): string => {
    let context = `Search Results for "${searchResponse.query}":\n\n`;
    
    // Add basic search results
    if (searchResponse.results && searchResponse.results.length > 0) {
      searchResponse.results.slice(0, 5).forEach((result: any, index: number) => {
        context += `${index + 1}. ${result.title}\n`;
        context += `   URL: ${result.url}\n`;
        if (result.content) {
          const cleanContent = result.content.replace(/\s+/g, ' ').trim().substring(0, 200);
          context += `   Summary: ${cleanContent}${result.content.length > 200 ? '...' : ''}\n`;
        }
        context += '\n';
      });
    }

    // Add detailed scraped content
    if (scrapedContent && scrapedContent.results && scrapedContent.results.length > 0) {
      context += '\nDetailed Content from Web Pages:\n\n';
      
      scrapedContent.results.forEach((result: any, index: number) => {
        if (result.success && result.content) {
          // Find the corresponding search result for title
          const searchResult = searchResponse.results.find((sr: any) => sr.url === result.url);
          const title = searchResult?.title || `Content from ${result.url}`;
          
          context += `Page ${index + 1}: ${title}\n`;
          context += `Source: ${result.url}\n`;
          context += `Full Content: ${result.content}\n\n`;
        }
      });
      
      context += `(Successfully scraped ${scrapedContent.summary.successful_scrapes} out of ${scrapedContent.summary.total_urls} pages)\n`;
    }

    context += '\nPlease use this web search and scraped content information to provide an accurate, up-to-date answer to the user\'s question.';
    
    return context;
  };

  /**
   * Clean up message content by removing excessive newlines and search/document context
   */
  cleanMessageContent = (content: string): string => {
    if (!content) return content;
    
    let cleanedContent = content
      .replace(/\r\n/g, '\n')      // Normalize line endings
      .replace(/\n{3,}/g, '\n\n')  // Replace 3+ newlines with 2 (paragraph break)
      .trim();                     // Remove leading/trailing whitespace
    
    // Remove web search context that might have been stored in old messages
    cleanedContent = cleanedContent.replace(/\n\n\[WEB SEARCH CONTEXT[^]*$/, '');
    
    // Remove document context that might have been stored in old messages
    cleanedContent = cleanedContent.replace(/^Document Context:[^]*?\n\nUser Question: /, '');
    cleanedContent = cleanedContent.replace(/^[^]*?\n\nUser Question: /, '');
    
    return cleanedContent.trim();
  };

  /**
   * Add a new message to the chat history
   */
  addMessageToChat = (message: ChatMessage) => {
    // Clean up the message content
    const cleanedMessage = {
      ...message,
      content: this.cleanMessageContent(message.content)
    };
    
    console.log(`💬 Adding message to chat: ${cleanedMessage.sender} - ${cleanedMessage.content.substring(0, 50)}...`);
    this.setState(prevState => ({
      messages: [...prevState.messages, cleanedMessage]
    }), () => {
      console.log(`✅ Message added. Total messages: ${this.state.messages.length}`);
    });
  }

  /**
   * Determine how far above the live edge we should keep the viewport.
   * Ensures we never hide the entire final message when it's short.
   */
  private getEffectiveAnchorOffset = (container: HTMLDivElement): number => {
    const lastMessage = this.state.messages[this.state.messages.length - 1];
    if (lastMessage?.isStreaming) {
      return 0;
    }

    const baseOffset = Math.max(this.SCROLL_ANCHOR_OFFSET, 0);
    if (baseOffset === 0) {
      return 0;
    }

    const lastMessageElement = container.querySelector('.message:last-of-type') as HTMLElement | null;
    if (!lastMessageElement) {
      return baseOffset;
    }

    const lastMessageHeight = lastMessageElement.offsetHeight;
    const maxAllowableOffset = Math.max(lastMessageHeight - this.MIN_VISIBLE_LAST_MESSAGE_HEIGHT, 0);
    return Math.min(baseOffset, maxAllowableOffset);
  };

  private getScrollMetrics = () => {
    const container = this.chatHistoryRef.current;
    if (!container) {
      return {
        distanceFromBottom: 0,
        dynamicOffset: 0
      };
    }

    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    const dynamicOffset = this.getEffectiveAnchorOffset(container);

    return { distanceFromBottom, dynamicOffset };
  };

  private getConversationSortTimestamp = (conversation: any): number => {
    if (!conversation || typeof conversation !== 'object') {
      return 0;
    }

    const candidateFields = [
      conversation.last_message_at,
      conversation.lastMessageAt,
      conversation.latest_message_at,
      conversation.latestMessageAt,
      conversation.started_at,
      conversation.startedAt,
      conversation.updated_at,
      conversation.updatedAt,
      conversation.created_at,
      conversation.createdAt
    ];

    for (const maybeDate of candidateFields) {
      if (!maybeDate) continue;
      const timestamp = new Date(maybeDate).getTime();
      if (!Number.isNaN(timestamp)) {
        return timestamp;
      }
    }

    return 0;
  };

  private sortConversationsByRecency = (conversations: any[]): any[] => {
    return [...conversations].sort((a, b) => {
      const timeA = this.getConversationSortTimestamp(a);
      const timeB = this.getConversationSortTimestamp(b);

      return timeB - timeA;
    });
  };

  /**
   * Check if user is near the bottom of the chat
   */
  isUserNearBottom = (thresholdOverride?: number) => {
    if (!this.chatHistoryRef.current) return true;

    const { distanceFromBottom, dynamicOffset } = this.getScrollMetrics();
    const threshold = thresholdOverride ?? Math.max(dynamicOffset, this.NEAR_BOTTOM_EPSILON);

    return distanceFromBottom <= threshold;
  };

  private hasRecentUserIntent = () => {
    if (!this.lastUserScrollTs) {
      return false;
    }

    return Date.now() - this.lastUserScrollTs <= this.USER_SCROLL_INTENT_GRACE_MS;
  };

  private canAutoScroll = (requestedAt: number = Date.now()) => {
    if (this.state.isAutoScrollLocked) {
      return false;
    }

    if (this.lastUserScrollTs && this.lastUserScrollTs > requestedAt) {
      return false;
    }

    return this.isUserNearBottom();
  };

  private cancelPendingAutoScroll = () => {
    if (this.pendingAutoScrollTimeout) {
      clearTimeout(this.pendingAutoScrollTimeout);
      this.pendingAutoScrollTimeout = null;
    }
  };

  private registerUserScrollIntent = () => {
    this.lastUserScrollTs = Date.now();
    this.cancelPendingAutoScroll();

    this.setState(prevState => {
      if (prevState.isAutoScrollLocked && prevState.showScrollToBottom) {
        return null;
      }

      return {
        isAutoScrollLocked: true,
        showScrollToBottom: true
      };
    });
  };

  /**
   * Update scroll state based on current position
   */
  updateScrollState = (options: { fromUser?: boolean; manualUnlock?: boolean } = {}) => {
    if (!this.chatHistoryRef.current) return;

    const { fromUser = false, manualUnlock = false } = options;
    const { distanceFromBottom, dynamicOffset } = this.getScrollMetrics();
    const nearBottomThreshold = Math.max(dynamicOffset, this.NEAR_BOTTOM_EPSILON);
    const isNearBottom = distanceFromBottom <= nearBottomThreshold;
    const isAtStrictBottom = distanceFromBottom <= this.STRICT_BOTTOM_THRESHOLD;

    let shouldClearUserIntent = false;
    let shouldSuppressManualIntent = false;

    this.setState(prevState => {
      let isAutoScrollLocked = prevState.isAutoScrollLocked;

      if (manualUnlock) {
        if (isAutoScrollLocked) {
          shouldClearUserIntent = true;
        }
        isAutoScrollLocked = false;
      } else if (fromUser) {
        if (isAtStrictBottom) {
          if (isAutoScrollLocked) {
            shouldClearUserIntent = true;
          }
          isAutoScrollLocked = false;
          shouldSuppressManualIntent = true;
        } else {
          isAutoScrollLocked = true;
        }
      } else if (isAtStrictBottom && prevState.isAutoScrollLocked && !this.hasRecentUserIntent()) {
        isAutoScrollLocked = false;
        shouldClearUserIntent = true;
      }

      const nextShowScrollToBottom = isAutoScrollLocked ? true : !isAtStrictBottom;

      if (
        prevState.isNearBottom === isNearBottom &&
        prevState.showScrollToBottom === nextShowScrollToBottom &&
        prevState.isAutoScrollLocked === isAutoScrollLocked
      ) {
        return null;
      }

      return {
        isNearBottom,
        showScrollToBottom: nextShowScrollToBottom,
        isAutoScrollLocked
      };
    }, () => {
      if (shouldClearUserIntent && !this.state.isAutoScrollLocked) {
        this.lastUserScrollTs = 0;
      }

      if (shouldSuppressManualIntent) {
        this.lastUserScrollTs = 0;
      }
    });
  };

  /**
   * Handle scroll events to track user scroll position
   */
  handleScroll = () => {
    if (this.isProgrammaticScroll) {
      this.updateScrollState();
      return;
    }

    this.registerUserScrollIntent();
    this.updateScrollState({ fromUser: true });
  };

  handleUserScrollIntent = (_source: 'pointer' | 'wheel' | 'touch' | 'key') => {
    this.registerUserScrollIntent();
  };

  handleScrollToBottomClick = () => {
    this.scrollToBottom({ behavior: 'smooth', manual: true });
  };

  private followStreamIfAllowed = () => {
    if (this.canAutoScroll()) {
      this.scrollToBottom();
    } else {
      this.updateScrollState();
    }
  };

  /**
   * Scroll the chat history to the bottom while respecting the anchor offset
   */
  scrollToBottom = (options: ScrollToBottomOptions = {}) => {
    if (!this.chatHistoryRef.current) return;

    this.cancelPendingAutoScroll();
    const { behavior = 'auto', manual = false, force = false } = options;
    const container = this.chatHistoryRef.current;

    const useAnchorOffset = !(manual || force);
    const dynamicOffset = useAnchorOffset ? this.getEffectiveAnchorOffset(container) : 0;
    const maxScrollTop = Math.max(container.scrollHeight - container.clientHeight, 0);
    const targetTop = Math.max(maxScrollTop - dynamicOffset, 0);

    this.isProgrammaticScroll = true;

    if (typeof container.scrollTo === 'function') {
      try {
        container.scrollTo({ top: targetTop, behavior });
      } catch (_err) {
        container.scrollTop = targetTop;
      }
    } else {
      container.scrollTop = targetTop;
    }

    const finalize = () => {
      this.isProgrammaticScroll = false;
      if (manual || force) {
        this.lastUserScrollTs = 0;
        this.updateScrollState({ manualUnlock: true });
      } else {
        this.updateScrollState();
      }
    };

    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(finalize);
    } else {
      setTimeout(finalize, 0);
    }
  }

  /**
   * Focus the input field
   */
  focusInput = () => {
    if (this.inputRef.current) {
      // Small delay to ensure the UI has updated
      setTimeout(() => {
        if (this.inputRef.current) {
          this.inputRef.current.focus();
        }
      }, 100);
    }
  };

  /**
   * Handle input change
   */
  handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    this.setState({ inputText: e.target.value });
    
    // Auto-resize the textarea: 1 → 4 lines, then scroll
    if (this.inputRef.current) {
      const ta = this.inputRef.current;
      ta.style.height = 'auto';
      const computed = window.getComputedStyle(ta);
      const lineHeight = parseFloat(computed.lineHeight || '0') || 24; // fallback if not computable
      const maxHeight = lineHeight * 4; // 4 lines max
      ta.style.height = `${Math.min(ta.scrollHeight, maxHeight)}px`;
    }
  };

  /**
   * Handle key press in the input field
   */
  handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Send message on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.handleSendMessage();
    }
  };

  /**
   * Toggle Library scope on/off
   */
  handleLibraryToggle = () => {
    this.setState(prevState => ({
      libraryScope: {
        ...prevState.libraryScope,
        enabled: !prevState.libraryScope.enabled,
        project: !prevState.libraryScope.enabled ? prevState.libraryScope.project : null,
      }
    }));
  };

  /**
   * Select a Library project (null = "All")
   */
  handleLibrarySelectProject = (project: LibraryProject | null) => {
    this.setState({
      libraryScope: {
        enabled: true,
        project,
      }
    });
  };

  /**
   * Load library projects list
   */
  loadLibraryProjects = async () => {
    if (!this.libraryService) return;
    try {
      const projects = await this.libraryService.fetchProjects('active');
      this.libraryProjects = projects;
      this.forceUpdate();
    } catch (err) {
      console.error('Failed to load library projects:', err);
    }
  };

  /**
   * Handle sending a message
   */
  handleSendMessage = () => {
    const { inputText } = this.state;
    
    // Don't send empty messages
    if (!inputText.trim() || this.state.isLoading) return;
    
    // Add user message to chat (will be updated with search context if web search is enabled)
    const userMessageId = generateId('user');
    const userMessage: ChatMessage = {
      id: userMessageId,
      sender: 'user',
      content: inputText.trim(),
      timestamp: new Date().toISOString(),
      isEditable: true
    };
    
    this.addMessageToChat(userMessage);

    if (typeof window !== 'undefined') {
      const schedule = window.requestAnimationFrame || ((cb: FrameRequestCallback) => window.setTimeout(cb, 0));
      schedule(() => this.scrollToBottom({ behavior: 'smooth', manual: true }));
    } else {
      this.scrollToBottom({ manual: true });
    }
    
    // Clear input
    this.setState({ inputText: '' });
    
    // Reset textarea height
    if (this.inputRef.current) {
      this.inputRef.current.style.height = 'auto';
    }
    
    // Send to AI and get response
    this.sendPromptToAI(userMessage.content, userMessageId);
    
    // Auto-close accordions on first message
    this.autoCloseAccordionsOnFirstMessage();
  };

  /**
   * Send prompt to AI provider and handle response
   */
  sendPromptToAI = async (prompt: string, userMessageId?: string) => {
    if (!this.aiService || !this.props.services?.api) {
      this.setState({ error: 'API service not available' });
      return;
    }

    if (!this.state.selectedModel) {
      this.setState({ error: 'Please select a model first' });
      return;
    }
    
    console.log(`🚀 Sending prompt to AI with conversation_id: ${this.state.conversation_id || 'null (will create new)'}`);
    
    try {
      // Set loading and streaming state
      this.setState({ isLoading: true, isStreaming: true, error: '' });
      
      // Create abort controller for streaming
      this.currentStreamingAbortController = new AbortController();
      
      let enhancedPrompt = prompt;
      let retrievalDataForResponse: ChatMessage['retrievalData'] | null = null;

      // RAG retrieval (collections) if a collection is selected
      if (this.state.ragEnabled && this.state.selectedRagCollectionId && this.ragService) {
        try {
          const selectedCollection = this.getSelectedRagCollection();

          const history = this.state.messages
            .filter(m => !m.isDocumentContext && !m.isSearchResults && !m.isRetrievedContext)
            .slice(-6) // last ~3 turns (user+assistant pairs)
            .map(m => ({
              role: m.sender === 'user' ? 'user' as const : 'assistant' as const,
              content: m.content,
            }));

          const retrievalResult = await this.ragService.retrieveContext(
            prompt,
            this.state.selectedRagCollectionId,
            history
          );

          if (retrievalResult && retrievalResult.chunks && retrievalResult.chunks.length > 0) {
            const relevantContext = retrievalResult.chunks
              .map((chunk, idx) => `Excerpt ${idx + 1}:\n${chunk.content}`)
              .join('\n\n');

            const ragContextBlock = `[RAG CONTEXT - ${selectedCollection?.name || 'Collection'}]\n${relevantContext}\n[END RAG CONTEXT]`;
            enhancedPrompt = `${ragContextBlock}\n\nUser Question: ${prompt}`;

            retrievalDataForResponse = {
              collectionId: this.state.selectedRagCollectionId,
              collectionName: selectedCollection?.name,
              chunks: retrievalResult.chunks as any,
              context: ragContextBlock,
              intent: retrievalResult.intent,
              metadata: retrievalResult.metadata,
            };
          }
        } catch (ragError) {
          console.error('RAG retrieval error:', ragError);
          // Non-blocking: continue without RAG context
          this.addMessageToChat({
            id: generateId('rag-warning'),
            sender: 'ai',
            content: '⚠️ RAG retrieval failed. Continuing without collection context.',
            timestamp: new Date().toISOString()
          });
        }
      }
      
      // Library context injection if enabled
      if (this.state.libraryScope.enabled && this.libraryService) {
        try {
          let libraryContext = '';
          if (this.state.libraryScope.project) {
            const ctx = await this.libraryService.fetchProjectContext(
              this.state.libraryScope.project.slug,
              this.state.libraryScope.project.lifecycle || 'active'
            );
            if (ctx?.files) {
              const fileEntries = Object.entries(ctx.files)
                .map(([name, f]) => `--- ${name} ---\n${f.content}`)
                .join('\n\n');
              libraryContext = `[LIBRARY CONTEXT - Project: ${this.state.libraryScope.project.name}]\n${fileEntries}\n[END LIBRARY CONTEXT]`;
            }
          } else {
            libraryContext = '[LIBRARY CONTEXT - Scope: All projects]\nThe user has enabled Library access to all projects. You can help them read and write files in their BrainDrive Library.\n[END LIBRARY CONTEXT]';
          }
          if (libraryContext) {
            enhancedPrompt = `${libraryContext}\n\n${enhancedPrompt}`;
          }
        } catch (libError) {
          console.error('Library context error:', libError);
        }
      }

      // Perform web search if enabled

      if (this.state.useWebSearch && this.searchService) {
        try {
          this.setState({ isSearching: true });
          
          // Add a temporary search indicator message
          const searchIndicatorId = generateId('search-indicator');
          this.addMessageToChat({
            id: searchIndicatorId,
            sender: 'ai',
            content: '🔍 Searching the web...',
            timestamp: new Date().toISOString()
          });
          
          // Perform enhanced search with web scraping
          const { searchResponse, scrapedContent } = await this.searchService.searchWithScraping(prompt, { 
            category: 'general',
            language: 'en'
          }, 3, 3000); // Scrape top 3 results, max 3000 chars each
          
          // Remove the search indicator
          this.setState(prevState => ({
            messages: prevState.messages.filter(msg => msg.id !== searchIndicatorId)
          }));
          
          if (searchResponse.results.length > 0) {
            // Create a search results message with collapsible content
            const searchResultsMessage: ChatMessage = {
              id: generateId('search-results'),
              sender: 'ai',
              content: '', // Empty content since we're using searchData
              timestamp: new Date().toISOString(),
              isSearchResults: true,
              searchData: {
                query: searchResponse.query,
                results: searchResponse.results.slice(0, 5), // Show top 5 results
                scrapedContent: scrapedContent,
                totalResults: searchResponse.results.length,
                successfulScrapes: scrapedContent.summary.successful_scrapes
              }
            };
            
            // Add search results message to chat
            this.addMessageToChat(searchResultsMessage);



            // Inject search and scraped content directly into enhanced prompt for AI (not shown in chat)
            const searchContext = this.buildSearchContextForPrompt(searchResponse, scrapedContent);
            enhancedPrompt = `${prompt}\n\n[WEB SEARCH CONTEXT - Use this information to answer the user's question]\n${searchContext}`;
          } else {
            // Add a simple message for no results
            this.addMessageToChat({
              id: generateId('search-no-results'),
              sender: 'ai',
              content: 'No web search results found for your query. I will answer based on my knowledge.',
              timestamp: new Date().toISOString()
            });
          }
          
          this.setState({ isSearching: false });
          
        } catch (searchError) {
          console.error('Web search error:', searchError);
          this.setState({ isSearching: false });
          
          // Remove search indicator if it exists
          this.setState(prevState => ({
            messages: prevState.messages.filter(msg => !msg.content.includes('🔍 Searching the web...'))
          }));
          
          // Add error message
          this.addMessageToChat({
            id: generateId('search-error'),
            sender: 'ai',
            content: `⚠️ Web search failed: ${searchError instanceof Error ? searchError.message : 'Unknown error'}. I'll answer based on my knowledge.`,
            timestamp: new Date().toISOString()
          });
        }
      }
      
      // Create placeholder for AI response
      const placeholderId = generateId('ai');
      
      this.addMessageToChat({
        id: placeholderId,
        sender: 'ai',
        content: '',
        timestamp: new Date().toISOString(),
        isStreaming: true,
        ...(retrievalDataForResponse ? { retrievalData: retrievalDataForResponse } : {}),
      });
      
      // Track the current response content for proper abort handling
      let currentResponseContent = '';
      const shouldInjectDocumentContext = !!this.state.documentContext &&
        (!this.state.documentContextInjectedConversationId || this.state.documentContextInjectedConversationId !== this.state.conversation_id);
      const contextMessages = shouldInjectDocumentContext
        ? [{ role: 'system', content: this.state.documentContext }]
        : [];
      
      // Handle streaming chunks
      const onChunk = (chunk: string) => {
        currentResponseContent += chunk;
        this.setState(prevState => {
          const updatedMessages = prevState.messages.map(message => {
            if (message.id === placeholderId) {
              return {
                ...message,
                content: this.cleanMessageContent(currentResponseContent)
              };
            }
            return message;
          });

          return { ...prevState, messages: updatedMessages };
        }, this.followStreamIfAllowed);
      };
      
      // Handle conversation ID updates
      const onConversationId = (id: string) => {
        console.log(`🔄 Conversation ID received: ${id}`);
        this.setState(prev => ({
          conversation_id: id,
          documentContextInjectedConversationId: shouldInjectDocumentContext ? id : prev.documentContextInjectedConversationId
        }), () => {
          console.log(`✅ Conversation ID updated in state: ${this.state.conversation_id}`);
          // Refresh conversations list after a small delay to ensure backend has processed the conversation
          setTimeout(() => {
            this.refreshConversationsList();
          }, 1000);
        });
      };
      
      // Get current page context to pass to AI service
      const pageContext = this.getCurrentPageContext();
      
      // Send prompt to AI
      await this.aiService.sendPrompt(
        enhancedPrompt,
        this.state.selectedModel,
        this.state.useStreaming,
        this.state.conversation_id,
        this.props.conversationType || "chat",
        onChunk,
        onConversationId,
        pageContext,
        this.state.selectedPersona || undefined,
        this.currentStreamingAbortController,
        contextMessages,
        this.state.documentContextMode || undefined
      );

      if (shouldInjectDocumentContext && this.state.conversation_id && !this.state.documentContextInjectedConversationId) {
        const convId = this.state.conversation_id;
        this.setState(prev => ({
          documentContextInjectedConversationId: prev.documentContextInjectedConversationId || convId
        }));
      }
      
      // Finalize the message
      this.setState(prevState => {
        console.log('✅ Finalizing message with ID:', placeholderId);
        
        const updatedMessages = prevState.messages.map(message => {
          if (message.id === placeholderId) {
            const shouldPreserveContinue = message.isCutOff;
            console.log(`✅ Finalizing message ${message.id}, isCutOff: ${message.isCutOff}, preserving canContinue: ${shouldPreserveContinue}`);
            
            return {
              ...message,
              isStreaming: false,
              canRegenerate: true,
              // Preserve canContinue state if message was cut off, otherwise set to false
              canContinue: shouldPreserveContinue ? true : false
            };
          }
          return message;
        });
        
        return {
          messages: updatedMessages,
          isLoading: false,
          isStreaming: false
        };
      }, () => {
        console.log(`✅ Message finalized. Total messages: ${this.state.messages.length}`);
        this.followStreamIfAllowed();
        // Focus the input box after response is completed
        this.focusInput();
        
        // Refresh conversations list after the message is complete to include the new conversation
        if (this.state.conversation_id) {
          this.refreshConversationsList();
        }
      });
      
      // Clear abort controller
      this.currentStreamingAbortController = null;
      
    } catch (error) {
      // Check if this was an abort error
      if (error instanceof Error && error.name === 'AbortError') {
        // Request was aborted, keep the partial response and mark it as stopped
        this.setState(prevState => ({
          isLoading: false,
          isStreaming: false,
          messages: prevState.messages.map(message => ({
            ...message,
            isStreaming: false,
            canRegenerate: true,
            // Only set canContinue and isCutOff for messages that are currently streaming
            canContinue: message.isStreaming ? true : message.canContinue,
            isCutOff: message.isStreaming ? true : message.isCutOff
          }))
        }), () => {
          this.focusInput();
        });
      } else {
        // Real error occurred
        this.setState({
          isLoading: false,
          isStreaming: false,
          error: `Error sending prompt: ${error instanceof Error ? error.message : 'Unknown error'}`
        }, () => {
          // Focus input even on error so user can try again
          this.focusInput();
        });
      }
      
      // Clear abort controller
      this.currentStreamingAbortController = null;
    }
  };

  render() {
    const {
      inputText,
      messages,
      isLoading,
      isLoadingHistory,
      useStreaming,
      error,
      isInitializing,
      models,
      isLoadingModels,
      selectedModel,
      conversations,
      selectedConversation,
      showModelSelection,
      showConversationHistory,
      personas,
      selectedPersona,
      isLoadingPersonas,
      showPersonaSelection,
      useWebSearch,
      isSearching,
      isProcessingDocuments
    } = this.state;
    
    const { promptQuestion } = this.props;
    const themeClass = this.state.currentTheme === 'dark' ? 'dark-theme' : '';
    
    return (
      <div className={`braindrive-chat-container ${themeClass}`}>
        <div className="chat-paper">
          {/* Chat header with controls and history dropdown */}
          <ChatHeader
            models={models}
            selectedModel={selectedModel}
            isLoadingModels={isLoadingModels}
            onModelChange={this.handleModelChange}
            showModelSelection={showModelSelection}
            personas={personas}
            selectedPersona={selectedPersona}
            onPersonaChange={this.handlePersonaChange}
            showPersonaSelection={showPersonaSelection}
            conversations={conversations}
            selectedConversation={selectedConversation}
            onConversationSelect={this.handleConversationSelect}
            onNewChatClick={this.handleNewChatClick}
            onUploadClick={this.handleFileUploadClick}
            showConversationHistory={true}
            onRenameSelectedConversation={(id) => this.handleRenameConversation(id)}
            onDeleteSelectedConversation={(id) => this.handleDeleteConversation(id)}
            isLoading={isLoading}
            isLoadingHistory={isLoadingHistory}
            uploadDisabled={isProcessingDocuments}
          />
          
          {/* Show initializing state or chat content */}
          {isInitializing ? (
            <LoadingStates isInitializing={isInitializing} />
          ) : (
            <>
              {/* Chat history area */}
              <div className="chat-history-container">
                <ChatHistory
                  messages={messages}
                  isLoading={isLoading}
                  isLoadingHistory={isLoadingHistory}
                  error={error}
                  chatHistoryRef={this.chatHistoryRef}
                  editingMessageId={this.state.editingMessageId}
                  editingContent={this.state.editingContent}
                  onStartEditing={this.startEditingMessage}
                  onCancelEditing={this.cancelEditingMessage}
                  onSaveEditing={this.saveEditedMessage}
                  onEditingContentChange={(content) => this.setState({ editingContent: content })}
                  onRegenerateResponse={this.regenerateResponse}
                  onContinueGeneration={this.continueGeneration}
                  showScrollToBottom={this.state.showScrollToBottom}
                  onScrollToBottom={this.handleScrollToBottomClick}
                  onToggleMarkdown={this.toggleMarkdownView}
                  onScroll={this.handleScroll}
                  onUserScrollIntent={this.handleUserScrollIntent}
                />
              </div>
              
              
              {/* Chat input area */}
                <ChatInput
                inputText={inputText}
                isLoading={isLoading}
                isLoadingHistory={isLoadingHistory}
                isStreaming={this.state.isStreaming}
                selectedModel={selectedModel}
                promptQuestion={promptQuestion}
                onInputChange={this.handleInputChange}
                onKeyPress={this.handleKeyPress}
                onSendMessage={this.handleSendMessage}
                onStopGeneration={this.stopGeneration}
                onFileUpload={this.handleFileUploadClick}
                onToggleWebSearch={this.toggleWebSearchMode}
                useWebSearch={useWebSearch}
                webSearchDisabled={true}
                inputRef={this.inputRef}
                ragEnabled={this.state.ragEnabled}
                ragCollections={this.state.ragCollections}
                ragCollectionsLoading={this.state.ragCollectionsLoading}
                ragCollectionsError={this.state.ragCollectionsError}
                selectedRagCollectionId={this.state.selectedRagCollectionId}
                onRagSelectCollection={this.selectRagCollection}
                onRagCreateCollection={this.openCreateRagCollectionModal}
                onRagManageDocuments={this.openManageRagDocumentsModal}
                onRagRefreshCollections={() => this.loadRagCollections({ silent: true })}
                personas={personas}
                selectedPersona={selectedPersona}
                onPersonaChange={this.handlePersonaChange}
                onPersonaToggle={this.handlePersonaToggle}
                showPersonaSelection={false} // Moved to header
                libraryScope={this.state.libraryScope}
                libraryProjects={this.libraryProjects}
                onLibraryToggle={this.handleLibraryToggle}
                onLibrarySelectProject={this.handleLibrarySelectProject}
              />

              {this.state.ragEnabled && (
                <>
                  <CreateRagCollectionModal
                    isOpen={this.state.isCreateRagCollectionModalOpen}
                    onClose={this.closeCreateRagCollectionModal}
                    onCreate={this.handleCreateRagCollection}
                  />

                  <ManageRagDocumentsModal
                    isOpen={this.state.isManageRagDocumentsModalOpen}
                    onClose={this.closeManageRagDocumentsModal}
                    ragService={this.ragService}
                    collection={
                      this.state.manageRagDocumentsCollectionId
                        ? (this.state.ragCollections.find((c) => c.id === this.state.manageRagDocumentsCollectionId) || null)
                        : null
                    }
                  />
                </>
              )}
            </>
          )}
          
          {/* Bottom history panel removed; history is now in header */}
        </div>
      </div>
    );
  }
}

// Add version information for debugging and tracking
(BrainDriveChat as any).version = '1.0.26';

export default BrainDriveChat;
