import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import ApiService, { User } from '../services/ApiService';
import { useNavigate } from 'react-router-dom';
import { useService, useTheme as useThemeService } from './ServiceContext';
import { UserSettingsInitService } from '../services/UserSettingsInitService';
import { UserNavigationInitService } from '../services/UserNavigationInitService';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (userData: { username: string; email: string; password: string; full_name: string }) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();
  const themeService = useThemeService();
  const apiService = ApiService.getInstance();
  const userSettingsInitService = useService<UserSettingsInitService>('userSettingsInit');
  const userNavigationInitService = useService<UserNavigationInitService>('userNavigationInit');

  // Function to check if token is about to expire and refresh it if needed
  const checkAndRefreshToken = useCallback(async () => {
    try {
      const tokenExpiresAtStr = localStorage.getItem('tokenExpiresAt');
      if (!tokenExpiresAtStr) {
        // console.log('AuthContext: No token expiration time found');
        return;
      }
      
      const tokenExpiresAt = parseInt(tokenExpiresAtStr, 10);
      const now = Date.now();
      const timeUntilExpiry = tokenExpiresAt - now;
      
      // If token expires in less than 5 minutes (300000 ms), refresh it
      if (timeUntilExpiry < 300000 && timeUntilExpiry > 0) {
        // console.log(`AuthContext: Token expires in ${timeUntilExpiry/1000} seconds, refreshing proactively`);
        await apiService.refreshToken();
        // console.log('AuthContext: Token refreshed proactively before expiration');
      } else if (timeUntilExpiry <= 0) {
        // console.log('AuthContext: Token has already expired, refreshing now');
        await apiService.refreshToken();
        // console.log('AuthContext: Expired token refreshed');
      } else {
        // console.log(`AuthContext: Token still valid for ${timeUntilExpiry/1000/60} minutes`);
      }
    } catch (error) {
      console.error('AuthContext: Error checking/refreshing token:', error);
      
      // Check for specific error types
      if (error instanceof Error) {
        if (error.message === 'account_not_found') {
          // console.log('AuthContext: User account not found, redirecting to login');
          // Force logout and redirect
          localStorage.removeItem('accessToken');
          localStorage.removeItem('refreshToken');
          navigate('/login?reason=account_not_found');
        } else if (error.message === 'token_expired') {
          // console.log('AuthContext: Token expired, redirecting to login');
          localStorage.removeItem('accessToken');
          localStorage.removeItem('refreshToken');
          navigate('/login?reason=token_expired');
        } else if (error.message === 'invalid_token_type') {
          // console.log('AuthContext: Invalid token type, redirecting to login');
          localStorage.removeItem('accessToken');
          localStorage.removeItem('refreshToken');
          navigate('/login?reason=invalid_token');
        }
      }
    }
  }, [apiService, navigate]);

  // Add a function to handle activity detection and token refresh
  useEffect(() => {
    let inactivityTimer: NodeJS.Timeout | null = null;
    let tokenRefreshInterval: NodeJS.Timeout | null = null;
    let tokenCheckInterval: NodeJS.Timeout | null = null;
    
    // Function to reset inactivity timer
    const resetInactivityTimer = () => {
      if (inactivityTimer) {
        clearTimeout(inactivityTimer);
      }
      
      // Set a new timer - if user is inactive for 25 minutes, refresh the token proactively
      inactivityTimer = setTimeout(async () => {
        if (user) {
          // console.log('AuthContext: User inactive for 25 minutes, proactively refreshing token');
          try {
            await apiService.refreshToken();
            // console.log('AuthContext: Token proactively refreshed after inactivity');
          } catch (error) {
            console.error('AuthContext: Failed to proactively refresh token:', error);
            
            // Handle specific error types
            if (error instanceof Error) {
              if (error.message === 'account_not_found' ||
                  error.message === 'token_expired' ||
                  error.message === 'invalid_token_type') {
                // console.log('AuthContext: Authentication error during inactivity refresh, redirecting to login');
                localStorage.removeItem('accessToken');
                localStorage.removeItem('refreshToken');
                navigate('/login?reason=session_expired');
              }
            }
          }
        }
      }, 25 * 60 * 1000); // 25 minutes
    };
    
    // Set up event listeners for user activity
    const activityEvents = ['mousedown', 'keydown', 'touchstart', 'scroll'];
    
    // Handler for activity events
    const handleUserActivity = () => {
      resetInactivityTimer();
    };
    
    // Add event listeners
    activityEvents.forEach(event => {
      window.addEventListener(event, handleUserActivity);
    });
    
    // Set up periodic token refresh (every 20 minutes)
    if (user) {
      // console.log('AuthContext: Setting up periodic token refresh');
      tokenRefreshInterval = setInterval(async () => {
        try {
          // console.log('AuthContext: Performing periodic token refresh');
          await apiService.refreshToken();
          // console.log('AuthContext: Periodic token refresh successful');
        } catch (error) {
          console.error('AuthContext: Periodic token refresh failed:', error);
          
          // Handle specific error types
          if (error instanceof Error) {
            if (error.message === 'account_not_found' ||
                error.message === 'token_expired' ||
                error.message === 'invalid_token_type') {
              // console.log('AuthContext: Authentication error during periodic refresh, redirecting to login');
              localStorage.removeItem('accessToken');
              localStorage.removeItem('refreshToken');
              navigate('/login?reason=session_expired');
            }
          }
        }
      }, 20 * 60 * 1000); // 20 minutes
      
      // Set up token expiration check (every 1 minute)
      // console.log('AuthContext: Setting up token expiration check');
      tokenCheckInterval = setInterval(() => {
        checkAndRefreshToken();
      }, 60 * 1000); // 1 minute
      
      // Initial checks
      checkAndRefreshToken();
      resetInactivityTimer();
    }
    
    // Cleanup
    return () => {
      activityEvents.forEach(event => {
        window.removeEventListener(event, handleUserActivity);
      });
      
      if (inactivityTimer) {
        clearTimeout(inactivityTimer);
      }
      
      if (tokenRefreshInterval) {
        clearInterval(tokenRefreshInterval);
      }
      
      if (tokenCheckInterval) {
        clearInterval(tokenCheckInterval);
      }
    };
  }, [user, apiService, checkAndRefreshToken]);

  useEffect(() => {
    const initAuth = async () => {
      try {
        // console.log('AuthContext: Initializing authentication');
        const token = localStorage.getItem('accessToken');
        
        if (!token) {
          // console.log('AuthContext: No access token found, user is not logged in');
          setIsLoading(false);
          return;
        }
        
        // console.log('AuthContext: Found access token, checking expiration');
        
        // Check if token is expired or about to expire
        const tokenExpiresAtStr = localStorage.getItem('tokenExpiresAt');
        if (tokenExpiresAtStr) {
          const tokenExpiresAt = parseInt(tokenExpiresAtStr, 10);
          const now = Date.now();
          const timeUntilExpiry = tokenExpiresAt - now;
          
          // console.log(`AuthContext: Token expires in ${timeUntilExpiry/1000} seconds`);
          
          // If token expires in less than 5 minutes or has expired, refresh it
          if (timeUntilExpiry < 300000) {
            // console.log('AuthContext: Token is expired or about to expire, refreshing');
            try {
              await apiService.refreshToken();
              // console.log('AuthContext: Token refreshed successfully during initialization');
            } catch (refreshError) {
              console.warn('AuthContext: Token refresh failed during initialization:', refreshError);
              
              // Handle specific error types
              if (refreshError instanceof Error) {
                if (refreshError.message === 'account_not_found' ||
                    refreshError.message === 'token_expired' ||
                    refreshError.message === 'invalid_token_type') {
                  console.error('AuthContext: Authentication error during initialization refresh, user needs to login again');
                  localStorage.removeItem('accessToken');
                  localStorage.removeItem('refreshToken');
                  setIsLoading(false);
                  return;
                }
              }
              
              // If refresh fails and token is already expired, we might need to redirect to login
              if (timeUntilExpiry <= 0) {
                console.error('AuthContext: Token is expired and refresh failed, user needs to login again');
                localStorage.removeItem('accessToken');
                localStorage.removeItem('refreshToken');
                setIsLoading(false);
                return;
              }
            }
          }
        } else {
          // No expiration time found, try to refresh token anyway
          // console.log('AuthContext: No token expiration time found, attempting to refresh token');
          try {
            await apiService.refreshToken();
            // console.log('AuthContext: Token refreshed successfully');
          } catch (refreshError) {
            console.warn('AuthContext: Token refresh failed during initialization, will try with existing token:', refreshError);
            
            // Handle specific error types
            if (refreshError instanceof Error) {
              if (refreshError.message === 'account_not_found' ||
                  refreshError.message === 'token_expired' ||
                  refreshError.message === 'invalid_token_type') {
                console.error('AuthContext: Authentication error during initialization refresh, user needs to login again');
                localStorage.removeItem('accessToken');
                localStorage.removeItem('refreshToken');
                setIsLoading(false);
                return;
              }
            }
            
            // Continue with the existing token - the user data fetch will fail if it's invalid
          }
        }
        
        // Set a timeout for the user data fetch to prevent hanging
        const fetchUserPromise = apiService.get<User>('/api/v1/auth/me');
        const timeoutPromise = new Promise<never>((_, reject) => {
          setTimeout(() => {
            reject(new Error('User data fetch timeout after 10 seconds'));
          }, 10000); // Increased timeout for better reliability
        });
        
        try {
          // Race between the fetch and the timeout
          const response = await Promise.race([fetchUserPromise, timeoutPromise]);
          // console.log('AuthContext: User data retrieved:', response);
          setUser(response);
          
          // Initialize user settings for the logged-in user
          // console.log(`AuthContext: Initializing user settings for user ID: ${response.id}`);
          try {
            await userSettingsInitService.initializeUserSettings(response.id);
            // console.log('AuthContext: User settings initialized successfully');
          } catch (settingsError) {
            console.error('AuthContext: Error initializing user settings:', settingsError);
          }
          
          // Initialize navigation routes and components
          // console.log(`AuthContext: Initializing navigation for user ID: ${response.id}`);
          try {
            await userNavigationInitService.initializeUserNavigation(response.id);
            // console.log('AuthContext: Navigation initialized successfully');
          } catch (navigationError) {
            console.error('AuthContext: Error initializing navigation:', navigationError);
          }
        } catch (error) {
          console.error('AuthContext: Error fetching user data:', error);
          
          // Check if this is an authentication error (401)
          const fetchError = error as any; // Type assertion for error handling
          if (fetchError.response && fetchError.response.status === 401) {
            console.error('AuthContext: Authentication error (401) when fetching user data');
            // Clear tokens if user data fetch fails due to authentication
            localStorage.removeItem('accessToken');
            localStorage.removeItem('refreshToken');
          } else {
            console.error('AuthContext: Non-authentication error when fetching user data:',
              fetchError.message || 'Unknown error');
            // For non-authentication errors, we might want to retry or handle differently
          }
        }
      } catch (error) {
        console.error('AuthContext: Auth initialization error:', error);
        localStorage.removeItem('accessToken');
        localStorage.removeItem('refreshToken');
      } finally {
        setIsLoading(false);
        // console.log('AuthContext: Authentication initialization completed');
      }
    };

    initAuth();
  }, [userSettingsInitService, userNavigationInitService, apiService]);

  const login = async (email: string, password: string) => {
    try {
      // console.log('AuthContext: Attempting login');
      const response = await apiService.login({ email, password });
      // console.log('AuthContext: Login successful, user:', response.user);
      setUser(response.user);
      
      // Initialize user settings after successful login
      // console.log(`AuthContext: Initializing user settings for user ID: ${response.user.id}`);
      try {
        await userSettingsInitService.initializeUserSettings(response.user.id);
        // console.log('AuthContext: User settings initialized successfully after login');
      } catch (settingsError) {
        console.error('AuthContext: Error initializing user settings after login:', settingsError);
      }
      
      // Initialize navigation routes and components after login
      // console.log(`AuthContext: Initializing navigation for user ID: ${response.user.id}`);
      try {
        await userNavigationInitService.initializeUserNavigation(response.user.id);
        // console.log('AuthContext: Navigation initialized successfully after login');
      } catch (navigationError) {
        console.error('AuthContext: Error initializing navigation after login:', navigationError);
      }
      
      // Navigation is handled by the calling component
    } catch (error) {
      console.error('AuthContext: Login error:', error);
      throw error;
    }
  };

  const register = async (userData: { username: string; email: string; password: string; full_name: string }) => {
    try {
      await apiService.post('/api/v1/auth/register', userData);
      // After registration, log the user in
      await login(userData.email, userData.password);
    } catch (error) {
      console.error('Registration error:', error);
      throw error;
    }
  };

  const logout = async () => {
    try {
      // console.log('AuthContext: Logging out user');
      
      // This will clear the refresh token cookie on the server
      await apiService.post('/api/v1/auth/logout', {}, {
        withCredentials: true,
        // Add a shorter timeout for logout to prevent hanging
        timeout: 5000
      });
      // console.log('AuthContext: Server-side logout successful');
    } catch (error) {
      console.error('AuthContext: Logout error:', error);
      // Even if server-side logout fails, we should still clear local state
      // console.log('AuthContext: Continuing with client-side logout despite server error');
    } finally {
      // Clear tokens from localStorage
      // console.log('AuthContext: Clearing tokens from localStorage');
      localStorage.removeItem('accessToken');
      localStorage.removeItem('refreshToken');
      
      // Clear user state
      setUser(null);
      
      // Redirect to login page
      // console.log('AuthContext: Redirecting to login page');
      navigate('/login');
    }
  };

  const value = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
    register,
    logout
  };

  if (isLoading) {
    const isDark = themeService.getCurrentTheme() === 'dark';
    const loadingBackground = isDark ? '#0f172a' : '#f5f5f5';
    const loadingTextPrimary = isDark ? '#f1f5f9' : '#000000';
    const loadingTextSecondary = isDark ? '#94a3b8' : '#666666';

    return (
      <div style={{
        backgroundColor: loadingBackground,
        color: loadingTextPrimary,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        flexDirection: 'column',
        gap: '16px'
      }}>
        <div style={{ fontSize: '18px', fontWeight: 'bold' }}>Loading...</div>
        <div style={{ fontSize: '14px', color: loadingTextSecondary }}>Authenticating your session</div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export default AuthContext;
