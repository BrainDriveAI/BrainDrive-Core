import type {
  RagCollection,
  RagContextRetrievalResult,
  RagCreateCollectionInput,
  RagDocument,
  Services,
} from '../types';

const RAG_SETTINGS_DEFINITION_ID = 'braindrive_rag_service_settings';
const DEFAULT_RAG_CHAT_BASE_URL = 'http://localhost:18000';

type RagServiceSettings = {
  document_chat?: {
    enabled?: boolean;
    protocol?: string;
    host?: string;
    port?: number;
  };
};

type RagChatHistoryTurn = { role: 'user' | 'assistant'; content: string };

type RagSearchConfig = {
  use_chat_history?: boolean;
  max_history_turns?: number;
  top_k?: number;
  use_hybrid?: boolean;
  alpha?: number;
  use_intent_classification?: boolean;
  query_transformation?: {
    enabled: boolean;
    methods?: string[];
  };
  filters?: {
    min_similarity?: number;
  };
};

type RagQuery = {
  query_text: string;
  collection_id: string;
  chat_history?: RagChatHistoryTurn[];
  config: RagSearchConfig;
};

function normalizeBaseUrl(url: string): string {
  return (url || '').trim().replace(/\/+$/, '');
}

function unwrapSettingValue(raw: any): any {
  if (!raw) return null;
  if (typeof raw !== 'object') return raw;

  if ('data' in raw && raw.data) return raw.data;
  if ('value' in raw && raw.value) return raw.value;

  return raw;
}

function extractErrorMessage(body: unknown): string | null {
  if (!body) return null;

  if (typeof body === 'string') return body;

  if (typeof body !== 'object') return String(body);

  const asAny = body as any;
  if (typeof asAny.message === 'string' && asAny.message.trim()) return asAny.message;
  if (typeof asAny.error === 'string' && asAny.error.trim()) return asAny.error;
  if (typeof asAny.detail === 'string' && asAny.detail.trim()) return asAny.detail;

  const detail = asAny.detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        const msg = (item as any).msg;
        return typeof msg === 'string' ? msg : null;
      })
      .filter(Boolean) as string[];
    if (messages.length) return messages.join('; ');
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }

  try {
    return JSON.stringify(body);
  } catch {
    return String(body);
  }
}

export class RagService {
  private services: Services;
  private baseUrlPromise: Promise<string> | null = null;
  private cachedBaseUrl: string | null = null;

  constructor(services: Services) {
    this.services = services;
  }

  private async tryGetSettingsDefinition(definitionId: string): Promise<any | null> {
    const settingsSvc: any = this.services?.settings;
    if (!settingsSvc) return null;

    const getter = settingsSvc.getSetting || settingsSvc.get;
    if (typeof getter !== 'function') return null;

    try {
      return await getter.call(settingsSvc, definitionId, { userId: 'current' });
    } catch {
      try {
        return await getter.call(settingsSvc, definitionId);
      } catch {
        return null;
      }
    }
  }

  async resolveBaseUrl(): Promise<string> {
    if (this.cachedBaseUrl) return this.cachedBaseUrl;
    if (this.baseUrlPromise) return this.baseUrlPromise;

    this.baseUrlPromise = (async () => {
      const raw = await this.tryGetSettingsDefinition(RAG_SETTINGS_DEFINITION_ID);
      const unwrapped = unwrapSettingValue(raw);
      const parsed = typeof unwrapped === 'string' ? JSON.parse(unwrapped) : unwrapped;
      const settings: RagServiceSettings | null = parsed && typeof parsed === 'object' ? parsed : null;

      const docChat = settings?.document_chat;
      const protocol = (docChat?.protocol || 'http').toString();
      const host = (docChat?.host || 'localhost').toString();
      const port = Number.isFinite(Number(docChat?.port)) ? Number(docChat?.port) : 18000;
      const baseUrl = normalizeBaseUrl(`${protocol}://${host}:${port}`);

      this.cachedBaseUrl = baseUrl || DEFAULT_RAG_CHAT_BASE_URL;
      return this.cachedBaseUrl;
    })()
      .catch((_error) => {
        this.cachedBaseUrl = DEFAULT_RAG_CHAT_BASE_URL;
        return this.cachedBaseUrl;
      })
      .finally(() => {
        this.baseUrlPromise = null;
      });

    return this.baseUrlPromise;
  }

  private async buildUrl(path: string): Promise<string> {
    const baseUrl = await this.resolveBaseUrl();
    const normalizedBase = normalizeBaseUrl(baseUrl);
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${normalizedBase}${normalizedPath}`;
  }

  private async fetchJson<T>(url: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
    const timeoutMs = init.timeoutMs ?? 30_000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(url, {
        ...init,
        signal: controller.signal,
        headers: {
          ...(init.headers || {}),
          ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        },
      });

      if (!response.ok) {
        let message = `HTTP ${response.status} ${response.statusText}`;
        try {
          const maybeJson = await response.json();
          const extracted = extractErrorMessage(maybeJson);
          if (extracted) message = extracted;
        } catch {
          try {
            const text = await response.text();
            if (text) message = text;
          } catch {
            // ignore
          }
        }
        throw new Error(message);
      }

      // DELETE endpoints may return 204 no-content
      if (response.status === 204) {
        return {} as T;
      }

      return (await response.json()) as T;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async listCollections(): Promise<RagCollection[]> {
    const url = await this.buildUrl('/collections/');
    return this.fetchJson<RagCollection[]>(url, { method: 'GET' });
  }

  async createCollection(payload: RagCreateCollectionInput): Promise<RagCollection> {
    const url = await this.buildUrl('/collections/');
    return this.fetchJson<RagCollection>(url, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async listDocuments(collectionId: string): Promise<RagDocument[]> {
    const url = await this.buildUrl(`/documents/?collection_id=${encodeURIComponent(collectionId)}`);
    return this.fetchJson<RagDocument[]>(url, { method: 'GET' });
  }

  async uploadDocument(collectionId: string, file: File): Promise<RagDocument> {
    const url = await this.buildUrl('/documents/');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('collection_id', collectionId);

    return this.fetchJson<RagDocument>(url, {
      method: 'POST',
      body: formData,
      timeoutMs: 120_000,
    });
  }

  async getDocument(documentId: string): Promise<RagDocument> {
    const url = await this.buildUrl(`/documents/${encodeURIComponent(documentId)}`);
    return this.fetchJson<RagDocument>(url, { method: 'GET' });
  }

  async deleteDocument(documentId: string): Promise<void> {
    const url = await this.buildUrl(`/documents/${encodeURIComponent(documentId)}`);
    await this.fetchJson<void>(url, { method: 'DELETE' });
  }

  async retrieveContext(
    queryText: string,
    collectionId: string,
    chatHistory: RagChatHistoryTurn[] = [],
    configOverrides: Partial<RagSearchConfig> = {}
  ): Promise<RagContextRetrievalResult> {
    const url = await this.buildUrl('/search/');

    const query: RagQuery = {
      query_text: queryText,
      collection_id: collectionId,
      chat_history: chatHistory,
      config: {
        use_chat_history: true,
        max_history_turns: 3,
        top_k: 7,
        use_hybrid: true,
        alpha: 0.5,
        use_intent_classification: true,
        query_transformation: { enabled: true, methods: [] },
        ...configOverrides,
      },
    };

    return this.fetchJson<RagContextRetrievalResult>(url, {
      method: 'POST',
      body: JSON.stringify(query),
      timeoutMs: 60_000,
    });
  }
}
