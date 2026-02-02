/**
 * Web Search Service for SearXNG Integration
 * 
 * This service handles web search functionality using a local SearXNG instance.
 * It provides methods to search the web and format results for AI consumption.
 */

import { ApiService, SearchResult } from '../types';

export interface SearchResponse {
  query: string;
  number_of_results: number;
  results: SearchResult[];
  suggestions?: string[];
  unresponsive_engines?: string[];
}

export interface SearchOptions {
  category?: string;
  engines?: string[];
  language?: string;
  time_range?: string;
  safesearch?: number;
  format?: 'json' | 'csv' | 'rss';
}

export class SearchService {
  private apiService: ApiService | null;
  private defaultOptions: SearchOptions;

  constructor(apiService?: ApiService) {
    this.apiService = apiService || null;
    this.defaultOptions = {
      format: 'json',
      language: 'en',
      safesearch: 1,
      category: 'general'
    };
  }

  /**
   * Perform a web search using the BrainDrive backend proxy to SearXNG
   */
  async search(query: string, options: SearchOptions = {}): Promise<SearchResponse> {
    if (!query?.trim()) {
      throw new Error('Search query is required');
    }

    if (!this.apiService) {
      throw new Error('API service not available for authenticated requests');
    }

    const searchOptions = { ...this.defaultOptions, ...options };
    
    try {
      // Build search parameters for the API request
      const params: Record<string, string> = {
        q: query.trim()
      };
      
      // Add search options as parameters
      Object.entries(searchOptions).forEach(([key, value]) => {
        if (value !== undefined && value !== null && key !== 'format') {
          params[key] = value.toString();
        }
      });

      console.log(`🔍 Searching via BrainDrive API service with params:`, params);

      // Use the authenticated API service
      const data = await this.apiService.get('/api/v1/searxng/web', { params });
      
      // Validate response structure
      if (!data || typeof data !== 'object') {
        throw new Error('Invalid response format from SearXNG');
      }

      // Transform the response to our format
      const searchResponse: SearchResponse = {
        query: query.trim(),
        number_of_results: data.number_of_results || 0,
        results: this.transformResults(data.results || []),
        suggestions: data.suggestions || [],
        unresponsive_engines: data.unresponsive_engines || []
      };

      console.log(`✅ SearXNG search completed: ${searchResponse.number_of_results} results`);
      return searchResponse;

    } catch (error) {
      console.error('❌ SearXNG search error:', error);
      
      if (error instanceof Error) {
        // Handle specific API errors
        if (error.message.includes('401') || error.message.includes('Unauthorized')) {
          throw new Error('Authentication failed. Please ensure you are logged in.');
        }
        if (error.message.includes('502') || error.message.includes('503')) {
          throw new Error('Search service is temporarily unavailable. Please try again later.');
        }
        throw error;
      }
      
      throw new Error(String(error));
    }
  }

  /**
   * Transform SearXNG results to our SearchResult format
   */
  private transformResults(results: any[]): SearchResult[] {
    if (!Array.isArray(results)) {
      return [];
    }

    return results
      .filter(result => result && typeof result === 'object')
      .map(result => ({
        title: result.title || 'Untitled',
        url: result.url || '',
        content: result.content || result.snippet || '',
        engine: result.engine,
        score: result.score
      }))
      .filter(result => result.url && result.title); // Filter out invalid results
  }

  /**
   * Scrape content from URLs
   */
  async scrapeUrls(urls: string[], maxContentLength: number = 5000): Promise<any> {
    if (!this.apiService) {
      throw new Error('API service not available for scraping');
    }

    if (!urls || urls.length === 0) {
      throw new Error('At least one URL is required for scraping');
    }

    try {
      console.log(`🕷️ Scraping ${urls.length} URLs via BrainDrive API...`);

      const response = await this.apiService.post('/api/v1/searxng/scrape', urls, {
        params: { max_content_length: maxContentLength }
      });

      console.log(`✅ Scraping completed: ${response.summary?.successful_scrapes}/${response.summary?.total_urls} successful`);
      return response;

    } catch (error) {
      console.error('❌ URL scraping error:', error);
      
      if (error instanceof Error) {
        if (error.message.includes('401') || error.message.includes('Unauthorized')) {
          throw new Error('Authentication failed for scraping. Please ensure you are logged in.');
        }
        if (error.message.includes('502') || error.message.includes('503')) {
          throw new Error('Scraping service is temporarily unavailable. Please try again later.');
        }
        throw error;
      }
      
      throw new Error(String(error));
    }
  }

  /**
   * Enhanced search with web scraping
   */
  async searchWithScraping(
    query: string, 
    options: SearchOptions = {}, 
    scrapeTopResults: number = 3,
    maxContentLength: number = 3000
  ): Promise<{ searchResponse: SearchResponse; scrapedContent: any }> {
    // First, perform the search
    const searchResponse = await this.search(query, options);
    
    if (searchResponse.results.length === 0) {
      return { 
        searchResponse, 
        scrapedContent: { results: [], summary: { total_urls: 0, successful_scrapes: 0, total_content_length: 0 } } 
      };
    }

    // Extract URLs from top results
    const urlsToScrape = searchResponse.results
      .slice(0, scrapeTopResults)
      .map(result => result.url)
      .filter(url => url && url.startsWith('http'));

    if (urlsToScrape.length === 0) {
      return { 
        searchResponse, 
        scrapedContent: { results: [], summary: { total_urls: 0, successful_scrapes: 0, total_content_length: 0 } } 
      };
    }

    // Scrape the URLs
    const scrapedContent = await this.scrapeUrls(urlsToScrape, maxContentLength);
    
    return { searchResponse, scrapedContent };
  }

  /**
   * Check if the search service is accessible via backend
   */
  async checkHealth(): Promise<{ accessible: boolean; error?: string }> {
    if (!this.apiService) {
      return { 
        accessible: false, 
        error: 'API service not available' 
      };
    }

    try {
      const healthData = await this.apiService.get('/api/v1/searxng/health');
      return { 
        accessible: healthData.accessible === true,
        error: healthData.accessible ? undefined : healthData.error
      };
    } catch (error) {
      if (error instanceof Error) {
        return { 
          accessible: false, 
          error: `Health check failed: ${error.message}` 
        };
      }
      return { 
        accessible: false, 
        error: 'Health check failed: Unknown error' 
      };
    }
  }

  /**
   * Set API service for authenticated requests
   */
  setApiService(apiService: ApiService): void {
    this.apiService = apiService;
  }

  /**
   * Get current API service
   */
  getApiService(): ApiService | null {
    return this.apiService;
  }
}