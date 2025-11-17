import { AbstractBaseService } from './base/BaseService';

export type Theme = 'light' | 'dark';

type ThemeChangeListener = (theme: Theme) => void;

class ThemeService extends AbstractBaseService {
  private currentTheme: Theme;
  private listeners: ThemeChangeListener[] = [];
  private static instance: ThemeService;

  private constructor() {
    super(
      'theme',
      { major: 1, minor: 0, patch: 0 },
      [
        {
          name: 'theme-management',
          description: 'Theme switching and management capabilities',
          version: '1.0.0'
        },
        {
          name: 'theme-events',
          description: 'Theme change event subscription system',
          version: '1.0.0'
        }
      ]
    );
    
    // Default to dark so unauthenticated experiences (login/register) render in dark mode before user prefs load
    this.currentTheme = 'dark';
  }

  public static getInstance(): ThemeService {
    if (!ThemeService.instance) {
      ThemeService.instance = new ThemeService();
    }
    return ThemeService.instance;
  }

  async initialize(): Promise<void> {
    // Apply the initial theme
    this.applyTheme(this.currentTheme);
    
    // Set up media query listener for system theme changes
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', (e) => {
      // We don't have a useSystemTheme preference yet, so we'll just log this for now
      // console.log('System theme preference changed:', e.matches ? 'dark' : 'light');
      // In the future, we could add a useSystemTheme preference to UserPreferencesData
    });
  }

  async destroy(): Promise<void> {
    // Clean up all listeners
    this.listeners = [];
  }

  getCurrentTheme(): Theme {
    return this.currentTheme;
  }

  setTheme(theme: Theme): void {
    // console.log(`ThemeService.setTheme called with theme: ${theme}`);
    
    if (this.currentTheme === theme) {
      // console.log(`Theme is already set to ${theme}, no change needed`);
      return;
    }
    
    // console.log(`Changing theme from ${this.currentTheme} to ${theme}`);
    this.currentTheme = theme;
    this.applyTheme(theme);
    
    // Notify listeners
    this.notifyListeners();
    // console.log(`Theme changed to ${theme}, listeners notified`);
  }

  toggleTheme(): void {
    const newTheme = this.currentTheme === 'light' ? 'dark' : 'light';
    this.setTheme(newTheme);
  }

  addThemeChangeListener(listener: ThemeChangeListener): void {
    if (!this.listeners.includes(listener)) {
      this.listeners.push(listener);
    }
  }

  removeThemeChangeListener(listener: ThemeChangeListener): void {
    const index = this.listeners.indexOf(listener);
    if (index > -1) {
      this.listeners.splice(index, 1);
    }
  }

  private notifyListeners(): void {
    this.listeners.forEach(listener => {
      try {
        listener(this.currentTheme);
      } catch (error) {
        console.error('Error in theme change listener:', error);
      }
    });
  }

  private applyTheme(theme: Theme): void {
    // console.log(`Applying theme to DOM: ${theme}`);
    
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.body.classList.add('dark-scrollbars');
      // console.log('Added dark mode classes to document');
    } else {
      document.documentElement.classList.remove('dark');
      document.body.classList.remove('dark-scrollbars');
      // console.log('Removed dark mode classes from document');
    }
  }
}

// Export a singleton instance
export const themeService = ThemeService.getInstance();
