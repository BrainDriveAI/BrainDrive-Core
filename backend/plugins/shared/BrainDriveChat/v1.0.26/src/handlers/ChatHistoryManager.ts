/**
 * ChatHistoryManager - Manages conversation history and operations
 * Handles loading, creating, updating, and deleting conversations
 */

import { ConversationWithPersona, PersonaInfo, ModelInfo } from '../types';
import { AIService } from '../services';
import { API_CONFIG, ERROR_MESSAGES } from '../constants';

export class ChatHistoryManager {
  private apiService: any;
  private aiService: AIService | null;
  private stateManager: any;
  private messageHandler: any;

  constructor(
    apiService: any,
    aiService: AIService | null,
    stateManager: any,
    messageHandler: any
  ) {
    this.apiService = apiService;
    this.aiService = aiService;
    this.stateManager = stateManager;
    this.messageHandler = messageHandler;
  }

  /**
   * Fetch conversations from the API
   */
  async fetchConversations(pageContext?: any): Promise<void> {
    if (!this.apiService) {
      this.stateManager.updateError(ERROR_MESSAGES.API_UNAVAILABLE);
      return;
    }

    try {
      this.stateManager.updateLoadingStates({ isLoadingHistory: true });
      this.stateManager.clearError();
      
      // Get the current user's information
      const userResponse = await this.apiService.get('/api/v1/auth/me');
      const userId = userResponse.id;
      
      if (!userId) {
        throw new Error(ERROR_MESSAGES.USER_ID_NOT_FOUND);
      }
      
      // Prepare request parameters
      const params: any = {
        skip: 0,
        limit: API_CONFIG.MAX_CONVERSATION_FETCH_LIMIT,
        conversation_type: API_CONFIG.DEFAULT_CONVERSATION_TYPE
      };
      
      // Add page_id if available for page-specific conversations
      if (pageContext?.pageId) {
        params.page_id = pageContext.pageId;
      }
      
      // Fetch conversations
      const response = await this.apiService.get(
        `/api/v1/users/${userId}/conversations`,
        { params }
      );
      
      // Process response
      let conversations = this.processConversationsResponse(response);
      
      // Sort conversations by most recently updated or created
      conversations = this.sortConversations(conversations);
      
      // Auto-select the most recent conversation if available
      const mostRecentConversation = conversations.length > 0 ? conversations[0] : null;
      
      this.stateManager.setState({
        conversations,
        selectedConversation: mostRecentConversation,
        isLoadingHistory: false
      });
      
      // Only auto-load the most recent conversation if we don't have an active conversation
      const currentState = this.stateManager.getState();
      if (mostRecentConversation && !currentState.conversation_id) {
        await this.loadConversationWithPersona(mostRecentConversation.id);
      }
      
    } catch (error: any) {
      this.handleFetchError(error);
    }
  }

  /**
   * Process conversations response from API
   */
  private processConversationsResponse(response: any): ConversationWithPersona[] {
    let conversations = [];
    
    if (Array.isArray(response)) {
      conversations = response;
    } else if (response && response.data && Array.isArray(response.data)) {
      conversations = response.data;
    } else if (response && typeof response === 'object') {
      if (response.id && response.user_id) {
        conversations = [response];
      }
    }
    
    // Validate conversation objects
    return conversations.filter((conv: any) => {
      return conv && typeof conv === 'object' && conv.id && conv.user_id;
    });
  }

  /**
   * Sort conversations by most recently updated or created
   */
  private sortConversations(conversations: ConversationWithPersona[]): ConversationWithPersona[] {
    return conversations.sort((a: any, b: any) => {
      // Use updated_at if available for both conversations
      if (a.updated_at && b.updated_at) {
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      }
      
      // If only one has updated_at, prioritize that one
      if (a.updated_at && !b.updated_at) {
        return -1; // a comes first
      }
      
      if (!a.updated_at && b.updated_at) {
        return 1; // b comes first
      }
      
      // If neither has updated_at, fall back to created_at
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  }

  /**
   * Handle fetch errors with appropriate user feedback
   */
  private handleFetchError(error: any): void {
    this.stateManager.updateLoadingStates({ isLoadingHistory: false });
    
    // Check for specific error types
    if (error.status === 403 || (error.response && error.response.status === 403)) {
      // Show empty state for better user experience
      this.stateManager.setState({
        conversations: [],
        error: '' // Don't show an error message to the user
      });
    } else if (error.status === 404 || (error.response && error.response.status === 404)) {
      // Handle 404 errors (no conversations found)
      this.stateManager.setState({
        conversations: [],
        error: '' // Don't show an error message to the user
      });
    } else {
      // Handle other errors
      this.stateManager.updateError(`Error loading conversations: ${error.message || 'Unknown error'}`);
    }
  }

  /**
   * Load conversation history with persona and model auto-selection
   */
  async loadConversationWithPersona(conversationId: string): Promise<void> {
    console.log(`🔄 Loading conversation with persona: ${conversationId}`);
    
    if (!this.apiService || !this.aiService) {
      this.stateManager.updateError(ERROR_MESSAGES.API_UNAVAILABLE);
      return;
    }
    
    try {
      // Clear current conversation without showing initial greeting
      this.stateManager.setState({
        messages: [],
        conversation_id: null,
        isLoadingHistory: true,
        error: ''
      });
      
      // Get the selected conversation from state to access model/server info
      const state = this.stateManager.getState();
      const selectedConversation = state.selectedConversation;
      
      // Try to fetch conversation with persona details first
      let conversationWithPersona: ConversationWithPersona | null = null;
      try {
        conversationWithPersona = await this.aiService.loadConversationWithPersona(conversationId);
      } catch (error) {
        // If the new endpoint doesn't exist yet, fall back to regular conversation loading
        console.warn('Persona-aware conversation loading not available, falling back to regular loading');
        conversationWithPersona = selectedConversation;
      }
      
      // Restore persona selection only if personas are enabled
      this.restorePersonaSelection(conversationWithPersona);
      
      // Restore model selection from conversation data
      this.restoreModelSelection(conversationWithPersona);
      
      // Now load the conversation messages using the regular method
      await this.loadConversationHistory(conversationId);
      
      console.log(`✅ Conversation loaded successfully: ${conversationId}`);
      
    } catch (error) {
      console.error('Error loading conversation with persona:', error);
      // Fall back to regular conversation loading
      await this.loadConversationHistory(conversationId);
    }
  }

  /**
   * Restore persona selection from conversation data
   */
  private restorePersonaSelection(conversationWithPersona: ConversationWithPersona | null): void {
    const state = this.stateManager.getState();
    
    if (state.showPersonaSelection && conversationWithPersona?.persona) {
      const persona = conversationWithPersona.persona;
      // Check if this persona exists in our current personas list
      const existingPersona = state.personas.find((p: PersonaInfo) => p.id === persona.id);
      if (existingPersona) {
        this.stateManager.updateSelectedPersona(existingPersona);
      } else {
        // Add the persona to our list if it's not there
        this.stateManager.setState({
          personas: [...state.personas, persona],
          selectedPersona: persona
        });
      }
    } else if (state.showPersonaSelection && conversationWithPersona?.persona_id) {
      // If we have a persona_id but no full persona data, try to find it in our list
      const existingPersona = state.personas.find((p: PersonaInfo) => p.id === conversationWithPersona.persona_id);
      if (existingPersona) {
        this.stateManager.updateSelectedPersona(existingPersona);
      }
    } else {
      // Ensure persona is reset to null when personas are disabled
      this.stateManager.updateSelectedPersona(null);
    }
  }

  /**
   * Restore model selection from conversation data
   */
  private restoreModelSelection(conversationWithPersona: ConversationWithPersona | null): void {
    if (!conversationWithPersona?.model || !conversationWithPersona?.server) {
      return;
    }
    
    const state = this.stateManager.getState();
    
    // Find the matching model in our models list
    const matchingModel = state.models.find((model: ModelInfo) =>
      model.name === conversationWithPersona.model &&
      model.serverName === conversationWithPersona.server
    );
    
    if (matchingModel) {
      this.stateManager.updateSelectedModel(matchingModel);
    } else {
      // If we can't find the exact model, create a temporary model object
      const tempModel: ModelInfo = {
        name: conversationWithPersona.model,
        provider: API_CONFIG.DEFAULT_PROVIDER,
        providerId: API_CONFIG.DEFAULT_PROVIDER_ID,
        serverName: conversationWithPersona.server,
        serverId: 'unknown' // We don't have the server ID from conversation data
      };
      this.stateManager.updateSelectedModel(tempModel);
    }
  }

  /**
   * Load conversation history from the API
   */
  async loadConversationHistory(conversationId: string): Promise<void> {
    console.log(`📚 Loading conversation history: ${conversationId}`);
    
    if (!this.apiService) {
      this.stateManager.updateError(ERROR_MESSAGES.API_UNAVAILABLE);
      return;
    }
    
    try {
      // Fetch conversation with messages
      const response = await this.apiService.get(
        `/api/v1/conversations/${conversationId}/with-messages`
      );
      
      // Process messages
      const messages = this.processConversationMessages(response);
      
      // Update state
      this.stateManager.setState({
        messages,
        conversation_id: conversationId,
        isLoadingHistory: false,
        isInitializing: false
      });
      
      console.log(`✅ Conversation history loaded: ${conversationId}, ${messages.length} messages`);
      
    } catch (error) {
      this.stateManager.setState({
        isLoadingHistory: false,
        error: ERROR_MESSAGES.CONVERSATION_LOAD_FAILED,
        isInitializing: false
      });
    }
  }

  /**
   * Process conversation messages from API response
   */
  private processConversationMessages(response: any): any[] {
    const messages: any[] = [];
    
    if (response && response.messages && Array.isArray(response.messages)) {
      // Convert API message format to ChatMessage format
      messages.push(...response.messages.map((msg: any) => ({
        id: msg.id || this.messageHandler.generateId('history'),
        sender: msg.sender === 'llm' ? 'ai' : 'user',
        content: this.messageHandler.cleanMessageContent(msg.message),
        timestamp: msg.created_at
      })));
    }
    
    return messages;
  }

  /**
   * Refresh conversations list without interfering with current conversation
   */
  async refreshConversationsList(pageContext?: any): Promise<void> {
    if (!this.apiService) return;
    
    try {
      const userResponse = await this.apiService.get('/api/v1/auth/me');
      const userId = userResponse.id;
      
      if (!userId) return;
      
      const params: any = {
        skip: 0,
        limit: API_CONFIG.MAX_CONVERSATION_FETCH_LIMIT,
        conversation_type: API_CONFIG.DEFAULT_CONVERSATION_TYPE
      };
      
      if (pageContext?.pageId) {
        params.page_id = pageContext.pageId;
      }
      
      const response = await this.apiService.get(
        `/api/v1/users/${userId}/conversations`,
        { params }
      );
      
      const conversations = this.processConversationsResponse(response);
      const sortedConversations = this.sortConversations(conversations);
      
      // Update conversations list and select current conversation if it exists
      const state = this.stateManager.getState();
      const currentConversation = state.conversation_id 
        ? sortedConversations.find(conv => conv.id === state.conversation_id)
        : null;
      
      this.stateManager.setState({
        conversations: sortedConversations,
        selectedConversation: currentConversation || state.selectedConversation
      });
      
    } catch (error: any) {
      console.error('Error refreshing conversations list:', error);
    }
  }

  /**
   * Rename a conversation
   */
  async renameConversation(conversationId: string, newTitle: string): Promise<void> {
    if (!this.apiService) {
      throw new Error(ERROR_MESSAGES.API_UNAVAILABLE);
    }

    try {
      await this.apiService.put(
        `/api/v1/conversations/${conversationId}`,
        { title: newTitle }
      );

      // Update the conversation in state
      const state = this.stateManager.getState();
      const updatedConversations = state.conversations.map((conv: ConversationWithPersona) =>
        conv.id === conversationId
          ? { ...conv, title: newTitle }
          : conv
      );

      const updatedSelectedConversation = state.selectedConversation?.id === conversationId
        ? { ...state.selectedConversation, title: newTitle }
        : state.selectedConversation;

      this.stateManager.setState({
        conversations: updatedConversations,
        selectedConversation: updatedSelectedConversation
      });

    } catch (error: any) {
      throw new Error(`Error renaming conversation: ${error.message || 'Unknown error'}`);
    }
  }

  /**
   * Delete a conversation
   */
  async deleteConversation(conversationId: string): Promise<void> {
    if (!this.apiService) {
      throw new Error(ERROR_MESSAGES.API_UNAVAILABLE);
    }

    try {
      await this.apiService.delete(`/api/v1/conversations/${conversationId}`);

      // Update state to remove the conversation
      const state = this.stateManager.getState();
      const updatedConversations = state.conversations.filter(
        (conv: ConversationWithPersona) => conv.id !== conversationId
      );

      // If the deleted conversation was selected, clear selection and start new chat
      const wasSelected = state.selectedConversation?.id === conversationId;

      this.stateManager.setState({
        conversations: updatedConversations,
        selectedConversation: wasSelected ? null : state.selectedConversation,
        conversation_id: wasSelected ? null : state.conversation_id,
        messages: wasSelected ? [] : state.messages,
        // Reset persona to null when starting new chat (respects persona toggle state)
        selectedPersona: wasSelected ? (state.showPersonaSelection ? state.selectedPersona : null) : state.selectedPersona
      });

    } catch (error: any) {
      throw new Error(`Error deleting conversation: ${error.message || 'Unknown error'}`);
    }
  }

  /**
   * Update conversation's persona
   */
  async updateConversationPersona(conversationId: string, personaId: string | null): Promise<void> {
    if (!this.aiService) {
      throw new Error(ERROR_MESSAGES.API_UNAVAILABLE);
    }

    try {
      await this.aiService.updateConversationPersona(conversationId, personaId);
    } catch (error) {
      console.error('Error updating conversation persona:', error);
      throw error;
    }
  }

  /**
   * Get conversation statistics
   */
  getConversationStats(): {
    totalConversations: number;
    conversationsWithPersonas: number;
    averageMessagesPerConversation: number;
  } {
    const state = this.stateManager.getState();
    const conversations = state.conversations || [];
    
    const totalConversations = conversations.length;
    const conversationsWithPersonas = conversations.filter(
      (conv: ConversationWithPersona) => conv.persona || conv.persona_id
    ).length;
    
    // This is a rough estimate since we don't have message counts for all conversations
    const currentMessages = state.messages?.length || 0;
    const averageMessagesPerConversation = totalConversations > 0 
      ? Math.round(currentMessages / Math.max(1, totalConversations))
      : 0;

    return {
      totalConversations,
      conversationsWithPersonas,
      averageMessagesPerConversation
    };
  }
}
