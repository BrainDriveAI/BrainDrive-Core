/**
 * Constants for BrainDriveChat component
 * Centralized configuration values to improve maintainability
 */

// Settings and storage keys
export const SETTINGS_KEYS = {
  STREAMING: 'ai_prompt_chat_streaming_enabled',
} as const;

// UI Configuration
export const UI_CONFIG = {
  MAX_HISTORY_ITEMS: 50,
  SCROLL_THRESHOLD: 100,
  MAX_TEXTAREA_HEIGHT: 150,
  MIN_TEXTAREA_HEIGHT: 64,
  CHAT_HISTORY_MIN_HEIGHT: 200,
  CHAT_HISTORY_MAX_HEIGHT: 800,
  INITIAL_GREETING_DELAY: 2000,
  CONVERSATION_REFRESH_DELAY: 1000,
  INPUT_FOCUS_DELAY: 100,
  SCROLL_DEBOUNCE_DELAY: 100,
} as const;

// Search Configuration
export const SEARCH_CONFIG = {
  MAX_RESULTS_TO_SHOW: 5,
  MAX_RESULTS_TO_SCRAPE: 3,
  MAX_SCRAPE_CONTENT_LENGTH: 3000,
  SEARCH_RESULT_SNIPPET_LENGTH: 200,
} as const;

// File Upload Configuration
export const FILE_CONFIG = {
  ACCEPTED_EXTENSIONS: '.pdf,.txt,.log,.csv,.json,.xlsx,.xls,.ods,.md,.markdown,.xml,.html,.htm,.xhtml,.docx,.doc,.pptx,.ppt,.rtf,.eml,.epub',
  MAX_FILE_SIZE_MB: 10,
} as const;

// Message Configuration
export const MESSAGE_CONFIG = {
  MAX_CONTENT_LENGTH: 10000,
  TYPING_INDICATOR_DELAY: 100,
} as const;

// API Configuration
export const API_CONFIG = {
  DEFAULT_CONVERSATION_TYPE: 'chat',
  DEFAULT_PROVIDER: 'ollama',
  DEFAULT_PROVIDER_ID: 'ollama_servers_settings',
  MAX_CONVERSATION_FETCH_LIMIT: 50,
} as const;

// Provider -> settings_id mapping used when calling chat endpoints
export const PROVIDER_SETTINGS_ID_MAP: Record<string, string> = {
  ollama: 'ollama_servers_settings',
  openai: 'openai_api_keys_settings',
  openrouter: 'openrouter_api_keys_settings',
  claude: 'claude_api_keys_settings',
  groq: 'groq_api_keys_settings',
};

// Theme Configuration
export const THEME_CONFIG = {
  DEFAULT_THEME: 'light',
  SUPPORTED_THEMES: ['light', 'dark'] as const,
} as const;

// Error Messages
export const ERROR_MESSAGES = {
  API_UNAVAILABLE: 'API service not available',
  MODEL_NOT_SELECTED: 'Please select a model first',
  USER_ID_NOT_FOUND: 'Could not get current user ID',
  CONVERSATION_LOAD_FAILED: 'Error loading conversation history',
  DOCUMENT_SERVICE_UNAVAILABLE: 'Document service not available',
  SEARCH_SERVICE_UNAVAILABLE: 'Search service not available',
} as const;

// Success Messages
export const SUCCESS_MESSAGES = {
  CONVERSATION_LINK_COPIED: '📋 Conversation link copied to clipboard!',
  WEB_SEARCH_ENABLED: '🔍 Web search enabled - I can now search the web to help answer your questions',
  WEB_SEARCH_DISABLED: '🔍 Web search disabled',
} as const;

// Loading Messages
export const LOADING_MESSAGES = {
  SEARCHING_WEB: '🔍 Searching the web...',
  PROCESSING_DOCUMENTS: '📄 Processing documents...',
  LOADING_CONVERSATION: '💬 Loading conversation...',
} as const;
