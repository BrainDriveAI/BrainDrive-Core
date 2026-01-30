// Chat message types
export interface ChatMessage {
  id: string;
  sender: 'user' | 'ai';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
  // Search results
  isSearchResults?: boolean;
  searchData?: {
    query: string;
    results: SearchResult[];
    scrapedContent?: any;
    totalResults: number;
    successfulScrapes?: number;
  };
  // User control features
  isEditable?: boolean;
  isEdited?: boolean;
  originalContent?: string;
  canContinue?: boolean;
  canRegenerate?: boolean;
  isCutOff?: boolean;
  
  // Document context
  isDocumentContext?: boolean;
  documentData?: {
    results?: DocumentProcessingResult[];
    context: string;
    filename?: string;
    segmentCount?: number;
    totalChars?: number;
    truncated?: boolean;
    mode?: 'one-shot' | 'persist';
  };

  // Retrieved chunks context (RAG collection retrieval)
  isRetrievedContext?: boolean;
  retrievalData?: {
    collectionId?: string;
    collectionName?: string;
    chunks: RagDocumentChunk[];
    context: string;
    intent?: RagIntentResponse | null;
    metadata?: Record<string, any>;
  };
  // Markdown toggle
  showRawMarkdown?: boolean;
  
  // Web search context (for user messages)
  hasSearchContext?: boolean;
  searchContextData?: {
    originalPrompt: string;
    searchQuery: string;
    searchResults: SearchResult[];
    scrapedContent?: any;
    totalResults: number;
    successfulScrapes?: number;
  };
}

// Interface for web search
export interface SearchResult {
  title: string;
  url: string;
  content: string;
  engine?: string;
  score?: number;
}

// Model information
export interface ModelInfo {
  name: string;
  provider: string;
  providerId: string;
  serverName: string;
  serverId: string;
  isTemporary?: boolean;
}

// Persona information
export interface PersonaInfo {
  id: string;
  name: string;
  description?: string;
  system_prompt: string;
  model_settings?: {
    temperature?: number;
    top_p?: number;
    max_tokens?: number;
    // Additional model settings can be added here
  };
  avatar?: string;
  tags?: string[];
  sample_greeting?: string;
}

// Conversation information
export interface ConversationInfo {
  id: string;
  title?: string;
  user_id: string;
  model?: string;
  server?: string;
  persona_id?: string;
  conversation_type: string;
  created_at: string;
  updated_at?: string;
}

// Conversation with full persona details
export interface ConversationWithPersona extends ConversationInfo {
  persona?: PersonaInfo;
}

// Dropdown option for conversations
export interface ConversationDropdownOption {
  id: string;
  primaryText: string;
  secondaryText: string;
  metadata?: {
    model?: string;
    server?: string;
    created_at: string;
    updated_at?: string;
  };
}

// API Response interface
export interface ApiResponse {
  data?: any;
  status?: number;
  id?: string;
  [key: string]: any;
}

// Document processing interfaces
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

export interface DocumentContextSegment {
  index: number;
  text: string;
  char_count: number;
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

// =========================
// RAG (Collections) Types
// =========================

export interface RagCollection {
  id: string;
  name: string;
  description: string;
  color: string;
  created_at: string;
  updated_at: string;
  document_count: number;
  chat_session_count?: number;
}

export interface RagCreateCollectionInput {
  name: string;
  description?: string;
  color?: string;
}

export type RagDocumentStatus = 'uploaded' | 'processing' | 'processed' | 'failed' | string;

export interface RagDocument {
  id: string;
  original_filename: string;
  file_size: number;
  document_type: string;
  collection_id: string;
  status: RagDocumentStatus;
  created_at: string;
  processed_at: string;
  error_message?: string;
  metadata?: Record<string, any>;
  chunk_count: number;
}

export interface RagDocumentChunk {
  id: string;
  document_id: string;
  collection_id: string;
  content: string;
  chunk_index: number;
  chunk_type: string;
  parent_chunk_id?: string;
  metadata: Record<string, any>;
  embedding_vector?: number[];
}

export interface RagIntentResponse {
  type: string;
  requires_retrieval: boolean;
  requires_collection_scan: boolean;
  confidence: number;
  reasoning: string;
}

export interface RagContextRetrievalResult {
  chunks: RagDocumentChunk[];
  intent: RagIntentResponse | null;
  requires_generation: boolean;
  generation_type: string;
  metadata: Record<string, any>;
}
// Library types
export interface LibraryProject {
  name: string;
  slug: string;
  lifecycle: string;
  path: string;
  has_agent_md: boolean;
  has_spec: boolean;
  has_build_plan: boolean;
  has_decisions: boolean;
}

export interface LibraryScope {
  enabled: boolean;
  project: LibraryProject | null; // null = "All"
}

// Service interfaces
export interface ApiService {
  get: (url: string, options?: any) => Promise<ApiResponse>;
  post: (url: string, data: any, options?: any) => Promise<ApiResponse>;
  put: (url: string, data: any, options?: any) => Promise<ApiResponse>;
  delete: (url: string, options?: any) => Promise<ApiResponse>;
  postStreaming?: (url: string, data: any, onChunk: (chunk: string) => void, options?: any) => Promise<ApiResponse>;
}

export interface EventService {
  sendMessage: (target: string, message: any, options?: any) => void;
  subscribeToMessages: (target: string, callback: (message: any) => void) => void;
  unsubscribeFromMessages: (target: string, callback: (message: any) => void) => void;
}

export interface ThemeService {
  getCurrentTheme: () => string;
  addThemeChangeListener: (callback: (theme: string) => void) => void;
  removeThemeChangeListener: (callback: (theme: string) => void) => void;
}

export interface SettingsService {
  get: (key: string) => any;
  set: (key: string, value: any) => Promise<void>;
  getSetting?: (id: string) => Promise<any>;
  setSetting?: (id: string, value: any) => Promise<any>;
  getSettingDefinitions?: () => Promise<any>;
}

// Page context service interface
export interface PageContextService {
  getCurrentPageContext(): {
    pageId: string;
    pageName: string;
    pageRoute: string;
    isStudioPage: boolean;
  } | null;
  onPageContextChange(callback: (context: any) => void): () => void;
}

export interface Services {
  api?: ApiService;
  event?: EventService;
  theme?: ThemeService;
  settings?: SettingsService;
  pageContext?: PageContextService;
}

// Component props
export interface BrainDriveChatProps {
  moduleId?: string;
  services: Services;
  initialGreeting?: string;
  defaultStreamingMode?: boolean;
  promptQuestion?: string;
  conversationType?: string; // Allow plugins to specify their type
  // Persona-related props
  availablePersonas?: PersonaInfo[];  // Developer-defined personas
  showPersonaSelection?: boolean;     // Control visibility
  defaultPersona?: PersonaInfo;       // Default persona to use
}

// Component state
export interface BrainDriveChatState {
  messages: ChatMessage[];
  inputText: string;
  isLoading: boolean;
  error: string;
  currentTheme: string;
  selectedModel: ModelInfo | null;
  pendingModelKey: string | null;
  pendingModelSnapshot: ModelInfo | null;
  useStreaming: boolean;
  conversation_id: string | null;
  isLoadingHistory: boolean;
  currentUserId: string | null;
  isInitializing: boolean;
  conversations: ConversationInfo[];
  selectedConversation: ConversationInfo | null;
  isUpdating: boolean;
  models: ModelInfo[];
  isLoadingModels: boolean;
  showModelSelection: boolean;
  showConversationHistory: boolean;
  // Persona-related state
  personas: PersonaInfo[];
  selectedPersona: PersonaInfo | null;
  pendingPersonaId: string | null;
  isLoadingPersonas: boolean;
  showPersonaSelection: boolean;
  // Web search state
  useWebSearch: boolean;
  isSearching: boolean;
  // User control state
  isStreaming: boolean;
  editingMessageId: string | null;
  editingContent: string;
  
  // Document processing state
  documentContext: string;
  documentContextMode: 'one-shot' | 'persist' | null;
  documentContextInjectedConversationId: string | null;
  documentContextInfo?: {
    filename: string;
    segmentCount: number;
    totalChars: number;
    truncated?: boolean;
    mode?: 'one-shot' | 'persist';
  } | null;
  isProcessingDocuments: boolean;

  // RAG (collections) state
  ragEnabled: boolean;
  ragCollections: RagCollection[];
  ragCollectionsLoading: boolean;
  ragCollectionsError: string | null;
  selectedRagCollectionId: string | null;
  isCreateRagCollectionModalOpen: boolean;
  isManageRagDocumentsModalOpen: boolean;
  manageRagDocumentsCollectionId: string | null;
  
  // Library state
  libraryScope: LibraryScope;

  // Scroll state
  isNearBottom: boolean;
  showScrollToBottom: boolean;
  isAutoScrollLocked: boolean;
  
  // History UI state
  showAllHistory: boolean;
  openConversationMenu: string | null;
  isHistoryExpanded: boolean;
}

// Provider settings
export interface ServerInfo {
  id: string;
  serverName: string;
  serverAddress: string;
  apiKey?: string;
}

export interface ProviderSettings {
  id: string;
  name: string;
  servers: ServerInfo[];
}
