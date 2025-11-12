import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { ResponsiveLayouts, LayoutItem } from '../types';
import {
  LayoutChangeManager,
  LayoutChangeEvent,
  LayoutChangeOrigin,
  compareLayoutsSemanticaly,
  generateLayoutHash,
  isStaleLayoutChange
} from '../utils/layoutChangeManager';
import { getLayoutCommitTracker, CommitMetadata } from '../utils/layoutCommitTracker';

export interface UnifiedLayoutStateOptions {
  initialLayouts?: ResponsiveLayouts | null;
  debounceMs?: number;
  onLayoutPersist?: (layouts: ResponsiveLayouts, origin: LayoutChangeOrigin) => void;
  onError?: (error: Error) => void;
}

export interface UnifiedLayoutState {
  // Current layout state
  layouts: ResponsiveLayouts | null;
  isLayoutChanging: boolean;
  
  // Layout operations
  updateLayouts: (layouts: ResponsiveLayouts, origin: LayoutChangeOrigin, options?: { debounceMs?: number }) => void;
  resetLayouts: (layouts: ResponsiveLayouts | null) => void;
  
  // Operation tracking
  startOperation: (operationId: string) => void;
  stopOperation: (operationId: string) => void;
  
  // Utility functions
  getLayoutHash: () => string;
  compareWithCurrent: (layouts: ResponsiveLayouts) => boolean;
  
  // Phase 1 & 3: Commit tracking and barrier
  getLastCommitMeta: () => CommitMetadata | null;
  flush: () => Promise<{ version: number; hash: string }>;
  getCommittedLayouts: () => ResponsiveLayouts | null;
}

/**
 * Unified Layout State Hook
 * 
 * This hook provides a single source of truth for layout state management
 * with built-in debouncing, deduplication, and operation tracking.
 */
export function useUnifiedLayoutState(options: UnifiedLayoutStateOptions = {}): UnifiedLayoutState {
  const {
    initialLayouts = null,
    debounceMs = 50,
    onLayoutPersist,
    onError
  } = options;

  // Core state
  const [layouts, setLayouts] = useState<ResponsiveLayouts | null>(initialLayouts);
  const [isLayoutChanging, setIsLayoutChanging] = useState(false);
  
  // Refs for stable references
  const layoutChangeManagerRef = useRef<LayoutChangeManager | null>(null);
  const lastPersistedHashRef = useRef<string | null>(null);
  const initializationCompleteRef = useRef(false);
  const stableLayoutsRef = useRef<ResponsiveLayouts | null>(initialLayouts);
  
  // PHASE B: Add version tracking for stale update prevention
  const lastCommittedVersionRef = useRef<number>(0);
  
  // Phase 1: Add commit tracking
  const committedLayoutsRef = useRef<ResponsiveLayouts | null>(initialLayouts);
  const layoutCommitTracker = getLayoutCommitTracker();
  const isDebugMode = import.meta.env.VITE_LAYOUT_DEBUG === 'true';

  // Stable refs for callbacks to prevent recreation
  const onLayoutPersistRef = useRef(onLayoutPersist);
  const onErrorRef = useRef(onError);
  
  // Update refs when callbacks change
  useEffect(() => {
    onLayoutPersistRef.current = onLayoutPersist;
  }, [onLayoutPersist]);
  
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  // Handle processed layout changes - now stable!
  const handleLayoutChangeEvent = useCallback((event: LayoutChangeEvent) => {
    // PHASE B: Check if this is a stale update based on version
    if (event.origin.version !== undefined && event.origin.version < lastCommittedVersionRef.current) {
      if (isDebugMode) {
        console.log('[useUnifiedLayoutState] Ignoring stale layout change', {
          eventVersion: event.origin.version,
          currentVersion: lastCommittedVersionRef.current,
          source: event.origin.source
        });
      }
      return;
    }
    
    // Phase 1: Log the persist event
    const version = event.origin.version || lastCommittedVersionRef.current + 1;
    if (isDebugMode) {
      console.log(`[UnifiedLayoutState] Persist v${version} hash:${event.hash}`, {
        source: event.origin.source,
        operationId: event.origin.operationId
      });
    }
    
    setIsLayoutChanging(true);
    
    // Update the layouts state only if different
    setLayouts(prevLayouts => {
      if (compareLayoutsSemanticaly(prevLayouts, event.layouts)) {
        
        setIsLayoutChanging(false); // Reset immediately if no change
        return prevLayouts; // Return same reference to prevent re-render
      }
      
      // Update stable reference only when layouts actually change
      stableLayoutsRef.current = event.layouts;
      return event.layouts;
    });
    
    // RECODE V2 BLOCK: Strengthen commit barrier - always persist resize operations
    const isUserAction = event.origin.source === 'user-drag' ||
                         event.origin.source === 'user-resize' ||
                         event.origin.source === 'user-remove' ||
                         event.origin.source === 'drop-add' ||
                         event.origin.source === 'user-bounce-recovery';
    
    // For resize operations, force persist even if hash appears same (dimensions might differ)
    const shouldPersist = onLayoutPersistRef.current &&
                         (event.hash !== lastPersistedHashRef.current || isUserAction);
    
    if (shouldPersist) {
      try {
        // RECODE V2 BLOCK: Enhanced logging for resize operations
        if (event.origin.source === 'user-resize') {
          const desktopItems = event.layouts.desktop || [];
          console.log('[RECODE_V2_BLOCK] useUnifiedLayoutState persist - resize dimensions', {
            source: event.origin.source,
            version: event.origin.version || lastCommittedVersionRef.current + 1,
            hash: event.hash,
            desktopItemDimensions: desktopItems.map((item: any) => ({
              id: item.i,
              dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
            })),
            timestamp: Date.now()
          });
        }
        
        // RECODE V2 BLOCK: Always update committed layouts for user actions
        committedLayoutsRef.current = JSON.parse(JSON.stringify(event.layouts));
        
        // Phase 1: Record commit in tracker
        const commitMeta: CommitMetadata = {
          version: event.origin.version || lastCommittedVersionRef.current + 1,
          hash: event.hash,
          timestamp: Date.now()
        };
        layoutCommitTracker.recordCommit(commitMeta);
        
        const firstDesktopItem = event.layouts.desktop?.[0];
        console.log('[UnifiedLayoutState] Persist dispatch', JSON.stringify({
          source: event.origin.source,
          version: commitMeta.version,
          hash: event.hash,
          firstDesktop: firstDesktopItem ? { id: firstDesktopItem.i, x: firstDesktopItem.x, y: firstDesktopItem.y } : null
        }));
        onLayoutPersistRef.current!(event.layouts, { ...event.origin, version: commitMeta.version });
        lastPersistedHashRef.current = event.hash;
        
        // PHASE B: Update committed version when persisting user changes
        if (event.origin.version !== undefined) {
          lastCommittedVersionRef.current = event.origin.version;
        } else {
          lastCommittedVersionRef.current++;
        }
      } catch (error) {
        console.error('[useUnifiedLayoutState] Error persisting layout:', error);
        onErrorRef.current?.(error as Error);
      }
    }
    
    // Reset changing state after a brief delay
    setTimeout(() => setIsLayoutChanging(false), 100);
  }, []); // Now has no dependencies - completely stable!

  // Initialize layout change manager
  useEffect(() => {
    if (!layoutChangeManagerRef.current) {
      layoutChangeManagerRef.current = new LayoutChangeManager(
        handleLayoutChangeEvent,
        debounceMs
      );
    }
    
    return () => {
      if (layoutChangeManagerRef.current) {
        layoutChangeManagerRef.current.destroy();
        layoutChangeManagerRef.current = null;
      }
    };
  }, [handleLayoutChangeEvent, debounceMs]);

  // Handle initial layouts
  useEffect(() => {
    if (initialLayouts && !initializationCompleteRef.current) {
      
      setLayouts(initialLayouts);
      stableLayoutsRef.current = initialLayouts;
      lastPersistedHashRef.current = generateLayoutHash(initialLayouts);
      initializationCompleteRef.current = true;
    }
  }, [initialLayouts]);

  // Update layouts function - now stable!
  const updateLayouts = useCallback((newLayouts: ResponsiveLayouts, origin: LayoutChangeOrigin, options?: { debounceMs?: number }) => {
    if (!layoutChangeManagerRef.current) {
      console.warn('[useUnifiedLayoutState] Layout change manager not initialized');
      return;
    }

    // Skip if layouts are semantically identical - use stable ref instead of state
    if (compareLayoutsSemanticaly(stableLayoutsRef.current, newLayouts)) {
      if (isDebugMode) {
        console.log('[UnifiedLayoutState] Skipping identical layout update');
      }
      return;
    }

    // Phase 1: Track pending commit
    const hash = generateLayoutHash(newLayouts);
    // If this matches the last persisted hash, it will be dropped by the pipeline; avoid tracking pending
    if (lastPersistedHashRef.current === hash) {
      if (isDebugMode) {
        console.log('[UnifiedLayoutState] Skipping update equal to last persisted hash');
      }
      return;
    }
    const version = origin.version ?? lastCommittedVersionRef.current + 1;
    layoutCommitTracker.trackPending(version, hash);
    
    // Queue the layout change with appropriate debounce key
    const debounceKey = origin.operationId || origin.source;
    const originWithVersion: LayoutChangeOrigin = {
      ...origin,
      version
    };
    layoutChangeManagerRef.current.queueLayoutChange(newLayouts, originWithVersion, debounceKey, options?.debounceMs);
  }, []); // Now has no dependencies - completely stable!

  // Reset layouts function (for page changes, etc.)
  const resetLayouts = useCallback((newLayouts: ResponsiveLayouts | null) => {
    // Reset visible and stable state
    setLayouts(newLayouts);
    stableLayoutsRef.current = newLayouts;
    lastPersistedHashRef.current = newLayouts ? generateLayoutHash(newLayouts) : null;

    // ALSO reset committed snapshot and version to avoid cross-page contamination
    committedLayoutsRef.current = newLayouts;
    lastCommittedVersionRef.current = 0;
    // Clear commit tracker (dev instrumentation)
    layoutCommitTracker.clear();

    // Clear any pending changes in the manager
    if (layoutChangeManagerRef.current) {
      layoutChangeManagerRef.current.flush();
    }
  }, []);

  // Operation tracking functions
  const startOperation = useCallback((operationId: string) => {
    if (layoutChangeManagerRef.current) {
      layoutChangeManagerRef.current.startOperation(operationId);
    }
  }, []);

  const stopOperation = useCallback((operationId: string) => {
    if (layoutChangeManagerRef.current) {
      layoutChangeManagerRef.current.stopOperation(operationId);
    }
  }, []);

  // Utility functions
  const getLayoutHash = useCallback(() => {
    return generateLayoutHash(stableLayoutsRef.current);
  }, []);

  const compareWithCurrent = useCallback((otherLayouts: ResponsiveLayouts) => {
    return compareLayoutsSemanticaly(stableLayoutsRef.current, otherLayouts);
  }, []);
  
  // Phase 1: Get last commit metadata
  const getLastCommitMeta = useCallback(() => {
    return layoutCommitTracker.getLastCommit();
  }, []);
  
  // Phase 3: Enhanced flush that waits for pending layout changes to complete
  const flush = useCallback(async (): Promise<{ version: number; hash: string }> => {
    // First flush any pending changes in the layout change manager and wait for completion
    if (layoutChangeManagerRef.current) {
      await layoutChangeManagerRef.current.flush();
    }
    
    // Wait for the commit tracker to process all pending commits
    await layoutCommitTracker.flush();
    
    // Return the last committed metadata
    const lastCommit = layoutCommitTracker.getLastCommit();
    if (lastCommit) {
      return { version: lastCommit.version, hash: lastCommit.hash };
    }
    
    // If no commits yet, return current state
    return {
      version: lastCommittedVersionRef.current,
      hash: generateLayoutHash(stableLayoutsRef.current)
    };
  }, []);
  
  // Phase 1: Get committed layouts
  const getCommittedLayouts = useCallback(() => {
    return committedLayoutsRef.current;
  }, []);

  // Create a stable layouts reference that only changes when layouts actually change
  const stableLayouts = useMemo(() => {
    return stableLayoutsRef.current;
  }, [layouts]); // This will only change when the layouts state changes

  return {
    layouts: stableLayouts,
    isLayoutChanging,
    updateLayouts,
    resetLayouts,
    startOperation,
    stopOperation,
    getLayoutHash,
    compareWithCurrent,
    getLastCommitMeta,
    flush,
    getCommittedLayouts
  };
}
