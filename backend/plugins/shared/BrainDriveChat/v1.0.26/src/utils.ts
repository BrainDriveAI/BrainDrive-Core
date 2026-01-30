/**
 * Generate a unique ID with a prefix
 */
export function generateId(prefix: string = 'id'): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Extract text content from various AI response data formats
 */
export function extractTextFromData(data: any): string {
  if (!data) return '';
  
  // Handle string responses
  if (typeof data === 'string') {
    return data;
  }
  
  // Handle object responses with various text fields
  if (typeof data === 'object') {
    // Priority order for text extraction - 'text' field first as it's what our API returns
    if (data.text && typeof data.text === 'string') {
      return data.text;
    }
    
    // Handle choices array format (OpenAI-style)
    if (data.choices && Array.isArray(data.choices) && data.choices.length > 0) {
      const choice = data.choices[0];
      if (choice.delta && choice.delta.content) {
        return choice.delta.content;
      }
      if (choice.message && choice.message.content) {
        return choice.message.content;
      }
      if (choice.text) {
        return choice.text;
      }
    }
    
    // Handle other common text fields
    const textFields = [
      'content',
      'message',
      'response',
      'output',
      'result',
      'answer'
    ];
    
    for (const field of textFields) {
      if (data[field] && typeof data[field] === 'string') {
        return data[field];
      }
    }
    
    // Handle streaming delta format
    if (data.delta && data.delta.content) {
      return data.delta.content;
    }
    
    // Handle message format
    if (data.message && data.message.content) {
      return data.message.content;
    }
  }
  
  return '';
}

/**
 * Format timestamp for display
 */
export function formatTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (error) {
    return '';
  }
}

/**
 * Format relative time (e.g., "2 hours ago")
 */
export function formatRelativeTime(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);
    
    if (diffMins < 1) {
      return 'Just now';
    } else if (diffMins < 60) {
      return `${diffMins} min${diffMins !== 1 ? 's' : ''} ago`;
    } else if (diffHours < 24) {
      return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    } else if (diffDays < 7) {
      return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
    } else {
      return date.toLocaleDateString();
    }
  } catch (error) {
    return '';
  }
}

/**
 * Debounce function to limit rapid function calls
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout>;
  return (...args: Parameters<T>) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), wait);
  };
}

/**
 * Throttle function to limit function calls to once per interval
 */
export function throttle<T extends (...args: any[]) => any>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle: boolean;
  return (...args: Parameters<T>) => {
    if (!inThrottle) {
      func(...args);
      inThrottle = true;
      setTimeout(() => inThrottle = false, limit);
    }
  };
}

/**
 * Safely parse JSON with fallback
 */
export function safeJsonParse(jsonString: string, fallback: any = null): any {
  try {
    return JSON.parse(jsonString);
  } catch (error) {
    return fallback;
  }
}

/**
 * Check if a string is a valid URL
 */
export function isValidUrl(string: string): boolean {
  try {
    new URL(string);
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Truncate text to a specified length with ellipsis
 */
export function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return text.substring(0, maxLength - 3) + '...';
}

/**
 * Escape HTML characters to prevent XSS
 */
export function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Get a CSS custom property value
 */
export function getCssVariable(variableName: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(variableName)
    .trim();
}

/**
 * Set a CSS custom property value
 */
export function setCssVariable(variableName: string, value: string): void {
  document.documentElement.style.setProperty(variableName, value);
}

/**
 * Check if the current theme is dark
 */
export function isDarkTheme(): boolean {
  const theme = getCssVariable('--bg-color');
  // Dark themes typically have dark background colors
  return theme.includes('#1') || theme.includes('#2') || theme.includes('rgb(1') || theme.includes('rgb(2');
}