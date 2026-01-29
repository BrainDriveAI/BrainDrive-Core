import { useState, useCallback } from 'react';
import { ChatMessage } from '../types';
import { generateId } from '../utils';

export const useChatMessages = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  /**
   * Add a new message to the chat history
   */
  const addMessage = useCallback((message: ChatMessage) => {
    setMessages(prevMessages => [...prevMessages, message]);
  }, []);

  /**
   * Clear all messages
   */
  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  /**
   * Create a placeholder for AI response
   */
  const createAIResponsePlaceholder = useCallback(() => {
    const placeholderId = generateId('ai');
    
    const placeholderMessage: ChatMessage = {
      id: placeholderId,
      sender: 'ai',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true
    };
    
    addMessage(placeholderMessage);
    return placeholderId;
  }, [addMessage]);

  /**
   * Update a streaming message with new content
   */
  const updateStreamingMessage = useCallback((messageId: string, newContent: string) => {
    setMessages(prevMessages => {
      return prevMessages.map(message => {
        if (message.id === messageId) {
          return {
            ...message,
            content: message.content + newContent
          };
        }
        return message;
      });
    });
  }, []);

  /**
   * Finalize a streaming message (mark as no longer streaming)
   */
  const finalizeStreamingMessage = useCallback((messageId: string) => {
    setMessages(prevMessages => {
      return prevMessages.map(message => {
        if (message.id === messageId) {
          return {
            ...message,
            isStreaming: false
          };
        }
        return message;
      });
    });
  }, []);

  return {
    messages,
    addMessage,
    clearMessages,
    createAIResponsePlaceholder,
    updateStreamingMessage,
    finalizeStreamingMessage
  };
};