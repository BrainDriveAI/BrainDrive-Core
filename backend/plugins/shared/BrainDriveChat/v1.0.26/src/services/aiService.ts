import { ModelInfo, Services, ChatMessage, PersonaInfo } from '../types';
import { extractTextFromData, generateId } from '../utils';

export class AIService {
  private services: Services;
  private currentUserId: string | null = null;

  constructor(services: Services) {
    this.services = services;
    this.initializeUserId();
  }

  /**
   * Initialize current user ID
   */
  private async initializeUserId() {
    try {
      if (this.services?.api) {
        const response = await this.services.api.get('/api/v1/auth/me');
        if (response && response.id) {
          this.currentUserId = response.id;
        }
      }
    } catch (error) {
      console.error('Error getting current user ID:', error);
    }
  }

  /**
   * Send prompt to AI provider and handle response
   */
  async sendPrompt(
    prompt: string,
    selectedModel: ModelInfo,
    useStreaming: boolean,
    conversationId: string | null,
    conversationType: string = "chat", // New parameter
    onChunk: (chunk: string) => void,
    onConversationId: (id: string) => void,
    pageContext?: any, // New parameter for page context
    selectedPersona?: PersonaInfo,  // Add persona parameter
    abortController?: AbortController, // Add abort controller for cancellation
    contextMessages: { role: string; content: string }[] = [],
    documentContextMode?: 'one-shot' | 'persist'
  ): Promise<boolean> {
    if (!this.services?.api) {
      throw new Error('API service not available');
    }

    // Create chat messages array with user's prompt
    const messages = [
      ...contextMessages,
      { role: "user", content: prompt }
    ];

    // Use only the production endpoint
    const endpoint = '/api/v1/ai/providers/chat';

    // Create request params for production endpoint
    const requestParams: any = {
      provider: selectedModel.provider || 'ollama',
      settings_id: selectedModel.providerId || 'ollama_servers_settings',
      server_id: selectedModel.serverId,
      model: selectedModel.name,
      messages: messages.map(msg => ({
        role: msg.role || 'user',
        content: msg.content
      })),
      params: {
        temperature: 0.7,
        max_tokens: 2048
      },
      stream: useStreaming,
      user_id: this.currentUserId || 'current',
      conversation_id: conversationId,
      conversation_type: conversationType // New field
    };
    
    console.log(`📤 AIService sending request with conversation_id: ${conversationId || 'null'}`);

    // Add page context if available
    if (pageContext) {
      requestParams.page_id = pageContext.pageId;
      requestParams.page_context = JSON.stringify({
        pageName: pageContext.pageName,
        pageRoute: pageContext.pageRoute,
        isStudioPage: pageContext.isStudioPage
      });
    }

    // Add persona data if available
    if (selectedPersona) {
      requestParams.persona_id = selectedPersona.id;
      requestParams.persona_system_prompt = selectedPersona.system_prompt;
      requestParams.persona_model_settings = selectedPersona.model_settings;
      requestParams.persona_sample_greeting = selectedPersona.sample_greeting;

      // Apply persona model settings to params if available
      if (selectedPersona.model_settings) {
        requestParams.params = {
          ...requestParams.params,
          ...selectedPersona.model_settings
        };
      }
    }

    // Carry document context mode so the backend can decide whether to persist system messages
    if (contextMessages.length > 0 && documentContextMode) {
      requestParams.params.document_context_mode = documentContextMode;
    }

    try {
      let success = false;

      if (useStreaming && typeof this.services?.api?.postStreaming === 'function') {
        // Handle streaming
        try {
          await this.services.api.postStreaming(
            endpoint,
            requestParams,
            (chunk: string) => {
              try {
                // Check if request was aborted
                if (abortController?.signal.aborted) {
                  return;
                }
                
                // Handle Server-Sent Events format - remove 'data: ' prefix if present
                let jsonString = chunk;
                if (chunk.startsWith('data: ')) {
                  jsonString = chunk.substring(6); // Remove 'data: ' prefix
                }
                
                // Skip empty chunks or [DONE] markers
                if (!jsonString.trim() || jsonString.trim() === '[DONE]') {
                  return;
                }

                const data = JSON.parse(jsonString);

                // Store the conversation_id if it's in the response
                if (data.conversation_id && !conversationId) {
                  onConversationId(data.conversation_id);
                }

                const chunkText = extractTextFromData(data);
                if (chunkText) {
                  onChunk(chunkText);
                }
              } catch (error) {
                console.error('Error processing streaming chunk:', error);
              }
            },
            {
              timeout: 120000,
              signal: abortController?.signal
            }
          );
          success = true;
        } catch (error) {
          console.error('Streaming error:', error);
          // Don't throw AbortError as it's expected when canceling
          if (error instanceof Error && error.name !== 'AbortError') {
            throw error;
          }
        }
      } else {
        // Handle non-streaming
        try {
          const response = await this.services.api.post(endpoint, requestParams, { timeout: 60000 });
          const responseData = response.data || response;

          // Store the conversation_id if it's in the response
          if (responseData.conversation_id && !conversationId) {
            onConversationId(responseData.conversation_id);
          }

          const responseText = extractTextFromData(responseData);
          if (responseText) {
            onChunk(responseText);
            success = true;
          } else {
            throw new Error('No response text received');
          }
        } catch (error) {
          console.error('Non-streaming error:', error);
          throw error;
        }
      }

      return success;
    } catch (error) {
      console.error('Error in sendPrompt:', error);
      onChunk(`Error: ${error instanceof Error ? error.message : 'Unknown error occurred'}`);
      return false;
    }
  }

  /**
   * Get current user ID
   */
  getCurrentUserId(): string | null {
    return this.currentUserId;
  }

  /**
   * Load conversation with persona details
   */
  async loadConversationWithPersona(conversationId: string): Promise<any> {
    if (!this.services?.api) {
      throw new Error('API service not available');
    }

    try {
      const response = await this.services.api.get(
        `/api/v1/conversations/${conversationId}/with-persona`
      );
      return response;
    } catch (error) {
      console.error('Error loading conversation with persona:', error);
      throw error;
    }
  }

  /**
   * Update conversation's persona
   */
  async updateConversationPersona(conversationId: string, personaId: string | null): Promise<void> {
    if (!this.services?.api) {
      throw new Error('API service not available');
    }

    try {
      await this.services.api.put(
        `/api/v1/conversations/${conversationId}/persona`,
        { persona_id: personaId }
      );
    } catch (error) {
      console.error('Error updating conversation persona:', error);
      throw error;
    }
  }

  async cancelGeneration(conversationId: string | null): Promise<void> {
    if (!this.services?.api || !conversationId) {
      return;
    }

    try {
      await this.services.api.post(
        `/api/v1/ai/providers/cancel`,
        { conversation_id: conversationId }
      );
    } catch (error) {
      console.error('Error canceling generation:', error);
    }
  }
}
