import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Responsive, WidthProvider } from 'react-grid-layout';
import { useTheme, Box, Chip } from '@mui/material';
import { RenderMode, ResponsiveLayouts, LayoutItem, ModuleConfig } from '../types';
import { ModuleRenderer } from './ModuleRenderer';
import { LegacyModuleAdapter } from '../adapters/LegacyModuleAdapter';
import { useBreakpoint } from '../hooks/useBreakpoint';
import { GridItemControls } from '../../plugin-studio/components/canvas/GridItemControls';
import { useUnifiedLayoutState } from '../hooks/useUnifiedLayoutState';
import { LayoutChangeOrigin, generateLayoutHash } from '../utils/layoutChangeManager';
import { useControlVisibility } from '../../../hooks/useControlVisibility';
import { getLayoutCommitTracker } from '../utils/layoutCommitTracker';

const ResponsiveGridLayout = WidthProvider(Responsive);

export interface LayoutEngineProps {
  layouts: ResponsiveLayouts;
  modules: ModuleConfig[];
  mode: RenderMode;
  lazyLoading?: boolean;
  preloadPlugins?: string[];
  pageId?: string; // Add pageId to detect page changes
  onLayoutChange?: (layouts: ResponsiveLayouts) => void;
  onItemAdd?: (item: LayoutItem) => void;
  onItemRemove?: (itemId: string) => void;
  onItemSelect?: (itemId: string) => void;
  onItemConfig?: (itemId: string) => void;
  useSafeCommitSetters?: boolean;
}

const defaultGridConfig = {
  cols: { xxl: 12, xl: 12, lg: 12, sm: 8, xs: 4 },
  rowHeight: 60,
  margin: [10, 10] as [number, number],
  containerPadding: [10, 10] as [number, number],
  breakpoints: { xxl: 1600, xl: 1400, lg: 1024, sm: 768, xs: 0 },
};

export const LayoutEngine: React.FC<LayoutEngineProps> = React.memo(({
  layouts,
  modules,
  mode,
  lazyLoading = true,
  preloadPlugins = [],
  pageId,
  onLayoutChange,
  onItemAdd,
  onItemRemove,
  onItemSelect,
  onItemConfig,
  useSafeCommitSetters = false,
}) => {
  const theme = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  
  // Debug: Track component re-renders
  const layoutEngineRenderCount = useRef(0);
  layoutEngineRenderCount.current++;
  
  // ========== PHASE A: VERSIONED DOUBLE-BUFFER CONTROLLER ==========
  // A1: Add Feature Flag Support
  const ENABLE_LAYOUT_CONTROLLER_V2 = import.meta.env.VITE_LAYOUT_CONTROLLER_V2 === 'true' || false;
  
  // Phase 1: Add debug mode flag
  const isDebugMode = import.meta.env.VITE_LAYOUT_DEBUG === 'true';
  const layoutGracePeriod = Number(import.meta.env.VITE_LAYOUT_GRACE_PERIOD ?? 150);
  const userCommitDelayMs = Math.min(layoutGracePeriod, 40);
  
  // A2: Implement Controller State
  // Double-buffer refs for layout state
  const workingLayoutsRef = useRef<ResponsiveLayouts | null>(null);
  const canonicalLayoutsRef = useRef<ResponsiveLayouts | null>(null);
  
  // Controller state machine
  type ControllerState = 'idle' | 'resizing' | 'dragging' | 'grace' | 'commit';
  const controllerStateRef = useRef<ControllerState>('idle');
  
  // Version tracking for stale update prevention
  const lastVersionRef = useRef<number>(0);
  
  // Force update function for controller state changes
  const [, forceUpdate] = useState({});
  const triggerUpdate = useCallback(() => forceUpdate({}), []);
  
  // A5: Add Development Logging
  const logControllerState = useCallback((action: string, data?: any) => {
    if (import.meta.env.MODE === 'development' && ENABLE_LAYOUT_CONTROLLER_V2) {
      console.log(`[LayoutController V2] ${action}`, {
        state: controllerStateRef.current,
        version: lastVersionRef.current,
        workingLayouts: !!workingLayoutsRef.current,
        canonicalLayouts: !!canonicalLayoutsRef.current,
        timestamp: performance.now().toFixed(2),
        ...data
      });
    }
  }, [ENABLE_LAYOUT_CONTROLLER_V2]);
  
  // ========== PHASE C1: Complete State Machine Transitions ==========
  // State transition function with validation and logging
  const transitionToState = useCallback((newState: ControllerState, data?: any) => {
    if (!ENABLE_LAYOUT_CONTROLLER_V2) return;
    
    const oldState = controllerStateRef.current;
    
    // Validate state transitions
    const validTransitions: Record<ControllerState, ControllerState[]> = {
      'idle': ['resizing', 'dragging'],
      'resizing': ['grace', 'idle'], // Can abort to idle
      'dragging': ['grace', 'idle'], // Can abort to idle
      'grace': ['commit', 'idle'], // Can abort to idle
      'commit': ['idle']
    };
    
    if (!validTransitions[oldState].includes(newState)) {
      logControllerState('INVALID_STATE_TRANSITION', {
        from: oldState,
        to: newState,
        allowed: validTransitions[oldState],
        ...data
      });
      return;
    }
    
    controllerStateRef.current = newState;
    logControllerState(`STATE_TRANSITION: ${oldState} -> ${newState}`, data);
    
    // Trigger re-render to update displayedLayouts
    triggerUpdate();
  }, [ENABLE_LAYOUT_CONTROLLER_V2, logControllerState, triggerUpdate]);
  // ========== END PHASE C1 ==========
  
  // Log controller initialization
  useEffect(() => {
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      logControllerState('CONTROLLER_INITIALIZED', {
        featureFlag: ENABLE_LAYOUT_CONTROLLER_V2,
        environment: import.meta.env.MODE
      });
    }
  }, [ENABLE_LAYOUT_CONTROLLER_V2, logControllerState]);
  // ========== END PHASE A CONTROLLER ==========

  // Use unified layout state management with stable reference
  const unifiedLayoutState = useUnifiedLayoutState({
    initialLayouts: layouts,
    debounceMs: 200, // Increase debounce to prevent rapid updates
    onLayoutPersist: (persistedLayouts, origin) => {
      onLayoutChange?.(persistedLayouts);
    },
    onError: (error) => {
      console.error('[LayoutEngine] Layout state error:', error);
    }
  });

  // Create ultra-stable layouts reference using JSON comparison
  const stableLayoutsRef = useRef<ResponsiveLayouts>({
    mobile: [],
    tablet: [],
    desktop: [],
    wide: []
  });
  
  const currentLayouts = useMemo(() => {
    const newLayouts = unifiedLayoutState.layouts || {
      mobile: [],
      tablet: [],
      desktop: [],
      wide: []
    };
    
    // Only update if the actual content has changed (deep comparison)
    const currentHash = JSON.stringify(stableLayoutsRef.current);
    const newHash = JSON.stringify(newLayouts);
    
    if (currentHash !== newHash) {
      stableLayoutsRef.current = newLayouts;
    }
    
    return stableLayoutsRef.current;
  }, [unifiedLayoutState.layouts]);

  // A3: Implement Display Logic - Controller decides what layouts to display
  const displayedLayouts = useMemo(() => {
    if (!ENABLE_LAYOUT_CONTROLLER_V2) {
      return currentLayouts; // Fallback to existing logic
    }
    
    const state = controllerStateRef.current;
    
    // During operations and grace period, show working buffer
    if (state === 'resizing' || state === 'dragging' || state === 'grace') {
      logControllerState('DISPLAY_WORKING_BUFFER', { state });
      return workingLayoutsRef.current || currentLayouts;
    }
    
    // When idle or committed, always prefer currentLayouts to ensure fresh data
    // The buffers are just for operation management, not for caching page data
    logControllerState('DISPLAY_CANONICAL_BUFFER', {
      state,
      usingCurrentLayouts: true
    });
    return currentLayouts;
  }, [currentLayouts, ENABLE_LAYOUT_CONTROLLER_V2, logControllerState]);

  // Initialize buffers when layouts change externally
  useEffect(() => {
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      // Only update buffers when idle to avoid disrupting ongoing operations
      if (controllerStateRef.current === 'idle') {
        // Always sync buffers with current layouts when idle
        // This ensures page changes are reflected immediately
        // RECODE V2 BLOCK: Ensure ultrawide field exists
        const layoutsWithUltrawide = {
          ...currentLayouts,
          ultrawide: currentLayouts.ultrawide || []
        };
        canonicalLayoutsRef.current = layoutsWithUltrawide;
        workingLayoutsRef.current = layoutsWithUltrawide;
        logControllerState('BUFFERS_SYNCED', {
          source: 'external_update',
          state: controllerStateRef.current,
          itemCount: currentLayouts?.desktop?.length || 0,
          hasUltrawide: !!layoutsWithUltrawide.ultrawide
        });
      } else {
        // Log that we're skipping update due to ongoing operation
        logControllerState('BUFFER_SYNC_SKIPPED', {
          reason: 'operation_in_progress',
          state: controllerStateRef.current
        });
      }
    }
  }, [currentLayouts, ENABLE_LAYOUT_CONTROLLER_V2, logControllerState]);

  // ========== PHASE C2: Single Debounced Commit Process ==========
  // Track operation IDs for proper state management (moved up for use in commitLayoutChanges)
  const currentOperationId = useRef<string | null>(null);
  
  // Debounced commit timer ref
  const commitTimerRef = useRef<NodeJS.Timeout | null>(null);
  const awaitingCommitSetterRef = useRef<React.Dispatch<React.SetStateAction<boolean>> | null>(null);
  const commitHighlightSetterRef = useRef<React.Dispatch<React.SetStateAction<string | null>> | null>(null);
  const pendingAwaitingCommitRef = useRef<boolean | null>(null);
  const pendingHighlightRef = useRef<string | null>(null);

  const setIsAwaitingCommitSafe = useCallback((value: boolean) => {
    if (!useSafeCommitSetters) {
      setIsAwaitingCommit(value);
      return;
    }

    if (awaitingCommitSetterRef.current) {
      awaitingCommitSetterRef.current(value);
    } else {
      pendingAwaitingCommitRef.current = value;
    }
  }, [useSafeCommitSetters]);

  const setCommitHighlightSafe = useCallback((value: string | null) => {
    if (!useSafeCommitSetters) {
      setCommitHighlightId(value);
      return;
    }

    if (commitHighlightSetterRef.current) {
      commitHighlightSetterRef.current(value);
    } else {
      pendingHighlightRef.current = value;
    }
  }, [useSafeCommitSetters]);

  // Unified commit function for all operations
  const commitLayoutChanges = useCallback(async (finalLayout?: any, finalAllLayouts?: any, breakpoint?: string, activeItemId?: string) => {
    if (!ENABLE_LAYOUT_CONTROLLER_V2) {
      logControllerState('COMMIT_SKIPPED', {
        reason: 'Controller disabled'
      });
      return;
    }
    
    const version = lastVersionRef.current;
    let layouts: ResponsiveLayouts;
    
    // RECODE V2 BLOCK: Use finalLayout if provided (from resize/drag stop) for accurate dimensions
    if (finalLayout && finalAllLayouts) {
      // Convert the final layout data to ResponsiveLayouts format
      const convertedLayouts: ResponsiveLayouts = {
        mobile: [],
        tablet: [],
        desktop: [],
        wide: [],
        ultrawide: []
      };
      // Normalize breakpoint keys from either grid (xs/sm/lg/xl/xxl) or semantic names
      const toOurBreakpoint = (bp: string): keyof ResponsiveLayouts | undefined => {
        const map: Record<string, keyof ResponsiveLayouts> = {
          xs: 'mobile', sm: 'tablet', lg: 'desktop', xl: 'wide', xxl: 'ultrawide',
          mobile: 'mobile', tablet: 'tablet', desktop: 'desktop', wide: 'wide', ultrawide: 'ultrawide'
        };
        return map[bp];
      };
      
      // Helper: normalize an RGL layout array to include moduleId/pluginId
      const normalizeItems = (items: any[] = []): LayoutItem[] => {
        return (items || []).map((it: any) => {
          const id = it?.i ?? '';
          let pluginId = it?.pluginId;
          if (!pluginId && typeof id === 'string' && id.includes('_')) {
            pluginId = id.split('_')[0];
          }
          return {
            i: id,
            x: it?.x ?? 0,
            y: it?.y ?? 0,
            w: it?.w ?? 2,
            h: it?.h ?? 2,
            moduleId: it?.moduleId || id,
            pluginId: pluginId || 'unknown',
            minW: it?.minW,
            minH: it?.minH,
            isDraggable: it?.isDraggable ?? true,
            isResizable: it?.isResizable ?? true,
            static: it?.static ?? false,
            config: it?.config
          } as LayoutItem;
        });
      };

      // Use the current breakpoint's layout from finalLayout (has the latest changes)
      // Use the breakpoint parameter passed in
      if (finalLayout && breakpoint) {
        const ourBreakpoint = toOurBreakpoint(breakpoint);
        if (ourBreakpoint) {
          convertedLayouts[ourBreakpoint] = normalizeItems(finalLayout as any[]);
          
          console.log('[RECODE_V2_BLOCK] commitLayoutChanges - using finalLayout for commit', {
            breakpoint: ourBreakpoint,
            itemDimensions: finalLayout.map((item: any) => ({
              id: item.i,
              dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
            })),
            version,
            timestamp: Date.now()
          });
        }
      }
      
      // Fill in other breakpoints from finalAllLayouts
      Object.entries(finalAllLayouts).forEach(([gridBreakpoint, gridLayout]: [string, any]) => {
        const ourBreakpoint = toOurBreakpoint(gridBreakpoint);
        if (ourBreakpoint && Array.isArray(gridLayout)) {
          // Only use allLayouts if we haven't already set this breakpoint from the current layout
          if (toOurBreakpoint(gridBreakpoint) !== toOurBreakpoint(breakpoint || '') || !finalLayout) {
            convertedLayouts[ourBreakpoint] = normalizeItems(gridLayout as any[]);
          }
        }
      });
      
      layouts = convertedLayouts;
      // Also update the working buffer with the final layouts
      workingLayoutsRef.current = convertedLayouts;
    } else if (workingLayoutsRef.current) {
      // Fallback to working buffer if no final layout provided
      layouts = workingLayoutsRef.current;
      console.log('[RECODE_V2_BLOCK] commitLayoutChanges - using workingLayoutsRef', {
        version,
        hasLayouts: !!workingLayoutsRef.current,
        breakpoints: Object.keys(workingLayoutsRef.current || {}),
        itemCounts: Object.entries(workingLayoutsRef.current || {}).map(([bp, items]) => ({
          breakpoint: bp,
          count: (items as any[])?.length || 0
        })),
        timestamp: Date.now()
      });
    } else {
      console.error('[RECODE_V2_BLOCK] COMMIT SKIPPED - No layouts available!', {
        hasWorkingBuffer: !!workingLayoutsRef.current,
        hasCanonicalBuffer: !!canonicalLayoutsRef.current,
        version
      });
      logControllerState('COMMIT_SKIPPED', {
        reason: 'No layouts available'
      });
      return;
    }
    
    const hash = generateLayoutHash(layouts);
    
    // RECODE V2 BLOCK: Enhanced commit logging
    console.log('[RECODE_V2_BLOCK] COMMIT - About to persist layouts', {
      version,
      hash,
      hasLayouts: !!layouts,
      breakpoints: Object.keys(layouts || {}),
      itemCounts: Object.entries(layouts || {}).map(([bp, items]) => ({
        breakpoint: bp,
        count: (items as any[])?.length || 0,
        items: (items as any[])?.map((item: any) => ({
          id: item.i,
          dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
        }))
      }))
    });
    
    // Phase 1: Log commit event
    if (isDebugMode) {
      console.log(`[LayoutEngine] Commit v${version} hash:${hash}`, {
        source: controllerStateRef.current === 'resizing' ? 'user-resize' : 'user-drag',
        timestamp: Date.now()
      });
    }
    
    logControllerState('COMMIT_START', {
      version,
      hash,
      layoutsPresent: !!layouts,
      currentState: controllerStateRef.current
    });

    if (activeItemId) {
      if (typeof setCommitHighlightSafe === 'function') {
        setCommitHighlightSafe(activeItemId);
      } else {
        setCommitHighlightId(activeItemId);
      }
      if (commitHighlightTimeoutRef.current) {
        clearTimeout(commitHighlightTimeoutRef.current);
      }
      commitHighlightTimeoutRef.current = setTimeout(() => {
        if (typeof setCommitHighlightSafe === 'function') {
          setCommitHighlightSafe(null);
        } else {
          setCommitHighlightId(null);
        }
        commitHighlightTimeoutRef.current = null;
      }, 400);
    }

    transitionToState('commit', { version });

    const origin: LayoutChangeOrigin = {
      source: controllerStateRef.current === 'resizing' ? 'user-resize' : 'user-drag',
      version,
      timestamp: Date.now(),
      operationId: currentOperationId.current || `commit-${Date.now()}`
    };

    const debounceMs = origin.source.startsWith('user-') ? Math.min(userCommitDelayMs, 20) : undefined;

    unifiedLayoutState.updateLayouts(layouts, origin, { debounceMs });

    canonicalLayoutsRef.current = JSON.parse(JSON.stringify(layouts));

    const commitStart = performance.now();
    const flushPromise = unifiedLayoutState.flush();
    const safetyWindowMs = Math.max(layoutGracePeriod * 2, 600);

    if (typeof setIsAwaitingCommitSafe === 'function') {
      setIsAwaitingCommitSafe(true);
    } else {
      setIsAwaitingCommit(true);
    }

    let flushTimedOut = false;
    let flushError: unknown = null;
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null;

    const timeoutPromise = new Promise<void>((resolve) => {
      timeoutHandle = setTimeout(() => {
        flushTimedOut = true;
        resolve();
      }, safetyWindowMs);
    });

    try {
      await Promise.race([
        flushPromise.catch(error => {
          flushError = error;
          return Promise.reject(error);
        }),
        timeoutPromise
      ]);

      if (!flushTimedOut) {
        try {
          await flushPromise;
        } catch (error) {
          flushError = error;
        }
      }
    } catch (error) {
      flushError = error;
    } finally {
      if (timeoutHandle) {
        clearTimeout(timeoutHandle);
      }
      if (typeof setIsAwaitingCommitSafe === 'function') {
        setIsAwaitingCommitSafe(false);
      } else {
        setIsAwaitingCommit(false);
      }

      const flushDuration = Math.round(performance.now() - commitStart);
      const transitionSource = flushTimedOut ? 'flush_timeout' : flushError ? 'flush_error' : 'commit_complete';
      transitionToState('idle', { version, source: transitionSource });

      if (flushTimedOut) {
        console.warn('[LayoutEngine] Commit flush window exceeded, falling back to idle', {
          version,
          hash,
          safetyWindowMs
        });
        logControllerState('COMMIT_FLUSH_TIMEOUT', { version, hash, safetyWindowMs });
      } else if (flushError) {
        console.error('[LayoutEngine] Commit flush failed', flushError);
        logControllerState('COMMIT_FLUSH_ERROR', { version, hash, error: (flushError as Error)?.message });
      } else {
        logControllerState('COMMIT_COMPLETE', { version, hash, flushDuration });
      }

      if (isDebugMode) {
        console.log(`[LayoutEngine] Commit ${transitionSource} v${version} hash:${hash}`, {
          flushDuration,
          flushTimedOut,
          hasError: !!flushError,
          debounceMs
        });
      }
    }
  }, [ENABLE_LAYOUT_CONTROLLER_V2, unifiedLayoutState, logControllerState, transitionToState, isDebugMode, layoutGracePeriod, setCommitHighlightSafe, setIsAwaitingCommitSafe, userCommitDelayMs]);
  
  // Schedule a debounced commit
  const scheduleCommit = useCallback((delayMs: number = userCommitDelayMs, finalLayout?: any, finalAllLayouts?: any, breakpoint?: string, activeItemId?: string) => {
    // Clear any existing timer
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
    
    // Schedule new commit
    commitTimerRef.current = setTimeout(() => {
      commitTimerRef.current = null;
      void commitLayoutChanges(finalLayout, finalAllLayouts, breakpoint, activeItemId);
    }, delayMs);
    
    logControllerState('COMMIT_SCHEDULED', { delayMs, activeItemId });
  }, [commitLayoutChanges, logControllerState, userCommitDelayMs]);
  // ========== END PHASE C2 ==========

  // Local UI state
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isAwaitingCommit, setIsAwaitingCommit] = useState(false);
  const [commitHighlightId, setCommitHighlightId] = useState<string | null>(null);

  useEffect(() => {
    if (!useSafeCommitSetters) {
      awaitingCommitSetterRef.current = null;
      commitHighlightSetterRef.current = null;
      pendingAwaitingCommitRef.current = null;
      pendingHighlightRef.current = null;
      return;
    }

    awaitingCommitSetterRef.current = setIsAwaitingCommit;
    commitHighlightSetterRef.current = setCommitHighlightId;

    if (pendingAwaitingCommitRef.current !== null) {
      setIsAwaitingCommit(pendingAwaitingCommitRef.current);
      pendingAwaitingCommitRef.current = null;
    }

    if (pendingHighlightRef.current !== null) {
      setCommitHighlightId(pendingHighlightRef.current);
      pendingHighlightRef.current = null;
    }

    return () => {
      if (awaitingCommitSetterRef.current === setIsAwaitingCommit) {
        awaitingCommitSetterRef.current = null;
      }
      if (commitHighlightSetterRef.current === setCommitHighlightId) {
        commitHighlightSetterRef.current = null;
      }
    };
  }, [useSafeCommitSetters, setIsAwaitingCommit, setCommitHighlightId]);
  // Stabilize module identities to avoid transient incomplete IDs during operations
  const stableIdentityRef = useRef<Map<string, { pluginId: string; moduleId: string }>>(new Map());
  const commitHighlightTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { currentBreakpoint } = useBreakpoint();

  // --- Adaptive rowHeight calculation (tracks container + viewport height) ---
  const [computedRowHeight, setComputedRowHeight] = useState<number>(defaultGridConfig.rowHeight);
  const [containerHeight, setContainerHeight] = useState<number>(0);

  // Observe container size
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => setContainerHeight(el.clientHeight));
    ro.observe(el);
    setContainerHeight(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    return () => {
      if (commitHighlightTimeoutRef.current) {
        clearTimeout(commitHighlightTimeoutRef.current);
      }
    };
  }, []);

  // Recompute on window resize to follow viewport height changes
  useEffect(() => {
    const onResize = () => setContainerHeight(containerRef.current ? containerRef.current.clientHeight : window.innerHeight);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    try {
      const bpMap: Record<string, keyof ResponsiveLayouts> = { xs: 'mobile', sm: 'tablet', lg: 'desktop', xl: 'wide', xxl: 'ultrawide' };
      const ourBp = bpMap[currentBreakpoint || 'lg'] || 'desktop';
      const items = (displayedLayouts?.[ourBp] || []) as LayoutItem[];

      const wantsFill = items.some(it => (it as any)?.config?.viewportFill === true) || items.length === 1;
      if (!wantsFill) {
        if (computedRowHeight !== defaultGridConfig.rowHeight) setComputedRowHeight(defaultGridConfig.rowHeight);
        return;
      }

      // Available height: prefer remaining viewport below grid top so the grid
      // shrinks with browser height; fallback to container clientHeight
      const el = containerRef.current;
      const rect = el?.getBoundingClientRect();
      const viewportAvailable = rect ? Math.max(0, window.innerHeight - rect.top - 8) : 0;
      const available = viewportAvailable > 0 ? viewportAvailable : containerHeight;
      if (available <= 0 || items.length === 0) return;

      // Determine effective vertical paddings/margins from grid config
      const marginY = defaultGridConfig.margin[1];
      const containerPadY = defaultGridConfig.containerPadding[1];

      const targetRows = Math.max(...items.map(it => it.h || 1));
      const verticalGutter = marginY * Math.max(0, targetRows - 1);
      const availableForRows = Math.max(0, available - (containerPadY * 2));
      const desired = Math.max(24, Math.floor((availableForRows - verticalGutter) / (targetRows || 1)));

      const next = Math.min(140, Math.max(36, desired));
      if (Number.isFinite(next) && next > 0 && next !== computedRowHeight) setComputedRowHeight(next);
    } catch {
      if (computedRowHeight !== defaultGridConfig.rowHeight) setComputedRowHeight(defaultGridConfig.rowHeight);
    }
  }, [displayedLayouts, currentBreakpoint, containerHeight]);
  
  // Control visibility based on context
  const { showControls } = useControlVisibility(mode);

  // Failsafe: Programmatically remove resize handles when controls should be hidden
  useEffect(() => {
    if (!showControls) {
      const removeResizeHandles = () => {
        const resizeHandles = document.querySelectorAll('.react-resizable-handle');
        resizeHandles.forEach(handle => {
          (handle as HTMLElement).style.display = 'none';
          (handle as HTMLElement).style.visibility = 'hidden';
          (handle as HTMLElement).style.pointerEvents = 'none';
        });
      };

      // Remove immediately
      removeResizeHandles();

      // Also remove after a short delay to catch any dynamically added handles
      const timeoutId = setTimeout(removeResizeHandles, 100);

      // Set up a mutation observer to catch any new resize handles
      const observer = new MutationObserver(() => {
        if (!showControls) {
          removeResizeHandles();
        }
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['class']
      });

      return () => {
        clearTimeout(timeoutId);
        observer.disconnect();
      };
    }
  }, [showControls]);
  
  // Track page ID for proper state management
  const pageIdRef = useRef<string | undefined>(pageId);
  
  // Operation deduplication tracking
  const processedOperations = useRef<Set<string>>(new Set());
  const operationCleanupTimers = useRef<Map<string, NodeJS.Timeout>>(new Map());
  
  // Bounce detection tracking
  const previousPositionsRef = useRef<Map<string, { x: number; y: number; w: number; h: number; timestamp?: number }>>(new Map());
  
  // Track intended positions (the position user actually wanted)
  const intendedPositionsRef = useRef<Map<string, { x: number; y: number; w: number; h: number; timestamp: number }>>(new Map());
  
  // Track bounce suppression window
  const bounceSuppressionRef = useRef<Map<string, number>>(new Map());
  
  // Debug logging for bounce detection
  const debugLog = useCallback((message: string, data?: any) => {
    const timestamp = performance.now();
    const stack = new Error().stack?.split('\n').slice(2, 5).join(' -> ') || 'unknown';
    console.log(`[BOUNCE-DEBUG ${timestamp.toFixed(2)}ms] ${message}`, data ? { ...data, stack } : { stack });
  }, []);

  // Handle external layout changes (from props)
  useEffect(() => {
    // Phase 5: More careful page change detection to avoid resetting after save
    // Only reset if the pageId actually changed (not just the reference)
    const pageIdChanged = pageId !== pageIdRef.current && pageId !== undefined && pageIdRef.current !== undefined;
    
    if (pageIdChanged) {
      console.log('[LayoutEngine] Page ID changed, resetting layouts', {
        oldPageId: pageIdRef.current,
        newPageId: pageId
      });
      unifiedLayoutState.resetLayouts(layouts);
      pageIdRef.current = pageId;

      // Clear bounce detection tracking when page changes
      previousPositionsRef.current.clear();
      intendedPositionsRef.current.clear();
      debugLog('🔄 Page changed - cleared bounce detection tracking', { newPageId: pageId });
      return;
    }
    
    // Update pageId ref if it was undefined before
    if (pageIdRef.current === undefined && pageId !== undefined) {
      pageIdRef.current = pageId;
    }

    // Skip if layouts are semantically identical
    if (unifiedLayoutState.compareWithCurrent(layouts)) {
      return;
    }

    // Skip during active operations to prevent interference
    if (isDragging || isResizing) {
      debugLog('EXTERNAL SYNC BLOCKED - Active operation in progress', { isDragging, isResizing });
      return;
    }

    // Update from external source
    debugLog('EXTERNAL SYNC TRIGGERED', {
      layoutsKeys: Object.keys(layouts),
      currentOperationId: currentOperationId.current
    });
    unifiedLayoutState.updateLayouts(layouts, {
      source: 'external-sync',
      timestamp: Date.now()
    });
  }, [layouts, pageId, isDragging, isResizing, unifiedLayoutState]);

  // Handle layout change - Enhanced with controller V2
  const handleLayoutChange = useCallback((layout: any[], allLayouts: any) => {
    const operationId = currentOperationId.current;
    
    debugLog('handleLayoutChange called', {
      operationId,
      isResizing,
      isDragging,
      layoutLength: layout?.length,
      allLayoutsKeys: Object.keys(allLayouts || {}),
      layoutData: layout?.map(item => ({ i: item.i, x: item.x, y: item.y, w: item.w, h: item.h }))
    });

    // RECODE V2 BLOCK: Capture layout during resize BEFORE controller checks
    // Store the layout data immediately if we're resizing
    if (isResizing && layout && layout.length > 0 && currentBreakpoint) {
      const breakpointMap: Record<string, keyof ResponsiveLayouts> = {
        xs: 'mobile',
        sm: 'tablet',
        lg: 'desktop',
        xl: 'wide',
        xxl: 'ultrawide'
      };
      
      const ourBreakpoint = breakpointMap[currentBreakpoint];
      if (ourBreakpoint && workingLayoutsRef.current) {
        // Ensure the working buffer has the structure
        if (!workingLayoutsRef.current[ourBreakpoint]) {
          workingLayoutsRef.current[ourBreakpoint] = [];
        }
        workingLayoutsRef.current[ourBreakpoint] = layout as LayoutItem[];
        
        console.log('[RECODE_V2_BLOCK] IMMEDIATE resize capture in handleLayoutChange', {
          operationId,
          breakpoint: ourBreakpoint,
          itemCount: layout.length,
          items: layout.map((item: any) => ({
            id: item.i,
            dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
          }))
        });
      }
    }

    // Controller V2: Handle layout changes based on controller state
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      const state = controllerStateRef.current;
      
      // During resize/drag operations, update working buffer only
      if (state === 'resizing' || state === 'dragging') {
        // Convert to ResponsiveLayouts format
        const convertedLayouts: ResponsiveLayouts = {
          mobile: [],
          tablet: [],
          desktop: [],
          wide: [],
          ultrawide: []
        };

        const breakpointMap: Record<string, keyof ResponsiveLayouts> = {
          xs: 'mobile',
          sm: 'tablet',
          lg: 'desktop',
          xl: 'wide',
          xxl: 'ultrawide'
        };

        // RECODE V2 BLOCK: Use the current layout for the active breakpoint during resize
        // This ensures we capture the actual resized dimensions
        if (layout && layout.length > 0 && currentBreakpoint) {
          const ourBreakpoint = breakpointMap[currentBreakpoint];
          if (ourBreakpoint) {
            convertedLayouts[ourBreakpoint] = layout as LayoutItem[];
            console.log('[RECODE_V2_BLOCK] Working buffer update with resize dimensions', {
              state,
              breakpoint: ourBreakpoint,
              itemCount: layout.length,
              items: layout.map((item: any) => ({
                id: item.i,
                dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
              }))
            });
          }
        } else {
          console.warn('[RECODE_V2_BLOCK] No layout data during resize!', {
            state,
            hasLayout: !!layout,
            layoutLength: layout?.length,
            currentBreakpoint,
            allLayoutsKeys: Object.keys(allLayouts || {})
          });
        }

        // Fill in other breakpoints from allLayouts
        Object.entries(allLayouts).forEach(([gridBreakpoint, gridLayout]: [string, any]) => {
          const ourBreakpoint = breakpointMap[gridBreakpoint];
          if (ourBreakpoint && Array.isArray(gridLayout)) {
            // Only use allLayouts if we haven't already set this breakpoint from the current layout
            if (gridBreakpoint !== currentBreakpoint || !layout) {
              convertedLayouts[ourBreakpoint] = gridLayout as LayoutItem[];
            }
          }
        });
        
        // Update working buffer only
        workingLayoutsRef.current = convertedLayouts;
        logControllerState('WORKING_BUFFER_UPDATE', {
          state,
          operationId,
          layoutItemCount: layout?.length,
          version: lastVersionRef.current,  // PHASE B: Include version in logs
          hasLayouts: Object.values(convertedLayouts).some(l => l.length > 0)
        });
        
        // Don't persist during operations
        return;
      }
      
      // During grace period, ignore all layout changes
      if (state === 'grace') {
        logControllerState('GRACE_PERIOD_IGNORE', {
          reason: 'Layout change during grace period',
          operationId,
          version: lastVersionRef.current  // PHASE B: Include version in logs
        });
        return;
      }
      
      // During commit state, also ignore
      if (state === 'commit') {
        logControllerState('COMMIT_STATE_IGNORE', {
          reason: 'Layout change during commit',
          operationId,
          version: lastVersionRef.current  // PHASE B: Include version in logs
        });
        return;
      }
    }

    // EARLY BOUNCE DETECTION: Block suspicious layout changes immediately
    if (!operationId && !isResizing && !isDragging && layout && layout.length > 0) {
      // Check if any item is trying to change to a position we've seen before (potential bounce)
      for (const item of layout) {
        const itemKey = `${item.i}`;
        const intendedPos = intendedPositionsRef.current.get(itemKey);
        
        if (intendedPos) {
          const timeSinceIntended = Date.now() - intendedPos.timestamp;
          const currentPos = { x: item.x, y: item.y, w: item.w, h: item.h };
          
          // If this change is happening soon after an intended position was set
          // and it's different from the intended position, it's likely a bounce
          if (timeSinceIntended < 1000) {
            const isDifferentFromIntended = intendedPos.x !== currentPos.x || intendedPos.y !== currentPos.y ||
                                          intendedPos.w !== currentPos.w || intendedPos.h !== currentPos.h;
            
            if (isDifferentFromIntended) {
              debugLog('🚫 EARLY BOUNCE BLOCK - Rejecting suspicious layout change', {
                itemId: item.i,
                currentPosition: currentPos,
                intendedPosition: intendedPos,
                timeSinceIntended,
                reason: 'SUSPICIOUS_CHANGE_AFTER_OPERATION'
              });
              
              // Completely reject this layout change - CRITICAL: This prevents the bounce!
              return;
            }
          }
        }
      }
    }

    // BOUNCE DETECTION: Track position changes to detect visual bounces
    if (layout && layout.length > 0) {
      layout.forEach(item => {
        const currentPos = { x: item.x, y: item.y, w: item.w, h: item.h };
        const itemKey = `${item.i}`;
        
        // Get previous position from ref
        if (!previousPositionsRef.current) {
          previousPositionsRef.current = new Map();
        }
        
        const prevPos = previousPositionsRef.current.get(itemKey);
        
        if (prevPos) {
          // Check if position changed
          const posChanged = prevPos.x !== currentPos.x || prevPos.y !== currentPos.y ||
                           prevPos.w !== currentPos.w || prevPos.h !== currentPos.h;
          
          if (posChanged) {
            // Check if this is a bounce back to an even earlier position
            const prevPrevPos = previousPositionsRef.current.get(`${itemKey}_prev`);
            if (prevPrevPos) {
              const isBouncingBack = prevPrevPos.x === currentPos.x && prevPrevPos.y === currentPos.y &&
                                   prevPrevPos.w === currentPos.w && prevPrevPos.h === currentPos.h;
              
              if (isBouncingBack) {
                debugLog('🔴 BOUNCE DETECTED! Item returned to previous position', {
                  itemId: item.i,
                  operationId,
                  isResizing,
                  isDragging,
                  previousPosition: prevPos,
                  currentPosition: currentPos,
                  bouncedBackTo: prevPrevPos,
                  timeSinceLastChange: Date.now() - (prevPos.timestamp || 0)
                });
                
                // AGGRESSIVE BOUNCE PREVENTION: Completely block bounce changes
                const intendedPos = intendedPositionsRef.current.get(itemKey);
                if (intendedPos && !operationId && !isResizing && !isDragging) {
                  // This is a bounce occurring after operation completion
                  const timeSinceIntended = Date.now() - intendedPos.timestamp;
                  
                  // Block bounces that occur within 1 second of the intended position being set
                  if (timeSinceIntended < 1000) {
                    debugLog('🚫 BLOCKING BOUNCE - Rejecting entire layout change', {
                      itemId: item.i,
                      bouncedTo: currentPos,
                      intendedPosition: intendedPos,
                      timeSinceIntended,
                      action: 'REJECTING_LAYOUT_CHANGE'
                    });
                    
                    // COMPLETELY REJECT this layout change by returning early
                    // This prevents the bounce from being processed at all
                    return;
                  }
                }
              }
            }
            
            debugLog('📍 POSITION CHANGE', {
              itemId: item.i,
              operationId,
              isResizing,
              isDragging,
              from: prevPos,
              to: currentPos,
              deltaX: currentPos.x - prevPos.x,
              deltaY: currentPos.y - prevPos.y,
              deltaW: currentPos.w - prevPos.w,
              deltaH: currentPos.h - prevPos.h
            });
            
            // Store previous position as prev_prev for bounce detection
            previousPositionsRef.current.set(`${itemKey}_prev`, prevPos);
          }
        }
        
        // Update current position with timestamp
        previousPositionsRef.current.set(itemKey, { ...currentPos, timestamp: Date.now() });
      });
    }
    
    // Check for duplicate processing
    if (operationId && processedOperations.current.has(operationId)) {
      debugLog('Skipping duplicate processing for operation', { operationId });
      return;
    }
    
    // Allow layout changes during active operations OR during resize/drag state
    const hasActiveOperation = !!operationId;
    const isInActiveState = isResizing || isDragging;
    
    if (!hasActiveOperation && !isInActiveState) {
      debugLog('Ignoring layout change - no active operation or state');
      return;
    }

    // Convert react-grid-layout layouts back to our ResponsiveLayouts format
    const convertedLayouts: ResponsiveLayouts = {
      mobile: [],
      tablet: [],
      desktop: [],
      wide: [],
      ultrawide: []
    };

    // Helper: normalize an RGL layout array to include moduleId/pluginId
    const extractPluginId = (compositeId: string): string => {
      if (!compositeId) return 'unknown';
      const tokens = compositeId.split('_');
      if (tokens.length === 1) {
        const hyphenTokens = compositeId.split('-').filter(Boolean);
        return hyphenTokens[0] || tokens[0];
      }
      const idx = tokens.findIndex(t => /^(?:[0-9a-f]{24,}|\d{12,})$/i.test(t));
      const boundary = idx > 0 ? idx : 2; // heuristic: often 2 tokens like ServiceExample_Theme
      return tokens.slice(0, boundary).join('_');
    };

    const normalizeItems = (items: any[] = []): LayoutItem[] => {
      return (items || []).map((it: any) => {
        const id = it?.i ?? '';
        let moduleId = it?.moduleId;
        if (!moduleId && it?.config?.moduleId) {
          moduleId = it.config.moduleId;
        }
        if (!moduleId) {
          moduleId = id; // Last resort fallback
        }

        // Prefer explicit pluginId, then module map metadata, then extracted fallback.
        const moduleConfig = moduleMap[moduleId] || moduleMap[id];
        const pluginId =
          it?.pluginId ||
          moduleConfig?.pluginId ||
          moduleConfig?._legacy?.pluginId ||
          extractPluginId(typeof id === 'string' ? id : '');
        
        return {
          i: id,
          x: it?.x ?? 0,
          y: it?.y ?? 0,
          w: it?.w ?? 2,
          h: it?.h ?? 2,
          moduleId: moduleId,
          pluginId: pluginId || 'unknown',
          minW: it?.minW,
          minH: it?.minH,
          isDraggable: it?.isDraggable ?? true,
          isResizable: it?.isResizable ?? true,
          static: it?.static ?? false,
          config: it?.config
        } as LayoutItem;
      });
    };

    // Map react-grid-layout breakpoints back to our breakpoint names
    const breakpointMap: Record<string, keyof ResponsiveLayouts> = {
      xs: 'mobile',
      sm: 'tablet',
      lg: 'desktop',
      xl: 'wide',
      xxl: 'ultrawide'
    };

    // Phase 5: Preserve identity for ACTIVE breakpoint only to avoid legacy/blank flash
    // The 'layout' parameter contains the active breakpoint's updated layout during drag/resize
    if (layout && currentBreakpoint) {
      const ourBreakpoint = breakpointMap[currentBreakpoint];
      if (ourBreakpoint) {
        // Build a lookup of existing items so we can copy identity/config
        const source = (workingLayoutsRef.current || canonicalLayoutsRef.current || currentLayouts) as ResponsiveLayouts;
        const existing: LayoutItem[] = (source?.[ourBreakpoint] as LayoutItem[]) || [];
        const existingMap = new Map(existing.map(it => [it.i, it]));

        convertedLayouts[ourBreakpoint] = (layout as any[]).map((it: any) => {
          const id = it?.i ?? '';
          const pos = { x: it?.x ?? 0, y: it?.y ?? 0, w: it?.w ?? 2, h: it?.h ?? 2 };
          const base = existingMap.get(id);
          // Keep identity/config from base; only update position/size
          return base ? ({ ...base, ...pos } as LayoutItem) : normalizeItems([it])[0];
        });
        
        // RECODE V2 BLOCK: Enhanced item-level dimension tracking
        if (isResizing || operationId?.includes('resize')) {
          console.log('[RECODE_V2_BLOCK] onLayoutChange during resize - item dimensions', {
            operationId,
            breakpoint: ourBreakpoint,
            isResizing,
            itemDimensions: layout.map((item: any) => ({
              id: item.i,
              dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
            }))
          });
        }
        
        // Enhanced debugging to understand what dimensions we're getting
        if (isDebugMode) {
          console.log(`[LayoutEngine] Using current breakpoint layout for ${ourBreakpoint}`, {
            operationId,
            isResizing,
            isDragging,
            itemCount: layout.length,
            layoutItems: layout.map((item: any) => ({
              i: item.i,
              x: item.x,
              y: item.y,
              w: item.w,
              h: item.h
            }))
          });
        }
      }
    }

    // Fill in other breakpoints from allLayouts (no merge to avoid display regressions)
    Object.entries(allLayouts).forEach(([gridBreakpoint, gridLayout]: [string, any]) => {
      const ourBp = breakpointMap[gridBreakpoint];
      if (ourBp && Array.isArray(gridLayout)) {
        if (gridBreakpoint !== currentBreakpoint || !layout) {
          convertedLayouts[ourBp] = normalizeItems(gridLayout as any[]);
        }
      }
    });

    // PHASE B: Determine the origin with version information
    const origin: LayoutChangeOrigin = {
      source: isDragging ? 'user-drag' : isResizing ? 'user-resize' : 'external-sync',
      timestamp: Date.now(),
      operationId: currentOperationId.current || `late-${Date.now()}`,
      version: ENABLE_LAYOUT_CONTROLLER_V2 ? lastVersionRef.current : undefined
    };

    debugLog(`Processing layout change from ${origin.source}`, {
      operationId,
      convertedLayouts: Object.keys(convertedLayouts).map(bp => ({
        breakpoint: bp,
        itemCount: convertedLayouts[bp as keyof ResponsiveLayouts]?.length || 0
      }))
    });
    
    // Update through unified state management
    unifiedLayoutState.updateLayouts(convertedLayouts, origin);
    
    // Mark operation as processed
    if (operationId) {
      processedOperations.current.add(operationId);
      debugLog('Marked operation as processed', { operationId });
      
      // CAPTURE INTENDED POSITIONS: Store the final positions from user operations
      if (origin.source === 'user-resize' || origin.source === 'user-drag') {
        layout?.forEach(item => {
          const itemKey = `${item.i}`;
          const intendedPos = { x: item.x, y: item.y, w: item.w, h: item.h, timestamp: Date.now() };
          intendedPositionsRef.current.set(itemKey, intendedPos);
          
          debugLog('💾 CAPTURED INTENDED POSITION', {
            itemId: item.i,
            operationId,
            operationType: origin.source,
            intendedPosition: intendedPos
          });
        });
      }
    }
  }, [isDragging, isResizing, unifiedLayoutState, debugLog]);

  // ========== PHASE C3: Enhanced Drag Operation Support ==========
  // Handle drag start - Enhanced with controller V2 and Phase C improvements
  const handleDragStart = useCallback(() => {
    const operationId = `drag-${Date.now()}`;
    currentOperationId.current = operationId;
    setIsDragging(true);
    
    // Controller V2: Use state transition function
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      transitionToState('dragging', { operationId });
      workingLayoutsRef.current = JSON.parse(JSON.stringify(canonicalLayoutsRef.current || currentLayouts));
      logControllerState('DRAG_OPERATION_STARTED', {
        operationId,
        copiedCanonicalToWorking: true,
        version: lastVersionRef.current
      });
    }
    
    unifiedLayoutState.startOperation(operationId);
  }, [unifiedLayoutState, ENABLE_LAYOUT_CONTROLLER_V2, logControllerState, currentLayouts, transitionToState]);

  // Handle drag stop - Enhanced with controller V2 and Phase C improvements
  const handleDragStop = useCallback((layout: any[], oldItem: any, newItem: any) => {
    if (currentOperationId.current) {
      const operationId = currentOperationId.current;
      
      // Controller V2: Use unified commit process
      if (ENABLE_LAYOUT_CONTROLLER_V2) {
        lastVersionRef.current += 1;
        transitionToState('grace', {
          operationId,
          newVersion: lastVersionRef.current
        });
        
        // RECODE V2 BLOCK: Pass the final layout data to scheduleCommit for accurate positions
        // Build allLayouts object with current breakpoint's layout (include alias)
        const allLayouts: any = {};
        const toGridAlias = (name: string): string => {
          const m: Record<string, string> = { mobile: 'xs', tablet: 'sm', desktop: 'lg', wide: 'xl', ultrawide: 'xxl' };
          return m[name] || name;
        };
        if (currentBreakpoint) {
          allLayouts[currentBreakpoint] = layout;
          const semanticToGrid: Record<string, string> = { mobile: 'xs', tablet: 'sm', desktop: 'lg', wide: 'xl', ultrawide: 'xxl' };
          const gridKey = semanticToGrid[currentBreakpoint];
          if (gridKey) {
            allLayouts[gridKey] = layout;
          }
        }
        
        // Update working buffer for the active breakpoint with final drag positions
        if (layout && currentBreakpoint) {
          const toOurBreakpoint = (bp: string): keyof ResponsiveLayouts | undefined => {
            const map: Record<string, keyof ResponsiveLayouts> = {
              xs: 'mobile', sm: 'tablet', lg: 'desktop', xl: 'wide', xxl: 'ultrawide',
              mobile: 'mobile', tablet: 'tablet', desktop: 'desktop', wide: 'wide', ultrawide: 'ultrawide'
            };
            return map[bp];
          };
          const ourBreakpoint = toOurBreakpoint(currentBreakpoint);
          if (ourBreakpoint && workingLayoutsRef.current) {
            workingLayoutsRef.current[ourBreakpoint] = layout as LayoutItem[];
          }
        }

        // Schedule debounced commit with final layout data and normalized breakpoint
        const normalizedBreakpoint = currentBreakpoint ? toGridAlias(currentBreakpoint) : currentBreakpoint;
        const activeItemId = newItem?.i || oldItem?.i || null;
        scheduleCommit(userCommitDelayMs, layout, allLayouts, normalizedBreakpoint, activeItemId || undefined);
      }
      
      unifiedLayoutState.stopOperation(currentOperationId.current);
      
      // Delay clearing operation ID to catch late events
      setTimeout(() => {
        if (currentOperationId.current === operationId) {
          currentOperationId.current = null;
        }
      }, 200);
    }
    setIsDragging(false);
  }, [unifiedLayoutState, ENABLE_LAYOUT_CONTROLLER_V2, transitionToState, scheduleCommit, userCommitDelayMs]);

  // Handle resize start - Enhanced with controller V2 and Phase C improvements
  const handleResizeStart = useCallback(() => {
    const operationId = `resize-${Date.now()}`;
    currentOperationId.current = operationId;
    setIsResizing(true);
    
    // Controller V2: Use state transition function
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      transitionToState('resizing', { operationId });
      // RECODE V2 BLOCK: Ensure proper initialization with all breakpoints including ultrawide
      const sourceLayouts = canonicalLayoutsRef.current || currentLayouts || {
        mobile: [],
        tablet: [],
        desktop: [],
        wide: [],
        ultrawide: []
      };
      
      // Ensure ultrawide field exists
      if (!sourceLayouts.ultrawide) {
        sourceLayouts.ultrawide = [];
      }
      
      workingLayoutsRef.current = JSON.parse(JSON.stringify(sourceLayouts));
      
      console.log('[RECODE_V2_BLOCK] RESIZE START - Working buffer initialized', {
        operationId,
        hasCanonical: !!canonicalLayoutsRef.current,
        hasCurrent: !!currentLayouts,
        workingBufferBreakpoints: Object.keys(workingLayoutsRef.current || {}),
        itemCounts: Object.entries(workingLayoutsRef.current || {}).map(([bp, items]) => ({
          breakpoint: bp,
          count: (items as any[])?.length || 0
        }))
      });
      
      logControllerState('RESIZE_OPERATION_STARTED', {
        operationId,
        copiedCanonicalToWorking: true,
        version: lastVersionRef.current
      });
    }
    
    unifiedLayoutState.startOperation(operationId);
    debugLog('RESIZE START', { operationId, isResizing: true });
  }, [unifiedLayoutState, debugLog, ENABLE_LAYOUT_CONTROLLER_V2, logControllerState, currentLayouts, transitionToState]);

  // Handle resize stop - Enhanced with controller V2 and Phase C improvements
  const handleResizeStop = useCallback((layout: any, oldItem: any, newItem: any, placeholder: any, e: any, element: any) => {
    if (currentOperationId.current) {
      const operationId = currentOperationId.current;
      
      // RECODE V2 BLOCK: Enhanced resize stop logging
      console.log('[RECODE_V2_BLOCK] RESIZE STOP - Capturing final dimensions', {
        operationId,
        itemId: newItem?.i,
        oldDimensions: oldItem ? { w: oldItem.w, h: oldItem.h, x: oldItem.x, y: oldItem.y } : null,
        newDimensions: newItem ? { w: newItem.w, h: newItem.h, x: newItem.x, y: newItem.y } : null,
        layoutItemCount: layout?.length,
        currentBreakpoint,
        timestamp: Date.now()
      });
      
      // Controller V2: Use unified commit process
      if (ENABLE_LAYOUT_CONTROLLER_V2) {
        lastVersionRef.current += 1;
        transitionToState('grace', {
          operationId,
          newVersion: lastVersionRef.current
        });
        
        // RECODE V2 BLOCK: Pass the final layout data to scheduleCommit for accurate dimensions
        // Build allLayouts object with current breakpoint's layout (include both semantic and grid aliases)
        const allLayouts: any = {};
        const toGridAlias = (name: string): string => {
          const m: Record<string, string> = { mobile: 'xs', tablet: 'sm', desktop: 'lg', wide: 'xl', ultrawide: 'xxl' };
          return m[name] || name;
        };
        
        // Add the current breakpoint's final layout
        if (currentBreakpoint) {
          // currentBreakpoint might already be a grid key (xs/sm/lg/xl/xxl) or a semantic key
          allLayouts[currentBreakpoint] = layout;
          // Also add alias to ensure commit path can normalize either form
          const semanticToGrid: Record<string, string> = { mobile: 'xs', tablet: 'sm', desktop: 'lg', wide: 'xl', ultrawide: 'xxl' };
          const gridKey = semanticToGrid[currentBreakpoint];
          if (gridKey) {
            allLayouts[gridKey] = layout;
          }
        }
        
        // RECODE V2 BLOCK: Immediately update working buffer with final layout
        // This ensures the commit has the correct data
        if (layout && currentBreakpoint) {
          const toOurBreakpoint = (bp: string): keyof ResponsiveLayouts | undefined => {
            const map: Record<string, keyof ResponsiveLayouts> = {
              xs: 'mobile', sm: 'tablet', lg: 'desktop', xl: 'wide', xxl: 'ultrawide',
              mobile: 'mobile', tablet: 'tablet', desktop: 'desktop', wide: 'wide', ultrawide: 'ultrawide'
            };
            return map[bp];
          };

          const ourBreakpoint = toOurBreakpoint(currentBreakpoint);
          if (ourBreakpoint && workingLayoutsRef.current) {
            workingLayoutsRef.current[ourBreakpoint] = layout as LayoutItem[];
            console.log('[RECODE_V2_BLOCK] Updated working buffer with final resize dimensions', {
              operationId,
              breakpoint: ourBreakpoint,
              itemCount: layout.length,
              dimensions: layout.map((item: any) => ({
                id: item.i,
                w: item.w,
                h: item.h
              }))
            });
          }
        }
        
        // Schedule debounced commit with final layout data
        // Pass a normalized grid alias for breakpoint to maximize compatibility
        const normalizedBreakpoint = currentBreakpoint ? toGridAlias(currentBreakpoint) : currentBreakpoint;
        scheduleCommit(userCommitDelayMs, layout, allLayouts, normalizedBreakpoint, newItem?.i);
      }
      
      unifiedLayoutState.stopOperation(operationId);
      debugLog('RESIZE STOP - Starting grace period', { operationId });
      
      // Schedule cleanup of processed operation tracking
      const cleanupTimer = setTimeout(() => {
        processedOperations.current.delete(operationId);
        operationCleanupTimers.current.delete(operationId);
        debugLog('Cleaned up processed operation tracking', { operationId });
      }, 300); // 300ms - longer than grace period to ensure all related processing is complete
      
      operationCleanupTimers.current.set(operationId, cleanupTimer);
      
      // Keep operation ID active for extended grace period to catch all final layout changes
      setTimeout(() => {
        if (currentOperationId.current === operationId) {
          currentOperationId.current = null;
          debugLog('GRACE PERIOD ENDED', { operationId });
        }
      }, 200); // Extended grace period to catch all React Grid Layout events
    }
    setIsResizing(false);
    debugLog('RESIZE STATE SET TO FALSE');
  }, [unifiedLayoutState, debugLog, ENABLE_LAYOUT_CONTROLLER_V2, transitionToState, scheduleCommit, currentBreakpoint, userCommitDelayMs]);
  // ========== END PHASE C3 ==========

  // Handle drag over for drop zone functionality
  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    
    // Check if the dataTransfer contains module data
    const types = e.dataTransfer.types;
    const hasModuleData = types.includes('module') || types.includes('text/plain');
    
    if (hasModuleData && mode === RenderMode.STUDIO) {
      setIsDragOver(true);
    }
  }, [mode]);

  // Handle drag leave
  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  // Handle drop for adding new modules
  const handleDrop = useCallback(async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    
    if (mode !== RenderMode.STUDIO) return;
    
    try {
      // Try to get the module data from the drag event
      let moduleDataStr = e.dataTransfer.getData('module');
      
      // If module data is not available, try text/plain as fallback
      if (!moduleDataStr) {
        moduleDataStr = e.dataTransfer.getData('text/plain');
      }
      
      if (!moduleDataStr) {
        console.error('No module data found in drop event');
        return;
      }
      
      // Parse the module data
      const moduleData = JSON.parse(moduleDataStr);
      if (isDebugMode) {
        console.log('[AddTrace] Parsed module data', moduleData);
      }
      
      // DEBUG: Log controller + commit tracker state prior to add
      if (isDebugMode) {
        const tracker = getLayoutCommitTracker();
        const pending = tracker.getPendingCommits();
        const last = unifiedLayoutState.getLastCommitMeta?.();
        const committed = unifiedLayoutState.getCommittedLayouts?.();
        const committedCounts = committed ? Object.fromEntries(Object.entries(committed).map(([bp, arr]) => [bp, (arr as any[])?.length || 0])) : {};
        console.log('[AddTrace] Pre-Add State', {
          controllerState: controllerStateRef.current,
          hasCommitTimer: !!commitTimerRef.current,
          pendingCommits: pending.length,
          lastCommit: last,
          committedCounts
        });
      }

      // Phase 3: Commit barrier - await any pending commits before adding
      if (ENABLE_LAYOUT_CONTROLLER_V2) {
        const state = controllerStateRef.current;
        
        // If we're in grace or commit state, or have pending commits, wait for flush
        if (state === 'grace' || state === 'commit' || commitTimerRef.current) {
          if (isDebugMode) {
            console.log('[LayoutEngine] Awaiting flush before drop-add', {
              state,
              hasPendingCommit: !!commitTimerRef.current
            });
          }
          
          // Await the flush to ensure we're working with committed layouts
          await unifiedLayoutState.flush();
          
          if (isDebugMode) {
            const committed = unifiedLayoutState.getCommittedLayouts?.();
            const committedCounts = committed ? Object.fromEntries(Object.entries(committed).map(([bp, arr]) => [bp, (arr as any[])?.length || 0])) : {};
            const last = unifiedLayoutState.getLastCommitMeta?.();
            console.log('[AddTrace] Post-Flush State', { lastCommit: last, committedCounts });
          }
          
          if (isDebugMode) {
            console.log('[LayoutEngine] Flush complete, proceeding with drop-add');
          }
        }
      }
      
      // Calculate the drop position relative to the grid
      const rect = e.currentTarget.getBoundingClientRect();
      const x = Math.floor((e.clientX - rect.left) / 120); // Grid cell width approximation
      const y = Math.floor((e.clientY - rect.top) / 80);   // Grid cell height approximation
      
      // Create a unique ID for the module
      const uniqueId = `${moduleData.pluginId}_${moduleData.moduleId}_${Date.now()}`;
      
      // Default size for new modules
      const defaultWidth = 4;
      const defaultHeight = 3;
      
      // Create the new layout item
      const newItem: LayoutItem = {
        i: uniqueId,
        x: Math.max(0, x),
        y: Math.max(0, y),
        w: defaultWidth,
        h: defaultHeight,
        moduleId: moduleData.moduleId, // Use the actual moduleId from drag data, not the unique ID
        pluginId: moduleData.pluginId,
        config: {
          moduleId: moduleData.moduleId,
          displayName: moduleData.displayName || moduleData.moduleName,
          ...moduleData.config,
          // Preserve original item identity for adapter round-trips
          _originalItem: {
            i: uniqueId,
            pluginId: moduleData.pluginId,
            moduleId: moduleData.moduleId,
            args: moduleData.config || {}
          }
        },
        isDraggable: true,
        isResizable: true,
        static: false
      };
      
      if (isDebugMode) {
        console.log('[AddTrace] Adding new item to layout', {
          id: newItem.i,
          pluginId: newItem.pluginId,
          moduleId: newItem.moduleId,
          x: newItem.x,
          y: newItem.y,
          w: newItem.w,
          h: newItem.h
        });
      }
      
      // Phase 3: Use committed layouts as the base for adding new items
      // Harden against stale committed state after page change: if currentLayouts is empty
      // but committed has items, prefer currentLayouts (empty) to avoid cross-page bleed.
      const committedBase = unifiedLayoutState.getCommittedLayouts();
      const isEmptyLayouts = (l?: ResponsiveLayouts | null) => {
        if (!l) return true;
        const keys: (keyof ResponsiveLayouts)[] = ['mobile','tablet','desktop','wide','ultrawide'];
        return keys.every(k => !Array.isArray((l as any)[k]) || ((l as any)[k] || []).length === 0);
      };
      const layoutsToUpdate = (committedBase && !(isEmptyLayouts(currentLayouts) && !isEmptyLayouts(committedBase)))
        ? committedBase
        : currentLayouts;
      const updatedLayouts = { ...layoutsToUpdate };
      Object.keys(updatedLayouts).forEach(breakpoint => {
        const currentLayout = updatedLayouts[breakpoint as keyof ResponsiveLayouts];
        if (currentLayout) {
          updatedLayouts[breakpoint as keyof ResponsiveLayouts] = [
            ...currentLayout,
            newItem
          ];
        }
      });
      
      // Update through unified state management with version tracking
      const version = lastVersionRef.current + 1;
      lastVersionRef.current = version;
      
      if (isDebugMode) {
        const counts = Object.fromEntries(Object.entries(updatedLayouts).map(([bp, arr]) => [bp, (arr as any[])?.length || 0]));
        console.log('[AddTrace] Committing layouts after drop', { counts });
      }

      unifiedLayoutState.updateLayouts(updatedLayouts, {
        source: 'drop-add',
        timestamp: Date.now(),
        version
      });
      
      onItemAdd?.(newItem);
      
      // Select the newly added item (batched to prevent additional renders)
      setTimeout(() => {
        setSelectedItem(uniqueId);
        onItemSelect?.(uniqueId);
      }, 0);
      
    } catch (error) {
      console.error('Error handling drop:', error);
    }
  }, [mode, unifiedLayoutState, onItemAdd, onItemSelect]);

  // Handle item selection
  const handleItemClick = useCallback((itemId: string) => {
    if (mode === RenderMode.STUDIO) {
      // Don't toggle selection during drag or resize operations
      if (isDragging || isResizing) {
        return;
      }
      setSelectedItem(prev => prev === itemId ? null : itemId);
      onItemSelect?.(itemId);
    }
  }, [mode, onItemSelect, isDragging, isResizing]);

  // Handle item removal
  const handleItemRemove = useCallback((itemId: string) => {
    
    // Use displayedLayouts for consistency with controller V2
    const layoutsToUpdate = ENABLE_LAYOUT_CONTROLLER_V2 ? displayedLayouts : currentLayouts;
    
    // Create new layouts with the item removed from all breakpoints
    const updatedLayouts: ResponsiveLayouts = {
      mobile: layoutsToUpdate.mobile?.filter((item: LayoutItem) => item.i !== itemId) || [],
      tablet: layoutsToUpdate.tablet?.filter((item: LayoutItem) => item.i !== itemId) || [],
      desktop: layoutsToUpdate.desktop?.filter((item: LayoutItem) => item.i !== itemId) || [],
      wide: layoutsToUpdate.wide?.filter((item: LayoutItem) => item.i !== itemId) || []
    };

    // Update through unified state management
    unifiedLayoutState.updateLayouts(updatedLayouts, {
      source: 'user-remove',
      timestamp: Date.now(),
      operationId: `remove-${itemId}-${Date.now()}`
    });
    
    // Controller V2: Update buffers when removing items
    if (ENABLE_LAYOUT_CONTROLLER_V2) {
      canonicalLayoutsRef.current = updatedLayouts;
      workingLayoutsRef.current = updatedLayouts;
      logControllerState('ITEM_REMOVED', { itemId });
    }

    // Clear selection if the removed item was selected
    if (selectedItem === itemId) {
      setSelectedItem(null);
    }

    // Call the external callback if provided
    onItemRemove?.(itemId);
  }, [currentLayouts, displayedLayouts, unifiedLayoutState, selectedItem, onItemRemove, ENABLE_LAYOUT_CONTROLLER_V2, logControllerState]);

  // Create ultra-stable module map with deep comparison
  const stableModuleMapRef = useRef<Record<string, ModuleConfig>>({});

  const moduleMap = useMemo(() => {
    const nextMap = modules.reduce<Record<string, ModuleConfig>>((map, module) => {
      map[module.id] = module;
      return map;
    }, {});

    stableModuleMapRef.current = nextMap;
    return nextMap;
  }, [modules]);

  // Render grid items - Use displayedLayouts instead of currentLayouts
  const renderGridItems = useCallback(() => {
    const currentLayout =
      displayedLayouts[currentBreakpoint as keyof ResponsiveLayouts] ||
      displayedLayouts.desktop ||
      displayedLayouts.wide ||
      [];
    
    
    
    if (isDebugMode) {
      const preview = currentLayout.map((i: any) => ({ i: i.i, pluginId: i.pluginId, moduleId: i.moduleId, x: i.x, y: i.y, w: i.w, h: i.h }));
      console.log('[AddTrace] Render pass items', { breakpoint: currentBreakpoint, count: preview.length, items: preview });
    }

    return currentLayout.map((item: LayoutItem) => {
      // Try to find the module by moduleId with multiple strategies
      let module = moduleMap[item.moduleId];
      const isCommitHighlighted = commitHighlightId === item.i;
      
      // If direct lookup fails, try a conservative fallback only (avoid cross-binding by pluginId)
      let resolvedVia: 'direct' | 'sanitized' | 'fallback' | 'none' = module ? 'direct' : 'none';
      if (!module) {
        // Try without underscores (sanitized id) once
        if (item.moduleId && typeof item.moduleId === 'string') {
          const sanitizedModuleId = item.moduleId.replace(/_/g, '');
          module = moduleMap[sanitizedModuleId];
          if (module) resolvedVia = 'sanitized';
        }
      }
      
      if (!module) {
        
        
        // Instead of returning null, try to render with the layout item data directly
        // This allows the LegacyModuleAdapter to handle the module loading
        const isSelected = selectedItem === item.i;
        const isStudioMode = showControls; // Use control visibility instead of just mode check

        // Helper function to extract plugin ID from composite ID
        const extractPluginIdFromComposite = (compositeId: string): string => {
          if (!compositeId) return 'unknown';
          const tokens = compositeId.split('_');
          if (tokens.length === 1) {
            const hyphenTokens = compositeId.split('-').filter(Boolean);
            return hyphenTokens[0] || tokens[0];
          }
          const idx = tokens.findIndex(t => /^(?:[0-9a-f]{24,}|\d{12,})$/i.test(t));
          const boundary = idx > 0 ? idx : 2;
          return tokens.slice(0, boundary).join('_');
        };

        // Prefer explicit pluginId first, then module metadata, then extracted fallback.
        const mappedModule = moduleMap[item.moduleId]
          || Object.values(moduleMap).find((moduleConfig: any) => moduleConfig?._legacy?.moduleId === item.moduleId);
        let fallbackPluginId = (item as any)?.config?._originalItem?.pluginId
          || item.pluginId
          || mappedModule?.pluginId
          || mappedModule?._legacy?.pluginId;
        if (!fallbackPluginId || fallbackPluginId === 'unknown') {
          const potentialPluginId = extractPluginIdFromComposite(item.moduleId || '');
          fallbackPluginId = potentialPluginId || fallbackPluginId;
        }

        // Extract moduleId more robustly
        // First check if moduleId is in config (from args in database)
        let extractedModuleId = (item.config as any)?.moduleId;
        
        // CRITICAL FIX: If item.moduleId is just the module name (not composite), use it directly
        if (item.moduleId && !item.moduleId.includes('_')) {
          extractedModuleId = item.moduleId;
          console.log('[LayoutEngine] Using simple moduleId directly:', item.moduleId);
        }
        
        console.log('[LayoutEngine] Module extraction for fallback path:', {
          itemId: item.i,
          itemModuleId: item.moduleId,
          configModuleId: (item.config as any)?.moduleId,
          config: item.config,
          pluginId: fallbackPluginId,
          extractedSoFar: extractedModuleId
        });
        
        // If not in config and item.moduleId looks like a composite ID, try to extract
        if (!extractedModuleId && item.moduleId && item.moduleId.includes('_')) {
          const parts = item.moduleId.split('_');
          const isTimestamp = (s: string) => /^\d{12,}$/.test(s);
          extractedModuleId = parts.reverse().find(p => p && !isTimestamp(p) && p !== fallbackPluginId);
        }
        
        // Otherwise use item.moduleId as-is
        if (!extractedModuleId) {
          extractedModuleId = item.moduleId;
        }
        
        console.log('[LayoutEngine] Final extracted moduleId:', extractedModuleId);

        // Create stable breakpoint object
        const breakpointConfig = {
          name: currentBreakpoint,
          width: 0,
          height: 0,
          orientation: 'landscape' as const,
          pixelRatio: 1,
          containerWidth: 1200,
          containerHeight: 800,
        };

        if (isDebugMode) {
          console.log('[AddTrace] Resolve item (fallback path)', {
            itemId: item.i,
            pluginId: fallbackPluginId,
            requestedModuleId: item.moduleId,
            extractedModuleId
          });
        }

        return (
          <Box
            key={item.i}
            className={`layout-item react-grid-item ${isSelected ? 'layout-item--selected selected' : ''} ${isStudioMode ? 'layout-item--studio' : ''} ${isCommitHighlighted ? 'layout-item--commit-highlight' : ''}`}
            onClick={() => handleItemClick(item.i)}
            data-grid={item}
            sx={{
              position: 'relative',
              backgroundColor: showControls ? theme.palette.background.paper : 'transparent',
              border: showControls ? `1px solid ${theme.palette.divider}` : 'none',
              borderRadius: showControls ? 1 : 0,
              overflow: 'hidden',
              transition: 'background-color 0.3s ease, border-color 0.3s ease',
              ...(isCommitHighlighted && {
                borderColor: theme.palette.success.main,
                boxShadow: `0 0 0 2px ${theme.palette.success.main}33`
              }),
              ...(isSelected && {
                borderColor: theme.palette.primary.main,
                boxShadow: `0 0 0 2px ${theme.palette.primary.main}20`
              })
            }}
          >
            {/* Use the legacy GridItemControls component for consistent behavior */}
            {showControls && (
              <GridItemControls
                isSelected={isSelected}
                onConfig={() => onItemConfig?.(item.i)}
                onRemove={() => handleItemRemove(item.i)}
              />
            )}
          {(() => {
            const activeLayout =
              displayedLayouts[currentBreakpoint as keyof ResponsiveLayouts] ||
              displayedLayouts.desktop || displayedLayouts.wide || [];
            const wantsFullWidth = !showControls && (
              (Array.isArray(activeLayout) && activeLayout.length === 1) ||
              (Array.isArray(activeLayout) && activeLayout.some((it: any) => it?.config?.viewportFill || it?.config?.fullWidth))
            );
            return (
              <ModuleRenderer
                pluginId={fallbackPluginId}
                moduleId={extractedModuleId}
                additionalProps={{ ...(item.config || {}), ...(wantsFullWidth ? { viewportFill: true, centerContent: false } : {}) }}
                fallback={<div style={{ padding: 8 }}>Loading module...</div>}
              />
            );
          })()}
        </Box>
      );
      }

      const isSelected = selectedItem === item.i;
      const isStudioMode = showControls; // Use control visibility instead of just mode check

      if (isDebugMode) {
        console.log('[AddTrace] Resolve item', {
          itemId: item.i,
          requestedPluginId: item.pluginId,
          requestedModuleId: item.moduleId,
          resolvedModuleKey: module?.id || '(legacy)',
          via: resolvedVia
        });
      }

      return (
        <Box
          key={item.i}
          className={`layout-item react-grid-item ${isSelected ? 'layout-item--selected selected' : ''} ${isStudioMode ? 'layout-item--studio' : ''} ${isCommitHighlighted ? 'layout-item--commit-highlight' : ''}`}
          onClick={() => handleItemClick(item.i)}
          data-grid={item}
          sx={{
            position: 'relative',
            backgroundColor: showControls ? theme.palette.background.paper : 'transparent',
            border: showControls ? `1px solid ${theme.palette.divider}` : 'none',
            borderRadius: showControls ? 1 : 0,
            overflow: 'hidden',
            transition: 'background-color 0.3s ease, border-color 0.3s ease',
            ...(isCommitHighlighted && {
              borderColor: theme.palette.success.main,
              boxShadow: `0 0 0 2px ${theme.palette.success.main}33`
            }),
            ...(isSelected && {
              borderColor: theme.palette.primary.main,
              boxShadow: `0 0 0 2px ${theme.palette.primary.main}20`
            })
          }}
        >
          {/* Use the legacy GridItemControls component for consistent behavior */}
          {showControls && (
            <GridItemControls
              isSelected={isSelected}
              onConfig={() => onItemConfig?.(item.i)}
              onRemove={() => handleItemRemove(item.i)}
            />
          )}
          {(() => {
            // Helper function to extract plugin ID (local copy)
            const extractPluginIdLocal = (compositeId: string): string => {
              if (!compositeId) return 'unknown';
              const tokens = compositeId.split('_');
              if (tokens.length === 1) {
                const hyphenTokens = compositeId.split('-').filter(Boolean);
                return hyphenTokens[0] || tokens[0];
              }
              const idx = tokens.findIndex(t => /^(?:[0-9a-f]{24,}|\d{12,})$/i.test(t));
              const boundary = idx > 0 ? idx : 2;
              return tokens.slice(0, boundary).join('_');
            };

            const candidateModuleId = (item.config as any)?.moduleId || (item as any)?.config?._originalItem?.moduleId || item.moduleId;
            const mappedModule = moduleMap[candidateModuleId]
              || Object.values(moduleMap).find((moduleConfig: any) => moduleConfig?._legacy?.moduleId === candidateModuleId);
            const directPluginId = (item as any)?.config?._originalItem?.pluginId || item.pluginId;
            const hasDirectPluginId = typeof directPluginId === 'string'
              && directPluginId.trim().length > 0
              && directPluginId !== 'unknown';
            const candidatePluginId = hasDirectPluginId
              ? directPluginId
              : (mappedModule?.pluginId
                || mappedModule?._legacy?.pluginId
                || extractPluginIdLocal(item.moduleId || (item as any)?.config?._originalItem?.moduleId || (item.i || '')));

            // Use last known-good identity if it is more specific/complete
            const prev = stableIdentityRef.current.get(item.i);
            let effectivePluginId = candidatePluginId;
            let effectiveModuleId = candidateModuleId;

            if (prev) {
              // Prefer composite plugin ids with an underscore
              const prevIsComposite = prev.pluginId && prev.pluginId.includes('_');
              const candIsComposite = effectivePluginId && effectivePluginId.includes('_');
              if (prevIsComposite && !candIsComposite) {
                effectivePluginId = prev.pluginId;
              }
              // Prefer previously known moduleId if current is empty/falsy
              if (!effectiveModuleId && prev.moduleId) {
                effectiveModuleId = prev.moduleId;
              }
            }

            // Update cache only when both values look usable
            if (effectivePluginId && effectiveModuleId) {
              stableIdentityRef.current.set(item.i, {
                pluginId: effectivePluginId,
                moduleId: effectiveModuleId,
              });
            }

            if (isDebugMode) {
              console.log('[ModuleRenderTrace] Identity', {
                id: item.i,
                candidatePluginId,
                candidateModuleId,
                effectivePluginId,
                effectiveModuleId,
              });
            }

            const wantsFullWidth = !showControls && (
              (Array.isArray(activeLayout) && activeLayout.length === 1) ||
              (Array.isArray(activeLayout) && activeLayout.some((it: any) => it?.config?.viewportFill || it?.config?.fullWidth))
            );
            return (
              <ModuleRenderer
                pluginId={effectivePluginId}
                moduleId={effectiveModuleId}
                additionalProps={{ ...item.config, ...(wantsFullWidth ? { viewportFill: true, centerContent: false } : {}) }}
                fallback={<div style={{ padding: 8 }}>Loading module...</div>}
              />
            );
          })()}
        </Box>
      );
    });
  }, [
    displayedLayouts,  // Changed from unifiedLayoutState.layouts
    currentBreakpoint,
    moduleMap,
    selectedItem,
    mode,
    lazyLoading,
    preloadPlugins,
    handleItemClick,
    handleItemRemove,
    onItemConfig,
  ]);

  // Grid layout props - A4: Use displayedLayouts instead of currentLayouts
  const gridProps = useMemo(() => {
    // Convert ResponsiveLayouts to the format expected by react-grid-layout
    const reactGridLayouts: any = {};
    // Determine if we should present a full-width experience (published mode, single item or explicit flag)
    const bpMap: Record<string, string> = { mobile: 'xs', tablet: 'sm', desktop: 'lg', wide: 'xl', ultrawide: 'xxl' };
    const activeLayout =
      displayedLayouts[currentBreakpoint as keyof ResponsiveLayouts] ||
      displayedLayouts.desktop || displayedLayouts.wide || [];
    const wantsFullWidth = !showControls && (
      (Array.isArray(activeLayout) && activeLayout.length === 1) ||
      (Array.isArray(activeLayout) && activeLayout.some((it: any) => it?.config?.viewportFill || it?.config?.fullWidth))
    );

    const adjustForFullWidth = (items: any[], gridBp: string) => {
      if (!wantsFullWidth || !Array.isArray(items) || items.length === 0) return items;
      const cols = (defaultGridConfig.cols as any)[gridBp] || 12;
      // Expand the first item to full width
      return items.map((it: any, idx: number) => idx === 0 ? { ...it, x: 0, w: cols } : it);
    };

    Object.entries(displayedLayouts).forEach(([breakpoint, layout]) => {
      if (layout && Array.isArray(layout) && layout.length > 0) {
        // Map breakpoint names to react-grid-layout breakpoint names
        const breakpointMap: Record<string, string> = {
          mobile: 'xs',
          tablet: 'sm',
          desktop: 'lg',
          wide: 'xl',
          ultrawide: 'xxl'
        };
        const gridBreakpoint = breakpointMap[breakpoint] || breakpoint;
        reactGridLayouts[gridBreakpoint] = adjustForFullWidth(layout, gridBreakpoint);
      }
    });

    return {
      className: `layout-engine layout-engine--${mode}`,
      layouts: reactGridLayouts,
      onLayoutChange: handleLayoutChange,
      onDragStart: handleDragStart,
      onDragStop: handleDragStop,
      onResizeStart: handleResizeStart,
      onResizeStop: handleResizeStop,
      isDraggable: showControls, // Use control visibility instead of mode
      isResizable: showControls, // Use control visibility instead of mode
      resizeHandles: showControls ? ['se' as const] : [], // Only show resize handles when controls are visible
      draggableHandle: '.react-grid-dragHandleExample',
      compactType: 'vertical' as const,
      useCSSTransforms: true,
      preventCollision: false,
      allowOverlap: false,
      measureBeforeMount: false,
      transformScale: 1,
      ...defaultGridConfig,
      rowHeight: computedRowHeight,
      containerPadding: wantsFullWidth
        ? ([0, defaultGridConfig.containerPadding[1]] as [number, number])
        : defaultGridConfig.containerPadding,
      margin: wantsFullWidth
        ? ([4, defaultGridConfig.margin[1]] as [number, number])
        : defaultGridConfig.margin,
      autoSize: false,
      style: { height: '100%' },
    };
  }, [displayedLayouts, mode, showControls, handleLayoutChange, handleDragStart, handleDragStop, handleResizeStart, handleResizeStop, computedRowHeight, currentBreakpoint]);

  // Memoize the rendered grid items with minimal stable dependencies
  const gridItems = useMemo(() => {
    
    return renderGridItems();
  }, [
    // Only include the most essential dependencies that should trigger re-render
    displayedLayouts,  // Changed from currentLayouts
    currentBreakpoint,
    moduleMap,
    selectedItem,
    mode,
    commitHighlightId
    // Removed volatile dependencies: lazyLoading, preloadPlugins, callbacks
    // These don't affect the core rendering logic and cause unnecessary recalculations
  ]);

  return (
    <div
      className={`layout-engine-container ${isDragging ? 'layout-engine-container--dragging' : ''} ${isResizing ? 'layout-engine-container--resizing' : ''} ${isDragOver ? 'layout-engine-container--drag-over' : ''}`}
      ref={containerRef}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      style={{ position: 'relative' }}
    >
      {isAwaitingCommit && (
        <Box
          sx={{
            position: 'absolute',
            top: 12,
            right: 12,
            zIndex: 1200,
            pointerEvents: 'none',
            display: 'flex'
          }}
        >
          <Chip
            label="Saving layout…"
            size="small"
            color="info"
            sx={{ fontWeight: 600, boxShadow: 2, opacity: 0.9 }}
          />
        </Box>
      )}
      {/* Centering wrapper to keep the grid balanced on wide screens */}
      {(() => {
        const activeLayout =
          displayedLayouts[currentBreakpoint as keyof ResponsiveLayouts] ||
          displayedLayouts.desktop || displayedLayouts.wide || [];
        const wantsFullWidth = !showControls && (
          (Array.isArray(activeLayout) && activeLayout.length === 1) ||
          (Array.isArray(activeLayout) && activeLayout.some((it: any) => it?.config?.viewportFill || it?.config?.fullWidth))
        );
        return (
          <div className="layout-engine-center">
            <div className={`layout-engine-inner ${wantsFullWidth ? 'layout-engine-inner--full' : ''}`}>
              <ResponsiveGridLayout {...gridProps}>
                {gridItems}
              </ResponsiveGridLayout>
            </div>
          </div>
        );
      })()}
    </div>
  );
}, (prevProps, nextProps) => {
  // Custom comparison function for React.memo
  
  
  // Compare primitive props
  if (
    prevProps.mode !== nextProps.mode ||
    prevProps.lazyLoading !== nextProps.lazyLoading ||
    prevProps.pageId !== nextProps.pageId
  ) {
    
    return false;
  }
  
  // Compare arrays by length and content
  if (prevProps.modules.length !== nextProps.modules.length) {
    
    return false;
  }
  
  if ((prevProps.preloadPlugins?.length || 0) !== (nextProps.preloadPlugins?.length || 0)) {
    
    return false;
  }
  
  // Compare layouts using JSON stringify for deep comparison
  const prevLayoutsStr = JSON.stringify(prevProps.layouts);
  const nextLayoutsStr = JSON.stringify(nextProps.layouts);
  
  if (prevLayoutsStr !== nextLayoutsStr) {
    
    return false;
  }
  
  // Compare modules by ID (assuming modules have stable IDs)
  for (let i = 0; i < prevProps.modules.length; i++) {
    if (prevProps.modules[i].id !== nextProps.modules[i].id) {
      
      return false;
    }
  }
  
  // Skip callback function comparison - they change frequently but don't affect rendering
  // This is the key optimization: ignore callback prop changes
  
  
  
  return true; // Props are equal, prevent re-render
});

export default LayoutEngine;
