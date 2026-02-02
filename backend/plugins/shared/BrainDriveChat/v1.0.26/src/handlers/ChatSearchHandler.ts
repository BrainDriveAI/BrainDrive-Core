/**
 * ChatSearchHandler - Manages web search functionality
 * Handles search requests, result processing, and integration with AI responses
 */

import { SearchService } from '../services';
import { ChatMessage } from '../types';
import { generateId } from '../utils';
import { SEARCH_CONFIG, SUCCESS_MESSAGES, ERROR_MESSAGES } from '../constants';

export class ChatSearchHandler {
  private searchService: SearchService | null;
  private stateManager: any;
  private messageHandler: any;

  constructor(
    searchService: SearchService | null,
    stateManager: any,
    messageHandler: any
  ) {
    this.searchService = searchService;
    this.stateManager = stateManager;
    this.messageHandler = messageHandler;
  }

  /**
   * Toggle web search mode and test connection
   */
  async toggleWebSearchMode(): Promise<void> {
    const currentState = this.stateManager.getState();
    const newWebSearchMode = !currentState.useWebSearch;
    
    this.stateManager.updateUIStates({ useWebSearch: newWebSearchMode });
    
    // Test connection when enabling web search
    if (newWebSearchMode && this.searchService) {
      try {
        const healthCheck = await this.searchService.checkHealth();
        
        if (!healthCheck.accessible) {
          this.messageHandler.addSystemMessage(
            `Web search enabled but the search service is not accessible. ${healthCheck.error || 'Please ensure SearXNG is running and the backend is connected.'}`,
            'error'
          );
        } else {
          this.messageHandler.addSystemMessage(SUCCESS_MESSAGES.WEB_SEARCH_ENABLED, 'success');
        }
      } catch (error) {
        this.messageHandler.addSystemMessage(
          'Web search enabled but there was an error connecting to the search service',
          'error'
        );
      }
    } else {
      const message = newWebSearchMode 
        ? SUCCESS_MESSAGES.WEB_SEARCH_ENABLED 
        : SUCCESS_MESSAGES.WEB_SEARCH_DISABLED;
      this.messageHandler.addSystemMessage(message, 'info');
    }
  }

  /**
   * Perform web search with scraping
   */
  async performSearch(query: string): Promise<{
    searchResponse: any;
    scrapedContent: any;
    searchMessage: ChatMessage | null;
  }> {
    if (!this.searchService) {
      throw new Error(ERROR_MESSAGES.SEARCH_SERVICE_UNAVAILABLE);
    }

    this.stateManager.updateLoadingStates({ isSearching: true });
    
    // Add search indicator message
    const searchIndicatorId = generateId('search-indicator');
    this.messageHandler.addLoadingMessage('SEARCHING_WEB');

    try {
      // Perform enhanced search with web scraping
      const { searchResponse, scrapedContent } = await this.searchService.searchWithScraping(
        query,
        { 
          category: 'general',
          language: 'en'
        },
        SEARCH_CONFIG.MAX_RESULTS_TO_SCRAPE,
        SEARCH_CONFIG.MAX_SCRAPE_CONTENT_LENGTH
      );
      
      // Remove the search indicator
      this.messageHandler.removeMessage(searchIndicatorId);
      
      let searchMessage: ChatMessage | null = null;

      if (searchResponse.results.length > 0) {
        // Create a search results message with collapsible content
        searchMessage = this.messageHandler.createMessage('ai', '', {
          isSearchResults: true,
          searchData: {
            query: searchResponse.query,
            results: searchResponse.results.slice(0, SEARCH_CONFIG.MAX_RESULTS_TO_SHOW),
            scrapedContent: scrapedContent,
            totalResults: searchResponse.results.length,
            successfulScrapes: scrapedContent.summary.successful_scrapes
          }
        });
        
        this.messageHandler.addMessage(searchMessage);
      } else {
        // Add a message for no results
        this.messageHandler.addSystemMessage(
          'No web search results found for your query. I will answer based on my knowledge.',
          'info'
        );
      }
      
      this.stateManager.updateLoadingStates({ isSearching: false });
      
      return { searchResponse, scrapedContent, searchMessage };
      
    } catch (searchError) {
      console.error('Web search error:', searchError);
      this.stateManager.updateLoadingStates({ isSearching: false });
      
      // Remove search indicator if it exists
      this.messageHandler.removeMessage(searchIndicatorId);
      
      // Add error message
      const errorMessage = searchError instanceof Error ? searchError.message : 'Unknown error';
      this.messageHandler.addSystemMessage(
        `Web search failed: ${errorMessage}. I'll answer based on my knowledge.`,
        'error'
      );
      
      throw searchError;
    }
  }

  /**
   * Build comprehensive search context to inject into user prompt
   */
  buildSearchContextForPrompt(searchResponse: any, scrapedContent: any): string {
    let context = `Search Results for "${searchResponse.query}":\n\n`;
    
    // Add basic search results
    if (searchResponse.results && searchResponse.results.length > 0) {
      searchResponse.results.slice(0, SEARCH_CONFIG.MAX_RESULTS_TO_SHOW).forEach((result: any, index: number) => {
        context += `${index + 1}. ${result.title}\n`;
        context += `   URL: ${result.url}\n`;
        if (result.content) {
          const cleanContent = result.content
            .replace(/\s+/g, ' ')
            .trim()
            .substring(0, SEARCH_CONFIG.SEARCH_RESULT_SNIPPET_LENGTH);
          context += `   Summary: ${cleanContent}${result.content.length > SEARCH_CONFIG.SEARCH_RESULT_SNIPPET_LENGTH ? '...' : ''}\n`;
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
  }

  /**
   * Check if web search is available and enabled
   */
  isSearchAvailable(): boolean {
    const state = this.stateManager.getState();
    return !!(this.searchService && state.useWebSearch);
  }

  /**
   * Get search service health status
   */
  async getSearchHealthStatus(): Promise<{ accessible: boolean; error?: string }> {
    if (!this.searchService) {
      return { accessible: false, error: 'Search service not initialized' };
    }

    try {
      return await this.searchService.checkHealth();
    } catch (error) {
      return { 
        accessible: false, 
        error: error instanceof Error ? error.message : 'Unknown error' 
      };
    }
  }

  /**
   * Format search results for display
   */
  formatSearchResults(searchData: any): string {
    if (!searchData || !searchData.results) {
      return 'No search results available.';
    }

    let formatted = `Search Results for "${searchData.query}":\n\n`;
    
    searchData.results.forEach((result: any, index: number) => {
      formatted += `${index + 1}. **${result.title}**\n`;
      formatted += `   🔗 ${result.url}\n`;
      if (result.content) {
        formatted += `   📝 ${result.content.substring(0, 150)}...\n`;
      }
      formatted += '\n';
    });

    if (searchData.scrapedContent && searchData.successfulScrapes > 0) {
      formatted += `\n✅ Successfully scraped detailed content from ${searchData.successfulScrapes} pages.`;
    }

    return formatted;
  }

  /**
   * Extract search query from user input
   */
  extractSearchQuery(userInput: string): string {
    // Remove common conversational elements to get better search query
    let query = userInput
      .replace(/^(can you|could you|please|help me|tell me about|what is|what are|how to|how do|where is|where are|when is|when are|why is|why are)/i, '')
      .replace(/\?+$/, '')
      .trim();

    // Limit query length for better search results
    if (query.length > 100) {
      query = query.substring(0, 100).trim();
    }

    return query || userInput;
  }

  /**
   * Get search statistics
   */
  getSearchStats(): {
    totalSearches: number;
    successfulSearches: number;
    failedSearches: number;
  } {
    const state = this.stateManager.getState();
    const searchMessages = state.messages.filter((msg: ChatMessage) => msg.isSearchResults);
    
    return {
      totalSearches: searchMessages.length,
      successfulSearches: searchMessages.filter((msg: ChatMessage) => 
        msg.searchData && msg.searchData.results && msg.searchData.results.length > 0
      ).length,
      failedSearches: searchMessages.filter((msg: ChatMessage) => 
        !msg.searchData || !msg.searchData.results || msg.searchData.results.length === 0
      ).length
    };
  }
}
