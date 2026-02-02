import {
  CacheService as ICacheService,
  CacheInfo
} from '../types/published';

interface CacheConfig {
  enabled: boolean;
  defaultTTL: number; // milliseconds
  maxMemorySize: number; // bytes
  maxLocalStorageSize: number; // bytes
  maxIndexedDBSize: number; // bytes
  compressionEnabled: boolean;
  enableDebug: boolean;
}

interface CacheEntry<T = any> {
  key: string;
  value: T;
  timestamp: Date;
  expiresAt: Date;
  ttl: number;
  size: number;
  compressed: boolean;
  hitCount: number;
  missCount: number;
}

type CacheLevel = 'memory' | 'localStorage' | 'indexedDB' | 'http';

/**
 * CacheManager - Multi-level caching system with memory, localStorage, and IndexedDB support
 */
export class CacheManager implements ICacheService {
  private config: CacheConfig;
  private memoryCache = new Map<string, CacheEntry>();
  private memorySize = 0;
  private dbName = 'unified-page-renderer-cache';
  private dbVersion = 1;
  private db: IDBDatabase | null = null;

  constructor(config: Partial<CacheConfig> = {}) {
    this.config = {
      enabled: true,
      defaultTTL: 3600000, // 1 hour
      maxMemorySize: 50 * 1024 * 1024, // 50MB
      maxLocalStorageSize: 10 * 1024 * 1024, // 10MB
      maxIndexedDBSize: 100 * 1024 * 1024, // 100MB
      compressionEnabled: true,
      enableDebug: false,
      ...config
    };

    if (this.config.enabled) {
      this.initializeIndexedDB();
      this.startCleanupTimer();
    }
  }

  /**
   * Get value from cache
   */
  async get<T>(key: string): Promise<T | null> {
    if (!this.config.enabled) return null;

    try {
      // Try memory cache first
      const memoryResult = this.getFromMemory<T>(key);
      if (memoryResult !== null) {
        if (this.config.enableDebug) {
          console.log(`[Cache] Memory hit for key: ${key}`);
        }
        return memoryResult;
      }

      // Try localStorage
      const localStorageResult = await this.getFromLocalStorage<T>(key);
      if (localStorageResult !== null) {
        // Promote to memory cache
        await this.setInMemory(key, localStorageResult, this.config.defaultTTL);
        if (this.config.enableDebug) {
          console.log(`[Cache] localStorage hit for key: ${key}`);
        }
        return localStorageResult;
      }

      // Try IndexedDB
      const indexedDBResult = await this.getFromIndexedDB<T>(key);
      if (indexedDBResult !== null) {
        // Promote to memory and localStorage
        await this.setInMemory(key, indexedDBResult, this.config.defaultTTL);
        await this.setInLocalStorage(key, indexedDBResult, this.config.defaultTTL);
        if (this.config.enableDebug) {
          console.log(`[Cache] IndexedDB hit for key: ${key}`);
        }
        return indexedDBResult;
      }

      if (this.config.enableDebug) {
        console.log(`[Cache] Miss for key: ${key}`);
      }
      return null;

    } catch (error) {
      console.error('[Cache] Error getting value:', error);
      return null;
    }
  }

  /**
   * Set value in cache
   */
  async set<T>(key: string, value: T, ttl?: number): Promise<void> {
    if (!this.config.enabled) return;

    const actualTTL = ttl || this.config.defaultTTL;

    try {
      // Set in all cache levels
      await Promise.all([
        this.setInMemory(key, value, actualTTL),
        this.setInLocalStorage(key, value, actualTTL),
        this.setInIndexedDB(key, value, actualTTL)
      ]);

      if (this.config.enableDebug) {
        console.log(`[Cache] Set value for key: ${key}, TTL: ${actualTTL}ms`);
      }

    } catch (error) {
      console.error('[Cache] Error setting value:', error);
    }
  }

  /**
   * Delete value from cache
   */
  async delete(key: string): Promise<void> {
    if (!this.config.enabled) return;

    try {
      // Delete from all cache levels
      await Promise.all([
        this.deleteFromMemory(key),
        this.deleteFromLocalStorage(key),
        this.deleteFromIndexedDB(key)
      ]);

      if (this.config.enableDebug) {
        console.log(`[Cache] Deleted key: ${key}`);
      }

    } catch (error) {
      console.error('[Cache] Error deleting value:', error);
    }
  }

  /**
   * Clear all cache
   */
  async clear(): Promise<void> {
    if (!this.config.enabled) return;

    try {
      // Clear all cache levels
      await Promise.all([
        this.clearMemory(),
        this.clearLocalStorage(),
        this.clearIndexedDB()
      ]);

      if (this.config.enableDebug) {
        console.log('[Cache] Cleared all cache');
      }

    } catch (error) {
      console.error('[Cache] Error clearing cache:', error);
    }
  }

  /**
   * Get cache info for a key
   */
  async getInfo(key: string): Promise<CacheInfo | null> {
    if (!this.config.enabled) return null;

    try {
      // Check memory cache first
      const memoryEntry = this.memoryCache.get(key);
      if (memoryEntry && !this.isExpired(memoryEntry)) {
        return this.createCacheInfo(memoryEntry, 'memory');
      }

      // Check localStorage
      const localStorageEntry = this.getLocalStorageEntry(key);
      if (localStorageEntry && !this.isExpired(localStorageEntry)) {
        return this.createCacheInfo(localStorageEntry, 'localStorage');
      }

      // Check IndexedDB
      const indexedDBEntry = await this.getIndexedDBEntry(key);
      if (indexedDBEntry && !this.isExpired(indexedDBEntry)) {
        return this.createCacheInfo(indexedDBEntry, 'indexedDB');
      }

      return null;

    } catch (error) {
      console.error('[Cache] Error getting cache info:', error);
      return null;
    }
  }

  /**
   * Invalidate cache entries matching a pattern
   */
  async invalidate(pattern: string): Promise<void> {
    if (!this.config.enabled) return;

    try {
      const regex = new RegExp(pattern);

      // Invalidate memory cache
      for (const key of this.memoryCache.keys()) {
        if (regex.test(key)) {
          await this.deleteFromMemory(key);
        }
      }

      // Invalidate localStorage
      const localStorageKeys = this.getLocalStorageKeys();
      for (const key of localStorageKeys) {
        if (regex.test(key)) {
          await this.deleteFromLocalStorage(key.replace('cache_', ''));
        }
      }

      // Invalidate IndexedDB
      const indexedDBKeys = await this.getIndexedDBKeys();
      for (const key of indexedDBKeys) {
        if (regex.test(key)) {
          await this.deleteFromIndexedDB(key);
        }
      }

      if (this.config.enableDebug) {
        console.log(`[Cache] Invalidated entries matching pattern: ${pattern}`);
      }

    } catch (error) {
      console.error('[Cache] Error invalidating cache:', error);
    }
  }

  /**
   * Get cache statistics
   */
  getStats(): {
    memorySize: number;
    memoryEntries: number;
    localStorageSize: number;
    localStorageEntries: number;
    totalHits: number;
    totalMisses: number;
    hitRate: number;
  } {
    let totalHits = 0;
    let totalMisses = 0;
    let localStorageSize = 0;
    let localStorageEntries = 0;

    // Calculate memory stats
    for (const entry of this.memoryCache.values()) {
      totalHits += entry.hitCount;
      totalMisses += entry.missCount;
    }

    // Calculate localStorage stats
    try {
      const keys = this.getLocalStorageKeys();
      localStorageEntries = keys.length;
      
      for (const key of keys) {
        const item = localStorage.getItem(key);
        if (item) {
          localStorageSize += item.length * 2; // Approximate size in bytes
        }
      }
    } catch (error) {
      console.warn('[Cache] Error calculating localStorage stats:', error);
    }

    const hitRate = totalHits + totalMisses > 0 ? totalHits / (totalHits + totalMisses) : 0;

    return {
      memorySize: this.memorySize,
      memoryEntries: this.memoryCache.size,
      localStorageSize,
      localStorageEntries,
      totalHits,
      totalMisses,
      hitRate
    };
  }

  // Private methods

  private async initializeIndexedDB(): Promise<void> {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, this.dbVersion);

      request.onerror = () => {
        console.error('[Cache] IndexedDB initialization failed');
        resolve(); // Don't reject, just continue without IndexedDB
      };

      request.onsuccess = () => {
        this.db = request.result;
        resolve();
      };

      request.onupgradeneeded = (event) => {
        const db = (event.target as IDBOpenDBRequest).result;
        
        if (!db.objectStoreNames.contains('cache')) {
          const store = db.createObjectStore('cache', { keyPath: 'key' });
          store.createIndex('expiresAt', 'expiresAt', { unique: false });
        }
      };
    });
  }

  private getFromMemory<T>(key: string): T | null {
    const entry = this.memoryCache.get(key);
    
    if (!entry) {
      return null;
    }

    if (this.isExpired(entry)) {
      this.deleteFromMemory(key);
      entry.missCount++;
      return null;
    }

    entry.hitCount++;
    return entry.value as T;
  }

  private async setInMemory<T>(key: string, value: T, ttl: number): Promise<void> {
    const size = this.calculateSize(value);
    const now = new Date();
    const expiresAt = new Date(now.getTime() + ttl);

    // Check if we need to evict entries
    if (this.memorySize + size > this.config.maxMemorySize) {
      await this.evictMemoryEntries(size);
    }

    const entry: CacheEntry<T> = {
      key,
      value,
      timestamp: now,
      expiresAt,
      ttl,
      size,
      compressed: false,
      hitCount: 0,
      missCount: 0
    };

    this.memoryCache.set(key, entry);
    this.memorySize += size;
  }

  private async deleteFromMemory(key: string): Promise<void> {
    const entry = this.memoryCache.get(key);
    if (entry) {
      this.memoryCache.delete(key);
      this.memorySize -= entry.size;
    }
  }

  private async clearMemory(): Promise<void> {
    this.memoryCache.clear();
    this.memorySize = 0;
  }

  private async getFromLocalStorage<T>(key: string): Promise<T | null> {
    try {
      const item = localStorage.getItem(`cache_${key}`);
      if (!item) return null;

      const entry: CacheEntry<T> = JSON.parse(item);
      
      if (this.isExpired(entry)) {
        await this.deleteFromLocalStorage(key);
        return null;
      }

      return entry.value;
    } catch (error) {
      console.warn('[Cache] Error reading from localStorage:', error);
      return null;
    }
  }

  private async setInLocalStorage<T>(key: string, value: T, ttl: number): Promise<void> {
    try {
      const now = new Date();
      const expiresAt = new Date(now.getTime() + ttl);
      const size = this.calculateSize(value);

      const entry: CacheEntry<T> = {
        key,
        value,
        timestamp: now,
        expiresAt,
        ttl,
        size,
        compressed: false,
        hitCount: 0,
        missCount: 0
      };

      localStorage.setItem(`cache_${key}`, JSON.stringify(entry));
    } catch (error) {
      console.warn('[Cache] Error writing to localStorage:', error);
    }
  }

  private async deleteFromLocalStorage(key: string): Promise<void> {
    try {
      localStorage.removeItem(`cache_${key}`);
    } catch (error) {
      console.warn('[Cache] Error deleting from localStorage:', error);
    }
  }

  private async clearLocalStorage(): Promise<void> {
    try {
      const keys = this.getLocalStorageKeys();
      for (const key of keys) {
        localStorage.removeItem(key);
      }
    } catch (error) {
      console.warn('[Cache] Error clearing localStorage:', error);
    }
  }

  private async getFromIndexedDB<T>(key: string): Promise<T | null> {
    if (!this.db) return null;

    return new Promise((resolve) => {
      const transaction = this.db!.transaction(['cache'], 'readonly');
      const store = transaction.objectStore('cache');
      const request = store.get(key);

      request.onsuccess = () => {
        const entry = request.result as CacheEntry<T>;
        
        if (!entry || this.isExpired(entry)) {
          if (entry) {
            this.deleteFromIndexedDB(key);
          }
          resolve(null);
          return;
        }

        resolve(entry.value);
      };

      request.onerror = () => {
        console.warn('[Cache] Error reading from IndexedDB');
        resolve(null);
      };
    });
  }

  private async setInIndexedDB<T>(key: string, value: T, ttl: number): Promise<void> {
    if (!this.db) return;

    return new Promise((resolve) => {
      const now = new Date();
      const expiresAt = new Date(now.getTime() + ttl);
      const size = this.calculateSize(value);

      const entry: CacheEntry<T> = {
        key,
        value,
        timestamp: now,
        expiresAt,
        ttl,
        size,
        compressed: false,
        hitCount: 0,
        missCount: 0
      };

      const transaction = this.db!.transaction(['cache'], 'readwrite');
      const store = transaction.objectStore('cache');
      const request = store.put(entry);

      request.onsuccess = () => resolve();
      request.onerror = () => {
        console.warn('[Cache] Error writing to IndexedDB');
        resolve();
      };
    });
  }

  private async deleteFromIndexedDB(key: string): Promise<void> {
    if (!this.db) return;

    return new Promise((resolve) => {
      const transaction = this.db!.transaction(['cache'], 'readwrite');
      const store = transaction.objectStore('cache');
      const request = store.delete(key);

      request.onsuccess = () => resolve();
      request.onerror = () => {
        console.warn('[Cache] Error deleting from IndexedDB');
        resolve();
      };
    });
  }

  private async clearIndexedDB(): Promise<void> {
    if (!this.db) return;

    return new Promise((resolve) => {
      const transaction = this.db!.transaction(['cache'], 'readwrite');
      const store = transaction.objectStore('cache');
      const request = store.clear();

      request.onsuccess = () => resolve();
      request.onerror = () => {
        console.warn('[Cache] Error clearing IndexedDB');
        resolve();
      };
    });
  }

  private getLocalStorageEntry(key: string): CacheEntry | null {
    try {
      const item = localStorage.getItem(`cache_${key}`);
      return item ? JSON.parse(item) : null;
    } catch (error) {
      return null;
    }
  }

  private async getIndexedDBEntry(key: string): Promise<CacheEntry | null> {
    if (!this.db) return null;

    return new Promise((resolve) => {
      const transaction = this.db!.transaction(['cache'], 'readonly');
      const store = transaction.objectStore('cache');
      const request = store.get(key);

      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => resolve(null);
    });
  }

  private getLocalStorageKeys(): string[] {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('cache_')) {
        keys.push(key);
      }
    }
    return keys;
  }

  private async getIndexedDBKeys(): Promise<string[]> {
    if (!this.db) return [];

    return new Promise((resolve) => {
      const keys: string[] = [];
      const transaction = this.db!.transaction(['cache'], 'readonly');
      const store = transaction.objectStore('cache');
      const request = store.getAllKeys();

      request.onsuccess = () => resolve(request.result as string[]);
      request.onerror = () => resolve([]);
    });
  }

  private isExpired(entry: CacheEntry): boolean {
    return new Date() > entry.expiresAt;
  }

  private calculateSize(value: any): number {
    return JSON.stringify(value).length * 2; // Approximate size in bytes
  }

  private createCacheInfo(entry: CacheEntry, level: CacheLevel): CacheInfo {
    return {
      cached: true,
      cacheKey: entry.key,
      cacheLevel: level,
      cachedAt: entry.timestamp,
      expiresAt: entry.expiresAt,
      ttl: entry.ttl,
      hitCount: entry.hitCount,
      missCount: entry.missCount,
      hitRate: entry.hitCount + entry.missCount > 0 ? entry.hitCount / (entry.hitCount + entry.missCount) : 0,
      size: entry.size,
      compressed: entry.compressed
    };
  }

  private async evictMemoryEntries(requiredSize: number): Promise<void> {
    // Simple LRU eviction - remove oldest entries first
    const entries = Array.from(this.memoryCache.entries())
      .sort(([, a], [, b]) => a.timestamp.getTime() - b.timestamp.getTime());

    let freedSize = 0;
    for (const [key, entry] of entries) {
      if (freedSize >= requiredSize) break;
      
      await this.deleteFromMemory(key);
      freedSize += entry.size;
    }
  }

  private startCleanupTimer(): void {
    // Clean up expired entries every 5 minutes
    setInterval(() => {
      this.cleanupExpiredEntries();
    }, 5 * 60 * 1000);
  }

  private async cleanupExpiredEntries(): Promise<void> {
    // Clean memory cache
    for (const [key, entry] of this.memoryCache.entries()) {
      if (this.isExpired(entry)) {
        await this.deleteFromMemory(key);
      }
    }

    // Clean localStorage
    const localStorageKeys = this.getLocalStorageKeys();
    for (const key of localStorageKeys) {
      const entry = this.getLocalStorageEntry(key.replace('cache_', ''));
      if (entry && this.isExpired(entry)) {
        await this.deleteFromLocalStorage(key.replace('cache_', ''));
      }
    }

    // Clean IndexedDB
    if (this.db) {
      const transaction = this.db.transaction(['cache'], 'readwrite');
      const store = transaction.objectStore('cache');
      const index = store.index('expiresAt');
      const range = IDBKeyRange.upperBound(new Date());
      
      index.openCursor(range).onsuccess = (event) => {
        const cursor = (event.target as IDBRequest).result;
        if (cursor) {
          cursor.delete();
          cursor.continue();
        }
      };
    }
  }
}

export default CacheManager;
