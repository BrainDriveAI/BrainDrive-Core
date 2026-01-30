import { useState, useCallback } from 'react';
import { ConversationInfo, ChatMessage, Services } from '../types';
import { generateId } from '../utils';

export const useConversationHistory = (services?: Services) => {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [selectedConversation, setSelectedConversation] = useState<ConversationInfo | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);

  /**
   * Fetch conversations from the API
   */
  const fetchConversations = useCallback(async () => {
    if (!services?.api) {
      setIsLoadingHistory(false);
      return;
    }
    
    try {
      setIsLoadingHistory(true);
      
      // First, get the current user's information to get their ID
      const userResponse = await services.api.get('/api/v1/auth/me');
      
      // Extract the user ID from the response
      let userId = userResponse.id;
      
      if (!userId) {
        throw new Error('Could not get current user ID');
      }
      
      // Use the user ID as is - backend now handles IDs with or without dashes
      const response = await services.api.get(
        `/api/v1/users/${userId}/conversations`,
        {
          params: {
            skip: 0,
            limit: 50 // Fetch up to 50 conversations
          }
        }
      );
      
      let conversationList: ConversationInfo[] = [];
      
      if (Array.isArray(response)) {
        conversationList = response;
      } else if (response && response.data && Array.isArray(response.data)) {
        conversationList = response.data;
      } else if (response) {
        // Try to extract conversations from the response in a different way
        try {
          if (typeof response === 'object') {
            // Check if the response itself might be the conversations array
            if (response.id && response.user_id) {
              conversationList = [response as ConversationInfo];
            }
          }
        } catch (parseError) {
          // Error parsing response
        }
      }
      
      if (conversationList.length === 0) {
        // No conversations yet, but this is not an error
        setConversations([]);
        setIsLoadingHistory(false);
        return;
      }
      
      // Validate conversation objects
      const validConversations = conversationList.filter(conv => {
        return conv && typeof conv === 'object' && conv.id && conv.user_id;
      });
      
      // Sort conversations by most recently updated or created
      validConversations.sort((a, b) => {
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
      
      // Auto-select the most recent conversation if available
      const mostRecentConversation = validConversations.length > 0 ? validConversations[0] : null;
      
      setConversations(validConversations);
      setSelectedConversation(mostRecentConversation);
      setIsLoadingHistory(false);
      
      return { conversations: validConversations, mostRecent: mostRecentConversation };
    } catch (error: any) {
      // Check if it's a 403 Forbidden error
      if (error.status === 403 || (error.response && error.response.status === 403)) {
        // Show empty state for better user experience
        setIsLoadingHistory(false);
        setConversations([]);
      } else if (error.status === 404 || (error.response && error.response.status === 404)) {
        // Handle 404 errors (no conversations found)
        setIsLoadingHistory(false);
        setConversations([]);
      } else {
        // Handle other errors
        setIsLoadingHistory(false);
        console.error('Error loading conversations:', error);
      }
      return null;
    }
  }, [services?.api]);

  /**
   * Load conversation history from the API
   */
  const loadConversationHistory = useCallback(async (conversationIdToLoad: string): Promise<ChatMessage[]> => {
    if (!services?.api) {
      return [];
    }
    
    try {
      setIsLoadingHistory(true);
      
      // Fetch conversation with messages
      const response = await services.api.get(
        `/api/v1/conversations/${conversationIdToLoad}/with-messages`
      );
      
      // Process messages
      const messages: ChatMessage[] = [];
      
      if (response && response.messages && Array.isArray(response.messages)) {
        // Convert API message format to ChatMessage format
        messages.push(...response.messages.map((msg: any) => ({
          id: msg.id || generateId('history'),
          sender: msg.sender === 'llm' ? 'ai' : 'user' as 'ai' | 'user',
          content: msg.message,
          timestamp: msg.created_at
        })));
      }
      
      setConversationId(conversationIdToLoad);
      setIsLoadingHistory(false);
      
      return messages;
    } catch (error) {
      // Error loading conversation history
      setIsLoadingHistory(false);
      console.error('Error loading conversation history:', error);
      return [];
    }
  }, [services?.api]);

  /**
   * Handle conversation selection
   */
  const handleConversationSelect = useCallback((event: React.ChangeEvent<HTMLSelectElement>) => {
    const selectedConversationId = event.target.value;
    
    if (!selectedConversationId) {
      // New chat selected
      return { conversationId: null, conversation: null };
    }
    
    const conversation = conversations.find(
      conv => conv.id === selectedConversationId
    );
    
    if (conversation) {
      setSelectedConversation(conversation);
      return { conversationId: selectedConversationId, conversation };
    }
    
    return { conversationId: null, conversation: null };
  }, [conversations]);

  /**
   * Handle new chat
   */
  const handleNewChat = useCallback(() => {
    setSelectedConversation(null);
    setConversationId(null);
    return { conversationId: null, conversation: null };
  }, []);

  return {
    conversations,
    selectedConversation,
    isLoadingHistory,
    conversationId,
    fetchConversations,
    loadConversationHistory,
    handleConversationSelect,
    handleNewChat,
    setConversationId,
    setSelectedConversation
  };
};