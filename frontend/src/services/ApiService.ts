import axios, { AxiosInstance, AxiosRequestConfig, AxiosResponse, AxiosError } from 'axios';
import { AbstractBaseService, ServiceVersion, ServiceCapability } from './base/BaseService';
import { config } from '../config';
import { fetchEventSource } from '@microsoft/fetch-event-source';

export interface ApiConfig {
  baseURL: string;
  timeout?: number;
  headers?: Record<string, string>;
  retryAttempts?: number;
}

export interface User {
  id: string;
  username: string;
  email: string;
  full_name?: string;
  profile_picture?: string;
  is_active: boolean;
  is_verified: boolean;
  version?: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  refresh_expires_in?: number;
  issued_at?: number;
  user_id?: string;
  refresh_token?: string;
  user: User;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
    public code?: string,
    public data?: any
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

class ApiService extends AbstractBaseService {
  private api: AxiosInstance;
  private tokenRefreshPromise: Promise<string> | null = null;
  private static instance: ApiService;
  private retryAttempts: number;

  private constructor() {
    super(
      'api',
      { major: 1, minor: 0, patch: 0 },
      [
        {
          name: 'http',
          description: 'HTTP/HTTPS request capabilities',
          version: '1.0.0'
        },
        {
          name: 'auth',
          description: 'Authentication and token management',
          version: '1.0.0'
        }
      ]
    );

    this.retryAttempts = 5; // Increase retry attempts for initial connection
    
    const baseURL = config.api.baseURL;
    this.api = axios.create({
      baseURL,
      timeout: config.api.timeout || 10000,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      // Enable sending cookies with cross-origin requests
      withCredentials: true,
    });

    // console.log('ApiService initializing with config:', {
    //  baseURL,
    //  timeout: config.api.timeout,
    //  proxyEnabled: !baseURL, // Empty baseURL means using Vite proxy
    //  targetUrl: !baseURL ? 'http://localhost:8005' : undefined
    //});
    
    this.setupInterceptors();
  }

  public static getInstance(): ApiService {
    if (!ApiService.instance) {
      ApiService.instance = new ApiService();
    }
    return ApiService.instance;
  }

  private async retryWithBackoff<T>(
    operation: () => Promise<T>,
    maxAttempts: number = 3,
    delayMs: number = 2000
  ): Promise<T> {
    let lastError: Error = new Error('Unknown error');
    let totalWaitTime = 0;
    const fixedDelay = 6000; // Fixed 6 second delay
    
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        return await operation();
      } catch (error) {
        lastError = error as Error;
        
        // Retry on network errors or 500-level server errors (server might be starting up)
        if (error instanceof AxiosError) {
          if (error.code === 'ERR_NETWORK' || (error.response?.status && error.response.status >= 500)) {
            const status = error.response?.status;
            const isStartupError = status === 500 && (!error.response?.data || error.response.data === '');
            totalWaitTime += fixedDelay;
            
            // console.log(`Request attempt ${attempt}/${maxAttempts} failed:`, {
            //  code: error.code,
            //  status,
            //  message: error.message,
            //  isStartupError: isStartupError ? 'Yes - backend likely still starting' : 'No',
            //  nextRetryIn: attempt < maxAttempts ? `${fixedDelay}ms (6 seconds)` : 'No more retries',
            //  totalTimeWaited: `${totalWaitTime}ms (${totalWaitTime/1000} seconds)`
            //});
            
            if (attempt < maxAttempts) {
              await new Promise(resolve => setTimeout(resolve, fixedDelay));
              continue;
            }
          }
        }
        
        throw error;
      }
    }
    
    throw lastError;
  }

  public async initialize(): Promise<void> {
    return Promise.resolve();
  }

  public async destroy(): Promise<void> {
    // Cancel any pending requests when service is destroyed
    const cancelSource = axios.CancelToken.source();
    cancelSource.cancel('Service is being destroyed');
    return Promise.resolve();
  }

  private setupInterceptors(): void {
    // Request interceptor
    this.api.interceptors.request.use(
      (config) => {
        const token = localStorage.getItem('accessToken');
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
      },
      (error) => {
        return Promise.reject(error);
      }
    );

    // Response interceptor
    this.api.interceptors.response.use(
      (response) => response,
      async (error) => {
        const originalRequest = error.config;
        
        // If there's no response or config, we can't retry
        if (!error.response || !originalRequest) {
          return Promise.reject(error);
        }
        
        // Skip token refresh for login and register requests - they should fail immediately
        if (originalRequest.url && (
          originalRequest.url.includes('/auth/login') ||
          originalRequest.url.includes('/auth/register')
        )) {
          return Promise.reject(error);
        }
        
        // If error is not 401 or request has already been retried, reject
        if (error.response.status !== 401 || originalRequest._retry) {
          return Promise.reject(error);
        }

        // Mark this request as retried to prevent infinite loops
        originalRequest._retry = true;

        try {
          // console.log('ApiService interceptor: Detected 401 error, attempting token refresh');
          
          // Set a timeout for the refresh operation
          const refreshTimeout = 30000; // Increased to 30 seconds
          
          // Create a timeout promise
          const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => {
              reject(new Error(`Token refresh timeout after ${refreshTimeout}ms`));
            }, refreshTimeout);
          });
          
          // Race between the refresh operation and the timeout
          const newAccessToken = await Promise.race([
            this.refreshToken(),
            timeoutPromise
          ]);
          
          // If we got here, the token refresh was successful
          // console.log('ApiService interceptor: Token refresh successful, retrying original request');
          
          // Update the Authorization header with the new token
          originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
          
          // Make sure withCredentials is set for the retry
          originalRequest.withCredentials = true;
          
          // Retry the original request with the new token
          return this.api(originalRequest);
        } catch (refreshError) {
          console.error('ApiService interceptor: Token refresh failed', refreshError);
          
          // Check if this is a network error or timeout
          if (refreshError instanceof Error &&
              (refreshError.message.includes('Network Error') ||
               refreshError.message.includes('timeout'))) {
            console.error('ApiService interceptor: Network error or timeout during refresh - possible connectivity issue');
            // console.log('ApiService interceptor: Will not redirect to login for network/timeout issues');
            
            // Don't redirect for network errors or timeouts
            // Instead, we'll let the user continue with the current token if possible
            // or retry the refresh later
            
            // If we have a valid access token, use it
            const currentToken = localStorage.getItem('accessToken');
            if (currentToken) {
              // console.log('ApiService interceptor: Using existing access token despite refresh failure');
              return Promise.reject(new Error('Refresh failed but continuing with existing token'));
            } else {
              // If we don't have a valid token, we need to redirect
              // console.log('ApiService interceptor: No valid token available, redirecting to login');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              window.location.href = '/login?reason=no_valid_token';
            }
          } else if (refreshError instanceof Error) {
            // Handle specific authentication errors
            if (refreshError.message === 'account_not_found') {
              console.error('ApiService interceptor: User account not found during refresh');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              window.location.href = '/login?reason=account_not_found';
            } else if (refreshError.message === 'token_expired') {
              console.error('ApiService interceptor: Token expired during refresh');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              window.location.href = '/login?reason=token_expired';
            } else if (refreshError.message === 'invalid_token_type') {
              console.error('ApiService interceptor: Invalid token type during refresh');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              window.location.href = '/login?reason=invalid_token';
            } else {
              // For other errors (like authentication errors)
              // console.log('ApiService interceptor: Authentication error during refresh');
              
              // Clear tokens for authentication errors
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              
              // If refresh token is invalid, redirect to login with a reason
              // console.log('ApiService interceptor: Redirecting to login page');
              window.location.href = '/login?reason=session_expired';
            }
          } else {
            // For non-Error objects
            // console.log('ApiService interceptor: Unknown error during refresh');
            localStorage.removeItem('accessToken');
            localStorage.removeItem('refreshToken');
            window.location.href = '/login?reason=unknown_error';
          }
          
          return Promise.reject(refreshError);
        }
      }
    );
  }

  public async refreshToken(): Promise<string> {
    // console.log('ApiService: Attempting to refresh token');
    
    // Add a timeout to prevent hanging refresh requests
    const timeoutPromise = new Promise<string>((_, reject) => {
      setTimeout(() => {
        reject(new Error('Token refresh timeout after 30 seconds'));
      }, 30000); // Increased to 30 second timeout for better reliability
    });
    
    // If a refresh is already in progress, reuse that promise to prevent multiple simultaneous refreshes
    if (this.tokenRefreshPromise) {
      // console.log('ApiService: Token refresh already in progress, reusing promise');
      return Promise.race([this.tokenRefreshPromise, timeoutPromise]);
    }

    const refreshPromise = (async () => {
      try {
        // Get stored refresh token if available (for fallback)
        const storedRefreshToken = localStorage.getItem('refreshToken');
        
        if (!storedRefreshToken) {
          console.warn('ApiService: No refresh token found in localStorage for fallback');
        } else {
          // console.log('ApiService: Found refresh token in localStorage for fallback');
          // console.log(`ApiService: Refresh token length: ${storedRefreshToken.length}`);
        }
        
        // Create a request cancellation token
        const cancelToken = axios.CancelToken.source();
        
        // Set up a timeout to cancel the request if it takes too long
        const requestTimeout = setTimeout(() => {
          cancelToken.cancel('Request took too long');
        }, 25000); // Increased to 25 seconds
        
        // Prepare request body with refresh token
        const requestBody = storedRefreshToken ? { refresh_token: storedRefreshToken } : {};
        
        // Send refresh request with HTTP-only cookie and also include token in body as fallback
        // console.log('ApiService: Sending refresh request with HTTP-only cookie and fallback token in body');
        // console.log('ApiService: Request body contains refresh_token:', !!storedRefreshToken);
        
        // Log the actual refresh token (first 10 chars only for security)
        if (storedRefreshToken) {
          const tokenPreview = storedRefreshToken.substring(0, 10) + '...';
          // console.log(`ApiService: Using refresh token (preview): ${tokenPreview}`);
        }
        
        const response = await this.api.post<AuthResponse>(
          '/api/v1/auth/refresh',
          requestBody,
          {
            withCredentials: true, // Important for sending cookies
            timeout: 25000, // 25 second timeout for the request itself
            cancelToken: cancelToken.token,
            headers: {
              'Content-Type': 'application/json',
              'Accept': 'application/json'
            }
          }
        );
        
        // Clear the request timeout
        clearTimeout(requestTimeout);
        
        // Extract and store new tokens
        const {
          access_token,
          refresh_token,
          expires_in,
          refresh_expires_in,
          issued_at,
          user_id
        } = response.data;
        
        // console.log('ApiService: Received new access token');
        // console.log(`ApiService: Token expires in: ${expires_in || 'unknown'} seconds`);
        
        if (issued_at) {
          const issuedDate = new Date(issued_at * 1000);
          // console.log(`ApiService: Token issued at: ${issuedDate.toISOString()}`);
        }
        
        // Store access token in localStorage
        localStorage.setItem('accessToken', access_token);
        
        // Store token expiration time
        if (expires_in) {
          const expiresAt = Date.now() + (expires_in * 1000);
          localStorage.setItem('tokenExpiresAt', expiresAt.toString());
          // console.log(`ApiService: Access token expires at: ${new Date(expiresAt).toISOString()}`);
        }
        
        // Store refresh token in localStorage as fallback
        if (refresh_token) {
          // console.log('ApiService: Storing refresh token in localStorage as fallback');
          // console.log(`ApiService: Refresh token length: ${refresh_token.length}`);
          localStorage.setItem('refreshToken', refresh_token);
          
          // Store refresh token expiration time
          if (refresh_expires_in) {
            const refreshExpiresAt = Date.now() + (refresh_expires_in * 1000);
            localStorage.setItem('refreshTokenExpiresAt', refreshExpiresAt.toString());
            // console.log(`ApiService: Refresh token expires at: ${new Date(refreshExpiresAt).toISOString()}`);
          }
        } else {
          console.warn('ApiService: No refresh token in response - this may cause authentication issues');
        }
        
        // console.log('ApiService: Token refresh successful');
        return access_token;
      } catch (error) {
        console.error('ApiService: Token refresh failed', error);
        
        // Log specific error details
        if (error instanceof AxiosError) {
          console.error('ApiService: Refresh error details:', {
            status: error.response?.status,
            data: error.response?.data,
            headers: error.response?.headers,
            message: error.message
          });
          
          // Check if this is a network error, which might indicate connectivity issues
          if (error.code === 'ERR_NETWORK') {
            console.error('ApiService: Network error during token refresh - possible connectivity issue');
          }
        }
        
        // Handle different types of errors
        if (error instanceof AxiosError) {
          if (error.response && [401, 403].includes(error.response.status)) {
            // Authentication errors (401/403)
            // console.log('ApiService: Authentication error during refresh');
            
            // Get the error detail if available
            const errorDetail = error.response.data?.detail || '';
            // console.log('ApiService: Error detail:', errorDetail);
            
            // Handle specific error cases
            if (errorDetail === 'STALE_TOKEN_RESET_REQUIRED' || errorDetail.startsWith('INVALID_TOKEN_RESET_REQUIRED') || errorDetail.startsWith('BLOCKED_TOKEN_DETECTED')) {
              console.error('ApiService: INVALID TOKEN DETECTED - Performing complete reset');
              console.error('ApiService: Reason:', errorDetail);
              
              // Clear ALL storage types
              localStorage.clear();
              sessionStorage.clear();
              
              // Clear IndexedDB if available
              if ('indexedDB' in window) {
                try {
                  indexedDB.databases().then(databases => {
                    databases.forEach(db => {
                      if (db.name) indexedDB.deleteDatabase(db.name);
                    });
                  });
                } catch (e) {
                  console.warn('Failed to clear IndexedDB:', e);
                }
              }
              
              // Enhanced cookie clearing with multiple domain/path combinations
              const domains = ['', 'localhost', '127.0.0.1', '10.0.2.149', '.localhost', '.127.0.0.1'];
              const paths = ['/', '/api', '/api/v1', ''];
              
              // Get all existing cookies
              const cookies = document.cookie.split(";");
              
              cookies.forEach(cookie => {
                const eqPos = cookie.indexOf("=");
                const name = eqPos > -1 ? cookie.substr(0, eqPos).trim() : cookie.trim();
                
                domains.forEach(domain => {
                  paths.forEach(path => {
                    // Try multiple clearing methods
                    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=${path}${domain ? `;domain=${domain}` : ''}`;
                    document.cookie = `${name}=;max-age=0;path=${path}${domain ? `;domain=${domain}` : ''}`;
                    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=${path}${domain ? `;domain=${domain}` : ''};secure=false`;
                    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=${path}${domain ? `;domain=${domain}` : ''};httponly=false`;
                  });
                });
              });
              
              // Force complete page reload with cache clearing
              console.log('ApiService: Forcing page reload to clear all cached state');
              window.location.replace('/login?reason=invalid_token_cleared&t=' + Date.now());
              return; // Don't throw error, just redirect
            } else if (errorDetail.includes('user not found')) {
              console.error('ApiService: User not found during token refresh - account may have been deleted');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              
              // Create a custom error with a specific message for the caller to handle
              const customError = new Error('account_not_found');
              customError.name = 'AuthError';
              throw customError;
            } else if (errorDetail.includes('token has expired')) {
              console.error('ApiService: Refresh token has expired');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              
              // Create a custom error with a specific message for the caller to handle
              const customError = new Error('token_expired');
              customError.name = 'AuthError';
              throw customError;
            } else if (errorDetail.includes('not a refresh token')) {
              console.error('ApiService: Invalid token type - not a refresh token');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              
              // Create a custom error with a specific message for the caller to handle
              const customError = new Error('invalid_token_type');
              customError.name = 'AuthError';
              throw customError;
            } else {
              // Generic authentication error
              // console.log('ApiService: Generic authentication error during refresh');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              
              // Don't redirect here, let the caller decide what to do
              // console.log('ApiService: Not redirecting from refreshToken method, letting caller handle it');
            }
          } else if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
            // Timeout errors
            // console.log('ApiService: Timeout during refresh, may retry later');
            // Don't clear tokens for timeout errors
          } else if (error.code === 'ERR_NETWORK') {
            // Network errors
            // console.log('ApiService: Network error during refresh, may retry when online');
            // Don't clear tokens for network errors
          } else {
            // Other errors
            // console.log('ApiService: Other error during refresh, may retry');
            // Don't clear tokens for other errors
          }
        } else {
          // Non-Axios errors
          // console.log('ApiService: Non-Axios error during refresh:', error);
        }
        
        // For all errors, throw to allow retry logic to work
        // console.log('ApiService: Throwing error to allow retry logic to work');
        
        throw error;
      } finally {
        this.tokenRefreshPromise = null;
      }
    })();

    // Store the promise with race condition
    this.tokenRefreshPromise = Promise.race([refreshPromise, timeoutPromise]);
    return this.tokenRefreshPromise;
  }

  private async retryRequest<T>(
    requestFn: () => Promise<T>,
    retries: number = this.retryAttempts
  ): Promise<T> {
    try {
      return await requestFn();
    } catch (error) {
      if (retries > 0 && (error instanceof ApiError) && error.status && [408, 429, 500, 502, 503, 504].includes(error.status)) {
        const delay = Math.min(1000 * (2 ** (this.retryAttempts - retries)), 10000);
        await new Promise(resolve => setTimeout(resolve, delay));
        return this.retryRequest(requestFn, retries - 1);
      }
      throw error;
    }
  }

  public async get<T = any>(path: string, config?: AxiosRequestConfig): Promise<T> {
    return this.retryRequest(async () => {
      const response = await this.api.get<T>(path, config);
      return response.data;
    });
  }

  public async post<T = any>(path: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
    // console.log('ApiService.post called with path:', path);
    // console.log('ApiService.post config:', config);
    
    // Check if this is a streaming request
    const isStreaming = config?.responseType === 'text' || (data && data.stream === true);
    if (isStreaming) {
      // console.log('ApiService: Detected streaming request');
    }
    
    return this.retryRequest(async () => {
      try {
        // console.log('ApiService: Making request to', path);
        const startTime = Date.now();
        
        // For streaming requests, we need to handle the response differently
        if (isStreaming) {
          // Use axios directly to get the full response
          const response = await this.api.post(path, data, {
            ...config,
            // Force responseType to be 'text' for streaming
            responseType: 'text',
            // Add transformResponse to prevent JSON parsing
            transformResponse: [(data) => data]
          });
          
          const endTime = Date.now();
          // console.log(`ApiService: Received streaming response in ${endTime - startTime}ms`);
          // console.log('ApiService: Response status:', response.status);
          // console.log('ApiService: Response headers:', response.headers);
          
          if (typeof response.data === 'string') {
            // console.log(`ApiService: Received string data of length ${response.data.length}`);
            // console.log('ApiService: First 100 chars:', response.data.substring(0, 100));
            
            // Count the number of newlines to estimate the number of chunks
            const newlineCount = (response.data.match(/\n/g) || []).length;
            // console.log(`ApiService: Estimated ${newlineCount} chunks in response`);
          } else {
            // console.log('ApiService: Received non-string data:', typeof response.data);
          }
          
          return response.data as unknown as T;
        } else {
          // Regular non-streaming request
          const response = await this.api.post<T>(path, data, config);
          const endTime = Date.now();
          // console.log(`ApiService: Received response in ${endTime - startTime}ms`);
          return response.data;
        }
      } catch (error) {
        console.error('ApiService.post error:', error);
        throw error;
      }
    });
  }
  public async postStreaming<T = any>(
    path: string,
    data?: any,
    onChunk?: (chunk: string) => void,
    config?: AxiosRequestConfig
  ): Promise<T> {
    const baseURL = this.api.defaults.baseURL || '';
    const url = `${baseURL}${path}`;
    const token = localStorage.getItem('accessToken');
    let accumulated = '';
  
    const payload = data;  // ✅ Use full payload structure expected by backend
  
    // console.log('Connecting to streaming endpoint:', url);
    // console.log('Payload sent:', payload);
  
    return new Promise<T>((resolve, reject) => {
      fetchEventSource(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        body: JSON.stringify(payload),
        openWhenHidden: true,
  
        async onopen(response: Response): Promise<void> {
          const type = response.headers.get('content-type') || '';
          if (!response.ok || !type.includes('text/event-stream')) {
            reject(new Error(`Unexpected response: ${response.status} ${type}`));
            return Promise.resolve();
          } else {
            // console.log('✅ SSE connection established');
            return Promise.resolve();
          }
        },
  
        onmessage(event) {
          if (event.data === '[DONE]') {
            // console.log('✅ Streaming completed');
            resolve(accumulated as unknown as T);
            return;
          }
  
          try {
            // console.log('Received chunk:', event.data);
            accumulated += event.data;
            onChunk?.(event.data);
          } catch (err) {
            console.warn('⚠️ Error handling streamed chunk:', event.data, err);
          }
        },
  
        onerror(err) {
          console.error('❌ Stream error:', err);
          reject(err);
        }
      });
    });
  }

  // Simple SSE GET helper for endpoints like /install/{task_id}/events
  public async getSse(
    path: string,
    onMessage?: (data: string) => void
  ): Promise<void> {
    const baseURL = this.api.defaults.baseURL || '';
    const url = `${baseURL}${path}`;
    const token = localStorage.getItem('accessToken');

    return new Promise<void>((resolve, reject) => {
      fetchEventSource(url, {
        method: 'GET',
        headers: {
          'Accept': 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        openWhenHidden: true,
        async onopen(response: Response): Promise<void> {
          const type = response.headers.get('content-type') || '';
          // Debug log SSE handshake
          try { console.debug('SSE GET open:', { status: response.status, type }); } catch {}
          if (!response.ok || !type.includes('text/event-stream')) {
            // Surface response text for debugging
            try { console.error('SSE GET unexpected response', response.status, await response.text()); } catch {}
            reject(new Error(`Unexpected response: ${response.status} ${type}`));
            return Promise.resolve();
          }
          return Promise.resolve();
        },
        onmessage: (event) => {
          if (event.data) {
            try {
              onMessage?.(event.data);
            } catch (e) {
              console.warn('SSE onMessage handler error:', e);
            }
          }
        },
        onerror: (err) => {
          try { console.error('SSE GET error:', err); } catch {}
          reject(err);
        },
        onclose: () => {
          try { console.debug('SSE GET closed'); } catch {}
          resolve();
        }
      });
    });
  }

  public subscribeToSse(
    path: string,
    handlers: {
      onOpen?: (response: Response) => void | Promise<void>;
      onMessage?: (data: string) => void;
      onError?: (error: unknown) => void;
      onClose?: () => void;
    } = {}
  ): () => void {
    const baseURL = this.api.defaults.baseURL || '';
    const url = `${baseURL}${path}`;
    const token = localStorage.getItem('accessToken');
    const controller = new AbortController();

    fetchEventSource(url, {
      method: 'GET',
      headers: {
        'Accept': 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      signal: controller.signal,
      openWhenHidden: true,
      async onopen(response: Response): Promise<void> {
        const type = response.headers.get('content-type') || '';
        if (!response.ok || !type.includes('text/event-stream')) {
          const error = new Error(`Unexpected response: ${response.status} ${type}`);
          handlers.onError?.(error);
          controller.abort();
          return Promise.resolve();
        }
        if (handlers.onOpen) {
          await handlers.onOpen(response);
        }
        return Promise.resolve();
      },
      onmessage(event) {
        handlers.onMessage?.(event.data);
      },
      onerror(err) {
        handlers.onError?.(err);
        throw err;
      },
      onclose() {
        handlers.onClose?.();
      }
    });

    return () => {
      controller.abort();
    };
  }
  
  

  
  
  

  
  public async postStreaming_original<T = any>(
    path: string,
    data?: any,
    onChunk?: (chunk: string) => void,
    config?: AxiosRequestConfig
  ): Promise<T> {
    // console.log('ApiService.postStreaming called with path:', path);
  
    return this.retryRequest(async () => {
      return new Promise<T>((resolve, reject) => {
        const url = `${this.api.defaults.baseURL || ''}${path}`;
        const token = localStorage.getItem('accessToken');
        let accumulatedResponse = '';
  
        // console.log('Using fetchEventSource for streaming');
        // console.log('Full URL:', url);
  
        fetchEventSource(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {})
          },
          body: JSON.stringify(data),
          openWhenHidden: true,
          onmessage(event) {
            if (event.data === '[DONE]') {
              // console.log('Streaming complete: [DONE]');
              resolve(accumulatedResponse as unknown as T);
              return;
            }
  
            try {
              const parsed = JSON.parse(event.data);
              accumulatedResponse += event.data;
              // console.log('Received chunk:', Object.keys(parsed).join(', '));
              onChunk?.(event.data);
            } catch (e) {
              console.warn('Invalid JSON chunk received:', event.data);
            }
          },
          onerror(err) {
            console.error('fetchEventSource streaming error:', err);
            reject(err);
          },
          async onopen(response): Promise<void> {
            if (response.ok && response.headers.get('content-type')?.includes('text/event-stream')) {
              // console.log('Connection opened successfully for SSE');
              return Promise.resolve();
            }
            throw new Error(`Unexpected response status or content-type: ${response.status}`);
          }
        });
      });
    });
  }
  

  public async put<T = any>(path: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
    // console.log('ApiService.put called with path:', path);
    // console.log('ApiService.put data:', JSON.stringify(data, null, 2));
    
    // Check specifically for navigation_route_id
    if (data && 'navigation_route_id' in data) {
      // console.log(`PUT request contains navigation_route_id: ${data.navigation_route_id}`);
      // console.log(`navigation_route_id type: ${typeof data.navigation_route_id}`);
      
      // Log if it's null or empty string
      if (data.navigation_route_id === null) {
        // console.log('navigation_route_id is explicitly null');
      } else if (data.navigation_route_id === '') {
        // console.log('navigation_route_id is empty string');
      } else if (!data.navigation_route_id) {
        // console.log('navigation_route_id is falsy but not null or empty string');
      }
    } else {
      // console.log('PUT request does not contain navigation_route_id property');
    }
    
    return this.retryRequest(async () => {
      // console.log('Making PUT request to:', path);
      const response = await this.api.put<T>(path, data, config);
      // console.log('PUT response status:', response.status);
      // console.log('PUT response data:', JSON.stringify(response.data, null, 2));
      return response.data;
    });
  }

  public async delete<T = any>(path: string, config?: AxiosRequestConfig): Promise<T> {
    return this.retryRequest(async () => {
      const response = await this.api.delete<T>(path, config);
      return response.data;
    });
  }

  public async patch<T = any>(path: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
    return this.retryRequest(async () => {
      const response = await this.api.patch<T>(path, data, config);
      return response.data;
    });
  }

  public async login(credentials: LoginCredentials): Promise<AuthResponse> {
    try {
      // console.log('ApiService: Attempting login for user:', credentials.email);
      
      // Check if withCredentials is set to true (needed for cookies)
      // console.log('ApiService: axios withCredentials setting:', this.api.defaults.withCredentials);
      
      const response = await this.api.post<AuthResponse>('/api/v1/auth/login', credentials, {
        headers: {
          'Content-Type': 'application/json',
        },
        // Enable cookies for this request
        withCredentials: true
      });
      
      // console.log('ApiService: Login response status:', response.status);
      
      // Check for Set-Cookie header (note: this will usually not be visible in the browser due to security restrictions)
      const setCookieHeader = response.headers['set-cookie'];
      if (setCookieHeader) {
        // console.log('ApiService: Set-Cookie header present in response');
      } else {
        // console.log('ApiService: No Set-Cookie header visible in response (normal for secure cookies)');
      }
      
      const {
        access_token,
        refresh_token,
        expires_in,
        refresh_expires_in,
        issued_at,
        user_id,
        user
      } = response.data;
      
      // console.log('ApiService: Login successful, processing tokens');
      // console.log(`ApiService: Token expires in: ${expires_in || 'unknown'} seconds`);
      
      if (issued_at) {
        const issuedDate = new Date(issued_at * 1000);
        // console.log(`ApiService: Token issued at: ${issuedDate.toISOString()}`);
      }
      
      // Store access token in localStorage
      // console.log('ApiService: Storing access token in localStorage');
      localStorage.setItem('accessToken', access_token);
      
      // Store token expiration time
      if (expires_in) {
        const expiresAt = Date.now() + (expires_in * 1000);
        localStorage.setItem('tokenExpiresAt', expiresAt.toString());
        // console.log(`ApiService: Access token expires at: ${new Date(expiresAt).toISOString()}`);
      }
      
      // Store refresh token in localStorage as fallback
      if (refresh_token) {
        // console.log('ApiService: Storing refresh token in localStorage as fallback');
        // console.log(`ApiService: Refresh token length: ${refresh_token.length}`);
        localStorage.setItem('refreshToken', refresh_token);
        
        // Store refresh token expiration time
        if (refresh_expires_in) {
          const refreshExpiresAt = Date.now() + (refresh_expires_in * 1000);
          localStorage.setItem('refreshTokenExpiresAt', refreshExpiresAt.toString());
          // console.log(`ApiService: Refresh token expires at: ${new Date(refreshExpiresAt).toISOString()}`);
        }
      } else {
        console.warn('ApiService: No refresh token in response body, relying on HTTP-only cookie - this may cause authentication issues');
      }
      
      // console.log('ApiService: Login successful for user:', user.email);
      return response.data;
    } catch (error) {
      console.error('ApiService: Login attempt failed', error);
      
      if (error instanceof AxiosError) {
        const status = error.response?.status;
        const isServerError = status && status >= 500;
        const isStartupError = status === 500 && (!error.response?.data || error.response.data === '');
        
        console.error('ApiService: Login error details:', {
          status,
          code: error.code,
          message: error.message,
          details: error.response?.data,
          isServerError: isServerError ? 'Yes' : 'No',
          isStartupError: isStartupError ? 'Yes - backend might still be starting' : 'No'
        });
      }
      throw error;
    }
  }
}

export default ApiService;
