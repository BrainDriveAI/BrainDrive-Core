/**
 * ChatStateManager - Centralized state management for BrainDriveChat
 * Handles state updates, validation, and persistence
 */

import { BrainDriveChatState, ChatMessage, ModelInfo, PersonaInfo, ConversationWithPersona } from '../types';
import { SETTINGS_KEYS, UI_CONFIG } from '../constants';

export class ChatStateManager {
  private setState: (state: Partial<BrainDriveChatState>) => void;
  private getState: () => BrainDriveChatState;
  private settingsService: any;

  constructor(
    setState: (state: Partial<BrainDriveChatState>) => void,
    getState: () => BrainDriveChatState,
    settingsService?: any
  ) {
    this.setState = setState;
    this.getState = getState;
    this.settingsService = settingsService;
  }

  /**
   * Update messages in state with validation
   */
  updateMessages(messages: ChatMessage[]): void {
    // Validate messages before updating
    const validMessages = messages.filter(msg => 
      msg && msg.id && msg.sender && typeof msg.content === 'string'
    );

    this.setState({ messages: validMessages });
  }

  /**
   * Add a single message to the chat
   */
  addMessage(message: ChatMessage): void {
    const currentState = this.getState();
    const updatedMessages = [...currentState.messages, message];
    this.updateMessages(updatedMessages);
  }

  /**
   * Update a specific message by ID
   */
  updateMessage(messageId: string, updates: Partial<ChatMessage>): void {
    const currentState = this.getState();
    const updatedMessages = currentState.messages.map(msg =>
      msg.id === messageId ? { ...msg, ...updates } : msg
    );
    this.updateMessages(updatedMessages);
  }

  /**
   * Remove messages after a specific message (for regeneration)
   */
  removeMessagesAfter(messageId: string): void {
    const currentState = this.getState();
    const messageIndex = currentState.messages.findIndex(msg => msg.id === messageId);
    
    if (messageIndex >= 0) {
      const updatedMessages = currentState.messages.slice(0, messageIndex + 1);
      this.updateMessages(updatedMessages);
    }
  }

  /**
   * Update conversation state
   */
  updateConversation(conversationId: string | null, selectedConversation?: ConversationWithPersona | null): void {
    this.setState({
      conversation_id: conversationId,
      selectedConversation: selectedConversation || null
    });
  }

  /**
   * Update model selection
   */
  updateSelectedModel(model: ModelInfo | null): void {
    this.setState({ selectedModel: model });
  }

  /**
   * Update persona selection
   */
  updateSelectedPersona(persona: PersonaInfo | null): void {
    this.setState({ selectedPersona: persona });
  }

  /**
   * Update loading states
   */
  updateLoadingStates(updates: {
    isLoading?: boolean;
    isLoadingHistory?: boolean;
    isLoadingModels?: boolean;
    isLoadingPersonas?: boolean;
    isStreaming?: boolean;
    isSearching?: boolean;
    isProcessingDocuments?: boolean;
  }): void {
    this.setState(updates);
  }

  /**
   * Update UI states
   */
  updateUIStates(updates: {
    showModelSelection?: boolean;
    showConversationHistory?: boolean;
    showPersonaSelection?: boolean;
    useWebSearch?: boolean;
    useStreaming?: boolean;
    isHistoryExpanded?: boolean;
    showAllHistory?: boolean;
  }): void {
    this.setState(updates);
  }

  /**
   * Update error state
   */
  updateError(error: string): void {
    this.setState({ error });
  }

  /**
   * Clear error state
   */
  clearError(): void {
    this.setState({ error: '' });
  }

  /**
   * Reset to initial state for new chat
   */
  resetForNewChat(): void {
    const currentState = this.getState();
    this.setState({
      selectedConversation: null,
      conversation_id: null,
      messages: [],
      // Reset persona based on persona selection state
      selectedPersona: currentState.showPersonaSelection ? currentState.selectedPersona : null,
      error: '',
      isLoading: false,
      isStreaming: false,
      editingMessageId: null,
      editingContent: ''
    });
  }

  /**
   * Save streaming mode preference
   */
  async saveStreamingMode(enabled: boolean, pageContext?: any): Promise<void> {
    if (!this.settingsService?.setSetting) return;

    try {
      const settingKey = pageContext?.pageId 
        ? `page_${pageContext.pageId}_${SETTINGS_KEYS.STREAMING}`
        : SETTINGS_KEYS.STREAMING;
      
      await this.settingsService.setSetting(settingKey, enabled);
    } catch (error) {
      console.error('Error saving streaming mode:', error);
    }
  }

  /**
   * Load streaming mode preference
   */
  async loadStreamingMode(pageContext?: any): Promise<boolean | null> {
    if (!this.settingsService?.getSetting) return null;

    try {
      const settingKey = pageContext?.pageId 
        ? `page_${pageContext.pageId}_${SETTINGS_KEYS.STREAMING}`
        : SETTINGS_KEYS.STREAMING;
      
      let savedValue = await this.settingsService.getSetting(settingKey);
      
      // Fallback to global setting if page-specific doesn't exist
      if ((savedValue === null || savedValue === undefined) && pageContext?.pageId) {
        savedValue = await this.settingsService.getSetting(SETTINGS_KEYS.STREAMING);
      }
      
      return typeof savedValue === 'boolean' ? savedValue : null;
    } catch (error) {
      console.error('Error loading streaming mode:', error);
      return null;
    }
  }

  /**
   * Validate state consistency
   */
  validateState(): { isValid: boolean; errors: string[] } {
    const state = this.getState();
    const errors: string[] = [];

    // Validate messages
    if (!Array.isArray(state.messages)) {
      errors.push('Messages must be an array');
    } else {
      state.messages.forEach((msg, index) => {
        if (!msg.id) errors.push(`Message at index ${index} missing ID`);
        if (!msg.sender || !['user', 'ai'].includes(msg.sender)) {
          errors.push(`Message at index ${index} has invalid sender`);
        }
        if (typeof msg.content !== 'string') {
          errors.push(`Message at index ${index} has invalid content`);
        }
      });
    }

    // Validate selected model
    if (state.selectedModel && !state.selectedModel.name) {
      errors.push('Selected model missing name');
    }

    // Validate conversation consistency
    if (state.conversation_id && !state.selectedConversation) {
      errors.push('Conversation ID set but no selected conversation');
    }

    return {
      isValid: errors.length === 0,
      errors
    };
  }

  /**
   * Get state snapshot for debugging
   */
  getStateSnapshot(): {
    conversation_id: string | null;
    selectedModel: { name: string; provider: string } | null;
    selectedPersona: { id: string; name: string } | null;
    messagesCount: number;
    isLoading: boolean;
    isStreaming: boolean;
    error: string;
    useWebSearch: boolean;
    showPersonaSelection: boolean;
  } {
    const state = this.getState();
    return {
      conversation_id: state.conversation_id,
      selectedModel: state.selectedModel ? { name: state.selectedModel.name, provider: state.selectedModel.provider } : null,
      selectedPersona: state.selectedPersona ? { id: state.selectedPersona.id, name: state.selectedPersona.name } : null,
      messagesCount: state.messages.length,
      isLoading: state.isLoading,
      isStreaming: state.isStreaming,
      error: state.error,
      useWebSearch: state.useWebSearch,
      showPersonaSelection: state.showPersonaSelection
    };
  }
}
