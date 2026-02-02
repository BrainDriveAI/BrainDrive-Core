/**
 * ChatMessageHandler - Handles message processing, editing, and actions
 * Manages message lifecycle and user interactions
 */

import { ChatMessage } from '../types';
import { generateId } from '../utils';
import { MESSAGE_CONFIG, LOADING_MESSAGES } from '../constants';

export class ChatMessageHandler {
  private stateManager: any;
  private aiService: any;

  constructor(stateManager: any, aiService: any) {
    this.stateManager = stateManager;
    this.aiService = aiService;
  }

  /**
   * Clean up message content by removing excessive newlines and context
   */
  cleanMessageContent(content: string): string {
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
  }

  /**
   * Create a new message with validation
   */
  createMessage(
    sender: 'user' | 'ai',
    content: string,
    options: {
      isEditable?: boolean;
      isStreaming?: boolean;
      canRegenerate?: boolean;
      canContinue?: boolean;
      isSearchResults?: boolean;
      searchData?: any;
      isDocumentContext?: boolean;
      documentData?: any;
    } = {}
  ): ChatMessage {
    // Validate content length
    if (content.length > MESSAGE_CONFIG.MAX_CONTENT_LENGTH) {
      console.warn('Message content exceeds maximum length, truncating...');
      content = content.substring(0, MESSAGE_CONFIG.MAX_CONTENT_LENGTH) + '...';
    }

    return {
      id: generateId(sender),
      sender,
      content: this.cleanMessageContent(content),
      timestamp: new Date().toISOString(),
      ...options
    };
  }

  /**
   * Add a message to the chat
   */
  addMessage(message: ChatMessage): void {
    console.log(`💬 Adding message to chat: ${message.sender} - ${message.content.substring(0, 50)}...`);
    this.stateManager.addMessage(message);
  }

  /**
   * Create and add a user message
   */
  addUserMessage(content: string): ChatMessage {
    const message = this.createMessage('user', content, { isEditable: true });
    this.addMessage(message);
    return message;
  }

  /**
   * Create and add an AI message
   */
  addAIMessage(content: string, options: any = {}): ChatMessage {
    const message = this.createMessage('ai', content, options);
    this.addMessage(message);
    return message;
  }

  /**
   * Add a system/status message
   */
  addSystemMessage(content: string, messageType: 'success' | 'error' | 'info' = 'info'): ChatMessage {
    const prefixes = {
      success: '✅ ',
      error: '❌ ',
      info: 'ℹ️ '
    };
    
    const message = this.createMessage('ai', `${prefixes[messageType]}${content}`);
    this.addMessage(message);
    return message;
  }

  /**
   * Add a loading indicator message
   */
  addLoadingMessage(type: keyof typeof LOADING_MESSAGES): ChatMessage {
    const content = LOADING_MESSAGES[type];
    const message = this.createMessage('ai', content, { isStreaming: true });
    this.addMessage(message);
    return message;
  }

  /**
   * Remove a specific message by ID
   */
  removeMessage(messageId: string): void {
    const state = this.stateManager.getState();
    const updatedMessages = state.messages.filter((msg: ChatMessage) => msg.id !== messageId);
    this.stateManager.updateMessages(updatedMessages);
  }

  /**
   * Update streaming message content
   */
  updateStreamingMessage(messageId: string, content: string): void {
    this.stateManager.updateMessage(messageId, {
      content: this.cleanMessageContent(content)
    });
  }

  /**
   * Finalize streaming message
   */
  finalizeStreamingMessage(messageId: string, options: {
    canRegenerate?: boolean;
    canContinue?: boolean;
    isCutOff?: boolean;
  } = {}): void {
    this.stateManager.updateMessage(messageId, {
      isStreaming: false,
      canRegenerate: true,
      ...options
    });
  }

  /**
   * Start editing a message
   */
  startEditingMessage(messageId: string, content: string): void {
    this.stateManager.setState({
      editingMessageId: messageId,
      editingContent: content
    });
  }

  /**
   * Cancel editing a message
   */
  cancelEditingMessage(): void {
    this.stateManager.setState({
      editingMessageId: null,
      editingContent: ''
    });
  }

  /**
   * Save edited message
   */
  saveEditedMessage(messageId: string, newContent: string): Promise<void> {
    if (!newContent.trim()) {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      // Update the message content
      this.stateManager.updateMessage(messageId, {
        content: newContent.trim(),
        isEdited: true,
        originalContent: this.stateManager.getState().messages.find((m: ChatMessage) => m.id === messageId)?.content
      });

      // Clear editing state
      this.cancelEditingMessage();

      // Remove all messages after the edited message
      this.stateManager.removeMessagesAfter(messageId);

      resolve();
    });
  }

  /**
   * Toggle markdown view for a message
   */
  toggleMarkdownView(messageId: string): void {
    const state = this.stateManager.getState();
    const message = state.messages.find((m: ChatMessage) => m.id === messageId);
    
    if (message) {
      this.stateManager.updateMessage(messageId, {
        showRawMarkdown: !message.showRawMarkdown
      });
    }
  }

  /**
   * Regenerate the last AI response
   */
  async regenerateResponse(): Promise<string | null> {
    const state = this.stateManager.getState();
    const lastUserMessage = state.messages
      .filter((msg: ChatMessage) => msg.sender === 'user')
      .pop();
    
    if (lastUserMessage) {
      // Remove the last AI response (all messages after the last user message)
      this.stateManager.removeMessagesAfter(lastUserMessage.id);
      return lastUserMessage.content;
    }
    
    return null;
  }

  /**
   * Continue generation from where it left off
   */
  async continueGeneration(): Promise<boolean> {
    const state = this.stateManager.getState();
    const lastAiMessage = state.messages
      .filter((msg: ChatMessage) => msg.sender === 'ai')
      .pop();
    
    return !!(lastAiMessage && lastAiMessage.canContinue);
  }

  /**
   * Get conversation context for AI (last N messages)
   */
  getConversationContext(maxMessages: number = 10): ChatMessage[] {
    const state = this.stateManager.getState();
    return state.messages.slice(-maxMessages);
  }

  /**
   * Get message statistics
   */
  getMessageStats(): {
    total: number;
    userMessages: number;
    aiMessages: number;
    editedMessages: number;
    streamingMessages: number;
  } {
    const state = this.stateManager.getState();
    const messages = state.messages;

    return {
      total: messages.length,
      userMessages: messages.filter((m: ChatMessage) => m.sender === 'user').length,
      aiMessages: messages.filter((m: ChatMessage) => m.sender === 'ai').length,
      editedMessages: messages.filter((m: ChatMessage) => m.isEdited).length,
      streamingMessages: messages.filter((m: ChatMessage) => m.isStreaming).length
    };
  }

  /**
   * Export messages for sharing or backup
   */
  exportMessages(format: 'json' | 'markdown' = 'json'): string {
    const state = this.stateManager.getState();
    const messages = state.messages;

    if (format === 'markdown') {
      return messages
        .map((msg: ChatMessage) => {
          const timestamp = new Date(msg.timestamp).toLocaleString();
          const sender = msg.sender === 'user' ? 'User' : 'AI';
          return `## ${sender} (${timestamp})\n\n${msg.content}\n\n---\n`;
        })
        .join('\n');
    }

    return JSON.stringify(messages, null, 2);
  }

  /**
   * Search messages by content
   */
  searchMessages(query: string): ChatMessage[] {
    const state = this.stateManager.getState();
    const lowercaseQuery = query.toLowerCase();
    
    return state.messages.filter((msg: ChatMessage) =>
      msg.content.toLowerCase().includes(lowercaseQuery)
    );
  }
}
