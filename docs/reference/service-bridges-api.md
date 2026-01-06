# Service Bridges API Reference

Service Bridges are TypeScript interfaces that plugins use to interact with BrainDrive from frontend React components. They provide a clean abstraction over the backend REST API.

## Overview

When a plugin component is rendered, it receives Service Bridges via props or context. These bridges handle authentication, error handling, and data transformation automatically.

```typescript
// Example: Using Service Bridges in a plugin component
function MyPluginComponent({ bridges }) {
  const { api, settings, events, theme, pageContext, pluginState } = bridges;

  // Use bridges to interact with BrainDrive
  const data = await api.get('/some-endpoint');
}
```

## Available Bridges

| Bridge | Purpose |
|--------|---------|
| [API Bridge](#api-bridge) | HTTP requests to backend |
| [Event Bridge](#event-bridge) | Cross-plugin event communication |
| [Theme Bridge](#theme-bridge) | Theme/styling information |
| [Settings Bridge](#settings-bridge) | Read/write plugin settings |
| [PageContext Bridge](#pagecontext-bridge) | Current page information |
| [PluginState Bridge](#pluginstate-bridge) | Persist plugin state |

---

## API Bridge

The API Bridge provides methods for making HTTP requests to the BrainDrive backend. It automatically handles JWT authentication and error responses.

### Methods

```typescript
interface APIBridge {
  get<T>(endpoint: string, options?: RequestOptions): Promise<T>;
  post<T>(endpoint: string, data: any, options?: RequestOptions): Promise<T>;
  put<T>(endpoint: string, data: any, options?: RequestOptions): Promise<T>;
  patch<T>(endpoint: string, data: any, options?: RequestOptions): Promise<T>;
  delete<T>(endpoint: string, options?: RequestOptions): Promise<T>;
  postStreaming(
    endpoint: string,
    data: any,
    onChunk: (chunk: string) => void,
    options?: StreamingOptions
  ): Promise<void>;
}

interface RequestOptions {
  headers?: Record<string, string>;
  params?: Record<string, string | number>;
  timeout?: number;
}

interface StreamingOptions extends RequestOptions {
  onError?: (error: Error) => void;
  onComplete?: () => void;
}
```

### Usage Examples

```typescript
// GET request
const pages = await api.get<PageResponse[]>('/api/v1/pages');

// POST request
const newPage = await api.post<PageResponse>('/api/v1/pages', {
  name: 'My Page',
  route: '/my-page',
  content: { layout: {} }
});

// Streaming response (for AI chat)
await api.postStreaming(
  '/api/v1/ai/providers/chat',
  { model: 'llama2', messages: [...], stream: true },
  (chunk) => {
    // Handle each streamed chunk
    console.log('Received:', chunk);
  }
);
```

---

## Event Bridge

The Event Bridge enables communication between plugins and core components through a pub/sub event system.

### Methods

```typescript
interface EventBridge {
  emit(eventName: string, payload?: any): void;
  on(eventName: string, handler: (payload: any) => void): () => void;
  off(eventName: string, handler: (payload: any) => void): void;
  once(eventName: string, handler: (payload: any) => void): void;
}
```

### Usage Examples

```typescript
// Emit an event
events.emit('my-plugin:data-updated', { id: '123', value: 'new' });

// Subscribe to an event (returns unsubscribe function)
const unsubscribe = events.on('page:navigated', (payload) => {
  console.log('Navigated to:', payload.route);
});

// Later: unsubscribe
unsubscribe();

// One-time listener
events.once('user:logged-in', (user) => {
  console.log('Welcome,', user.name);
});
```

### Event Naming Convention

Use namespaced event names: `{source}:{action}`

- `page:navigated` - Page navigation occurred
- `theme:changed` - Theme was switched
- `plugin:{plugin-id}:custom-event` - Plugin-specific events

---

## Theme Bridge

The Theme Bridge provides access to the current theme and theme change notifications.

### Methods

```typescript
interface ThemeBridge {
  getCurrentTheme(): Theme;
  addThemeChangeListener(callback: (theme: Theme) => void): () => void;
  removeThemeChangeListener(callback: (theme: Theme) => void): void;
}

interface Theme {
  mode: 'light' | 'dark';
  colors: {
    primary: string;
    secondary: string;
    background: string;
    surface: string;
    text: string;
    // ... additional color tokens
  };
  // Additional theme properties
}
```

### Usage Examples

```typescript
// Get current theme
const theme = theme.getCurrentTheme();
console.log('Current mode:', theme.mode);

// React to theme changes
const unsubscribe = theme.addThemeChangeListener((newTheme) => {
  document.body.style.backgroundColor = newTheme.colors.background;
});
```

---

## Settings Bridge

The Settings Bridge provides read/write access to plugin settings.

### Methods

```typescript
interface SettingsBridge {
  getSetting<T>(key: string, defaultValue?: T): Promise<T>;
  setSetting<T>(key: string, value: T): Promise<void>;
  getSettings(keys: string[]): Promise<Record<string, any>>;
  deleteSetting(key: string): Promise<void>;
  onSettingChange(key: string, callback: (value: any) => void): () => void;
}
```

### Usage Examples

```typescript
// Get a setting with default
const apiKey = await settings.getSetting('openai_api_key', '');

// Set a setting
await settings.setSetting('preferred_model', 'gpt-4');

// Get multiple settings
const config = await settings.getSettings(['api_key', 'model', 'temperature']);

// React to setting changes
const unsubscribe = settings.onSettingChange('theme_preference', (newValue) => {
  applyTheme(newValue);
});
```

---

## PageContext Bridge

The PageContext Bridge provides information about the current page and navigation state.

### Methods

```typescript
interface PageContextBridge {
  getContext(): PageContext;
  onPageContextChange(callback: (context: PageContext) => void): () => void;
  navigate(route: string): void;
}

interface PageContext {
  pageId: string;
  route: string;
  pageName: string;
  parentRoute: string | null;
  isPublished: boolean;
  components: ComponentInstance[];
  // Additional page metadata
}
```

### Usage Examples

```typescript
// Get current page context
const context = pageContext.getContext();
console.log('Current page:', context.pageName);

// Navigate to another page
pageContext.navigate('/dashboard');

// React to page changes
pageContext.onPageContextChange((newContext) => {
  // Reload data for new page
  loadDataForPage(newContext.pageId);
});
```

---

## PluginState Bridge

The PluginState Bridge provides persistent state storage for plugins. State can be scoped to the plugin, a specific page, or with custom keys.

### Methods

```typescript
interface PluginStateBridge {
  save(data: any, options?: StateOptions): Promise<string>;
  load(options?: StateOptions): Promise<any>;
  clear(options?: StateOptions): Promise<void>;
  list(options?: ListOptions): Promise<StateEntry[]>;
}

interface StateOptions {
  pageId?: string;           // Scope to specific page
  stateKey?: string;         // Custom state key
  strategy?: 'persistent' | 'session' | 'none' | 'custom';
  ttl?: number;              // Time-to-live in seconds
}

interface ListOptions {
  pageId?: string;
  limit?: number;
  offset?: number;
}

interface StateEntry {
  id: string;
  stateKey: string;
  stateData: any;
  createdAt: string;
  updatedAt: string;
}
```

### Usage Examples

```typescript
// Save plugin state (auto-scoped to plugin)
const stateId = await pluginState.save({
  lastQuery: 'search term',
  results: [...]
});

// Load plugin state
const savedState = await pluginState.load();

// Save page-specific state
await pluginState.save(
  { scrollPosition: 150 },
  { pageId: context.pageId, stateKey: 'scroll' }
);

// Load page-specific state
const pageState = await pluginState.load({
  pageId: context.pageId,
  stateKey: 'scroll'
});

// Clear state
await pluginState.clear();

// List all states for this plugin
const allStates = await pluginState.list({ limit: 10 });
```

### State Strategies

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `persistent` | Stored until cleared | User preferences, saved data |
| `session` | Cleared on logout | Temporary workspace state |
| `none` | Not persisted | Fire-and-forget state |
| `custom` | Reserved for custom strategies | Advanced storage workflows |

---

## Notes for Developers

Service Bridge interfaces are governed by the BrainDrive backend and frontend. If you see a mismatch, check the BrainDrive Core frontend definitions and the OpenAPI spec and update this reference to match.

### Accessing Bridges in Your Plugin

Bridges are typically provided via React context or props:

```typescript
import { useBridges } from '@braindrive/plugin-sdk';

function MyComponent() {
  const { api, events, settings, theme, pageContext, pluginState } = useBridges();
  // ...
}
```

Or via props in class components:

```typescript
class MyComponent extends React.Component<BridgeProps> {
  async componentDidMount() {
    const data = await this.props.bridges.api.get('/api/v1/data');
  }
}
```
