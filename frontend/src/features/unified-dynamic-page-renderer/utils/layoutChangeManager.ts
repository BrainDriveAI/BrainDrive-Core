import { ResponsiveLayouts, LayoutItem } from '../types';

export interface LayoutChangeOrigin {
  source:
    | 'user-drag'
    | 'user-resize'
    | 'user-remove'
    | 'user-bounce-recovery'
    | 'external-sync'
    | 'initial-load'
    | 'drop-add';
  timestamp: number;
  operationId?: string;
  version?: number; // PHASE B: Add version field for stale update detection
}

export interface LayoutChangeEvent {
  layouts: ResponsiveLayouts;
  origin: LayoutChangeOrigin;
  hash: string;
}

/**
 * Semantic layout comparison that ignores object reference changes
 * but detects actual layout differences
 */
export function compareLayoutsSemanticaly(
  layouts1: ResponsiveLayouts | null,
  layouts2: ResponsiveLayouts | null
): boolean {
  if (!layouts1 && !layouts2) return true;
  if (!layouts1 || !layouts2) return false;

  const breakpoints: (keyof ResponsiveLayouts)[] = ['mobile', 'tablet', 'desktop', 'wide'];
  
  for (const breakpoint of breakpoints) {
    const layout1 = layouts1[breakpoint] || [];
    const layout2 = layouts2[breakpoint] || [];
    
    if (layout1.length !== layout2.length) return false;
    
    // Sort by item ID for consistent comparison
    const sorted1 = [...layout1].sort((a, b) => a.i.localeCompare(b.i));
    const sorted2 = [...layout2].sort((a, b) => a.i.localeCompare(b.i));
    
    for (let i = 0; i < sorted1.length; i++) {
      const item1 = sorted1[i];
      const item2 = sorted2[i];
      
      // Compare essential layout properties
      if (
        item1.i !== item2.i ||
        item1.x !== item2.x ||
        item1.y !== item2.y ||
        item1.w !== item2.w ||
        item1.h !== item2.h ||
        item1.moduleId !== item2.moduleId ||
        item1.pluginId !== item2.pluginId
      ) {
        return false;
      }
    }
  }
  
  return true;
}

/**
 * Generate a stable hash for layouts based on semantic content
 */
export function generateLayoutHash(layouts: ResponsiveLayouts | null): string {
  if (!layouts) return 'null';
  
  const breakpoints: (keyof ResponsiveLayouts)[] = ['mobile', 'tablet', 'desktop', 'wide'];
  const hashParts: string[] = [];
  
  for (const breakpoint of breakpoints) {
    const layout = layouts[breakpoint] || [];
    const sortedItems = [...layout]
      .sort((a, b) => a.i.localeCompare(b.i))
      .map(item => `${item.i}:${item.x},${item.y},${item.w},${item.h}:${item.moduleId}:${item.pluginId}`)
      .join('|');
    
    hashParts.push(`${breakpoint}=[${sortedItems}]`);
  }
  
  return hashParts.join(';');
}

/**
 * PHASE B: Check if a layout change is stale based on version comparison
 * @param origin The origin of the layout change
 * @param currentVersion The current committed version
 * @returns true if the change is stale and should be ignored
 */
export const isStaleLayoutChange = (origin: LayoutChangeOrigin, currentVersion: number): boolean => {
  return origin.version !== undefined && origin.version < currentVersion;
};

/**
 * Layout Change Manager - handles debouncing, deduplication, and origin tracking
 */
export class LayoutChangeManager {
  private pendingChanges = new Map<string, LayoutChangeEvent>();
  private lastProcessedHash: string | null = null;
  private debounceTimeouts = new Map<string, NodeJS.Timeout>();
  private activeOperations = new Set<string>();
  // Phase 3: Add promise tracking for flush operations
  private pendingPromises = new Map<string, { resolve: () => void; reject: (error: Error) => void }>();
  private flushPromise: Promise<void> | null = null;
  private flushResolve: (() => void) | null = null;
  
  constructor(
    private onLayoutChange: (event: LayoutChangeEvent) => void,
    private debounceMs: number = 50
  ) {}

  /**
   * Queue a layout change with debouncing and deduplication
   */
  queueLayoutChange(
    layouts: ResponsiveLayouts,
    origin: LayoutChangeOrigin,
    debounceKey: string = 'default',
    debounceOverride?: number
  ): void {
    const hash = generateLayoutHash(layouts);
    
    // Skip if this is the exact same layout we just processed
    if (hash === this.lastProcessedHash) {
      
      return;
    }
    
    // Skip if there's an active operation that should block this change
    if (this.shouldBlockChange(origin)) {
      
      return;
    }
    
    const event: LayoutChangeEvent = { layouts, origin, hash };
    this.pendingChanges.set(debounceKey, event);

    // Clear existing timeout for this key before scheduling a new one
    const existingTimeout = this.debounceTimeouts.get(debounceKey);
    if (existingTimeout) {
      clearTimeout(existingTimeout);
    }

    // Phase 3: Create flush promise if not exists
    if (!this.flushPromise) {
      this.flushPromise = new Promise<void>((resolve) => {
        this.flushResolve = resolve;
      });
    }

    const isUserOrigin =
      origin.source === 'user-drag' ||
      origin.source === 'user-resize' ||
      origin.source === 'user-bounce-recovery' ||
      origin.source === 'drop-add';
    const effectiveDebounce = typeof debounceOverride === 'number'
      ? debounceOverride
      : (isUserOrigin ? 0 : this.debounceMs);

    if (effectiveDebounce <= 0) {
      this.debounceTimeouts.delete(debounceKey);
      this.processPendingChange(debounceKey);
      return;
    }

    // Set new debounced timeout
    const timeout = setTimeout(() => {
      this.processPendingChange(debounceKey);
    }, effectiveDebounce);

    this.debounceTimeouts.set(debounceKey, timeout);
  }

  /**
   * Process a pending layout change
   */
  private processPendingChange(debounceKey: string): void {
    const event = this.pendingChanges.get(debounceKey);
    if (!event) return;
    
    // Final deduplication check
    if (event.hash === this.lastProcessedHash) {
      
      return;
    }
    
    
    
    this.lastProcessedHash = event.hash;
    this.pendingChanges.delete(debounceKey);
    this.debounceTimeouts.delete(debounceKey);
    
    // Phase 3: Resolve pending promise for this key
    const pendingPromise = this.pendingPromises.get(debounceKey);
    if (pendingPromise) {
      pendingPromise.resolve();
      this.pendingPromises.delete(debounceKey);
    }
    
    this.onLayoutChange(event);
    
    // Phase 3: Check if all pending changes are processed
    if (this.pendingChanges.size === 0 && this.flushResolve) {
      this.flushResolve();
      this.flushPromise = null;
      this.flushResolve = null;
    }
  }

  /**
   * Start tracking an operation that should block certain layout changes
   */
  startOperation(operationId: string): void {
    this.activeOperations.add(operationId);
    
  }

  /**
   * Stop tracking an operation
   */
  stopOperation(operationId: string): void {
    this.activeOperations.delete(operationId);
    
  }

  /**
   * Check if a layout change should be blocked
   */
  private shouldBlockChange(origin: LayoutChangeOrigin): boolean {
    // Don't block user-initiated changes
    if (
      origin.source === 'user-drag' ||
      origin.source === 'user-resize' ||
      origin.source === 'user-bounce-recovery'
    ) {
      return false;
    }
    
    // Block external syncs if there are active user operations
    if (origin.source === 'external-sync' && this.activeOperations.size > 0) {
      return true;
    }
    
    return false;
  }

  /**
   * Force process all pending changes and return a promise that resolves when done
   * Phase 3: Enhanced flush with promise support
   */
  flush(): Promise<void> {
    // If no pending changes, resolve immediately
    if (this.pendingChanges.size === 0) {
      return Promise.resolve();
    }
    
    // Process all pending changes immediately
    const keys = Array.from(this.pendingChanges.keys());
    for (const key of keys) {
      const timeout = this.debounceTimeouts.get(key);
      if (timeout) {
        clearTimeout(timeout);
      }
      this.processPendingChange(key);
    }
    
    // Return the flush promise or resolve immediately if all processed
    return this.flushPromise || Promise.resolve();
  }
  
  /**
   * Get pending change keys (for debugging)
   */
  getPending(): string[] {
    return Array.from(this.pendingChanges.keys());
  }

  /**
   * Clean up all timeouts
   */
  destroy(): void {
    for (const timeout of this.debounceTimeouts.values()) {
      clearTimeout(timeout);
    }
    this.debounceTimeouts.clear();
    this.pendingChanges.clear();
    this.activeOperations.clear();
  }
}
