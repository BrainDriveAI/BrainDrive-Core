import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Responsive, WidthProvider } from 'react-grid-layout';
import { RenderMode, ResponsiveLayouts, LayoutItem, ModuleConfig } from '../types';
import { LegacyModuleAdapter } from '../adapters/LegacyModuleAdapter';
import { useBreakpoint } from '../hooks/useBreakpoint';
import { GridItemControls } from '../../plugin-studio/components/canvas/GridItemControls';
import { useUnifiedLayoutState } from '../hooks/useUnifiedLayoutState';
import { LayoutChangeOrigin } from '../utils/layoutChangeManager';

const ResponsiveGridLayout = WidthProvider(Responsive);

export interface LayoutEngineBaseProps {
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
  canvasScale?: number;
  canvasWidth?: number;
  canvasHeight?: number;
}

const defaultGridConfig = {
  cols: { xxl: 12, xl: 12, lg: 12, sm: 8, xs: 4 },
  rowHeight: 60,
  margin: [10, 10] as [number, number],
  containerPadding: [10, 10] as [number, number],
  breakpoints: { xxl: 1600, xl: 1400, lg: 1024, sm: 768, xs: 0 },
};

export const LayoutEngineBase: React.FC<LayoutEngineBaseProps> = React.memo(({
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
  canvasScale,
  canvasWidth,
  canvasHeight,
}) => {
  // Debug: Track component re-renders
  const layoutEngineRenderCount = useRef(0);
  layoutEngineRenderCount.current++;
  
  if (process.env.NODE_ENV === 'development') {
    console.log(`[LayoutEngine] COMPONENT RENDER #${layoutEngineRenderCount.current}`, {
      layoutsKeys: Object.keys(layouts),
      modulesLength: modules.length,
      mode,
      lazyLoading,
      preloadPluginsLength: preloadPlugins.length,
    });
  }

  // Use unified layout state management with stable reference
  const unifiedLayoutState = useUnifiedLayoutState({
    initialLayouts: layouts,
    debounceMs: 200, // Increase debounce to prevent rapid updates
    onLayoutPersist: (persistedLayouts, origin) => {
      console.log(`[LayoutEngine] Persisting layout change from ${origin.source}`);
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
    wide: [],
    ultrawide: []
  });
  
  const currentLayouts = useMemo(() => {
    const newLayouts = unifiedLayoutState.layouts || {
      mobile: [],
      tablet: [],
      desktop: [],
      wide: [],
      ultrawide: []
    };
    
    // Only update if the actual content has changed (deep comparison)
    const currentHash = JSON.stringify(stableLayoutsRef.current);
    const newHash = JSON.stringify(newLayouts);
    
    if (currentHash !== newHash) {
      stableLayoutsRef.current = newLayouts;
    }
    
    return stableLayoutsRef.current;
  }, [unifiedLayoutState.layouts]);

  // Local UI state
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);

  // Bounce tracking to keep Studio zero-bounce without controller V2
  const previousPositionsRef = useRef<
    Map<string, { x: number; y: number; w: number; h: number; timestamp: number }>
  >(new Map());
  const intendedPositionsRef = useRef<
    Map<string, { x: number; y: number; w: number; h: number; timestamp: number }>
  >(new Map());
  const bounceWindowMs = 1000;

  const debugLog = useCallback((message: string, details?: Record<string, unknown>) => {
    if (process.env.NODE_ENV !== 'development') {
      return;
    }
    const payload = details ? JSON.stringify(details) : '';
    console.log(`[LayoutEngineBase] ${message}${payload ? ` :: ${payload}` : ''}`);
  }, []);

  const { currentBreakpoint } = useBreakpoint();
  
  // Track operation IDs for proper state management
  const currentOperationId = useRef<string | null>(null);
  const pageIdRef = useRef<string | undefined>(pageId);

  // Handle external layout changes (from props)
  useEffect(() => {
    // Reset layouts when page changes
    if (pageId !== pageIdRef.current) {
      console.log('[LayoutEngineBase] Page changed, resetting layouts');
      unifiedLayoutState.resetLayouts(layouts);
      pageIdRef.current = pageId;
      previousPositionsRef.current.clear();
      intendedPositionsRef.current.clear();
      return;
    }

    // Skip if layouts are semantically identical
    if (unifiedLayoutState.compareWithCurrent(layouts)) {
      return;
    }

    // Skip during active operations to prevent interference
    if (isDragging || isResizing) {
      console.log('[LayoutEngine] Skipping external sync during active operation');
      return;
    }

    // Update from external source
    console.log('[LayoutEngine] Syncing external layout change');
    unifiedLayoutState.updateLayouts(layouts, {
      source: 'external-sync',
      timestamp: Date.now()
    });
  }, [layouts, pageId, isDragging, isResizing, unifiedLayoutState]);

  // Handle layout change - convert from react-grid-layout format to our format
  const handleLayoutChange = useCallback((layout: any[] = [], allLayouts: any = {}) => {
    const operationId = currentOperationId.current;
    const items = Array.isArray(layout) ? layout : [];
    const now = Date.now();

    // Guard against bounce regressions by ignoring rapid post-operation flips
    if (!operationId && !isDragging && !isResizing && items.length > 0) {
      const bounceDetected = items.some(item => {
        const key = `${item.i}`;
        const intended = intendedPositionsRef.current.get(key);
        if (!intended) {
          return false;
        }
        const moved = intended.x !== item.x || intended.y !== item.y ||
                      intended.w !== item.w || intended.h !== item.h;
        const withinWindow = now - intended.timestamp < bounceWindowMs;
        if (moved && withinWindow) {
          debugLog('Bounce suppressed', {
            itemId: item.i,
            intended,
            next: { x: item.x, y: item.y, w: item.w, h: item.h },
            deltaMs: now - intended.timestamp
          });
          return true;
        }
        return false;
      });

      if (bounceDetected) {
        return;
      }
    }

    const recordPositions = () => {
      items.forEach(item => {
        const key = `${item.i}`;
        const prev = previousPositionsRef.current.get(key);
        const current = { x: item.x, y: item.y, w: item.w, h: item.h, timestamp: now };

        if (prev) {
          const deltaX = current.x - prev.x;
          const deltaY = current.y - prev.y;
          const deltaW = current.w - prev.w;
          const deltaH = current.h - prev.h;
          if (deltaX || deltaY || deltaW || deltaH) {
            debugLog('Position changed', {
              itemId: item.i,
              deltaX,
              deltaY,
              deltaW,
              deltaH
            });
          }
        }

        previousPositionsRef.current.set(key, current);
      });
    };

    recordPositions();

    const normalizeItems = (itemsToNormalize: any[] = []): LayoutItem[] => {
      return (itemsToNormalize || []).map((it: any) => {
        const draft: LayoutItem = {
          i: it?.i ?? '',
          x: it?.x ?? 0,
          y: it?.y ?? 0,
          w: it?.w ?? 2,
          h: it?.h ?? 2,
          moduleId: it?.moduleId || it?.i || '',
          pluginId: it?.pluginId || 'unknown',
          minW: it?.minW,
          minH: it?.minH,
          isDraggable: it?.isDraggable ?? true,
          isResizable: it?.isResizable ?? true,
          static: it?.static ?? false,
          config: it?.config
        };
        return draft;
      });
    };

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
      xxl: 'ultrawide',
      mobile: 'mobile',
      tablet: 'tablet',
      desktop: 'desktop',
      wide: 'wide',
      ultrawide: 'ultrawide'
    };

    Object.entries(allLayouts).forEach(([gridBreakpoint, gridLayout]: [string, any]) => {
      const ourBreakpoint = breakpointMap[gridBreakpoint];
      if (ourBreakpoint && Array.isArray(gridLayout)) {
        convertedLayouts[ourBreakpoint] = normalizeItems(gridLayout as any[]);
      }
    });

    if (items.length > 0 && currentBreakpoint) {
      const ourBreakpoint = breakpointMap[currentBreakpoint];
      if (ourBreakpoint) {
        convertedLayouts[ourBreakpoint] = normalizeItems(items);
      }
    }

    const origin: LayoutChangeOrigin = {
      source: isDragging ? 'user-drag' : isResizing ? 'user-resize' : 'external-sync',
      timestamp: now,
      operationId: operationId || undefined
    };

    unifiedLayoutState.updateLayouts(convertedLayouts, origin);

    if (operationId && (origin.source === 'user-drag' || origin.source === 'user-resize') && items.length > 0) {
      items.forEach(item => {
        intendedPositionsRef.current.set(`${item.i}`, {
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
          timestamp: now
        });
      });
    }
  }, [
    bounceWindowMs,
    currentBreakpoint,
    debugLog,
    intendedPositionsRef,
    isDragging,
    isResizing,
    previousPositionsRef,
    unifiedLayoutState
  ]);

  // Handle drag start
  const handleDragStart = useCallback(() => {
    const operationId = `drag-${Date.now()}`;
    currentOperationId.current = operationId;
    setIsDragging(true);
    unifiedLayoutState.startOperation(operationId);
    console.log('[LayoutEngine] Started drag operation:', operationId);
  }, [unifiedLayoutState]);

  // Handle drag stop
  const handleDragStop = useCallback((layout: any[], ..._args: any[]) => {
    if (currentOperationId.current) {
      unifiedLayoutState.stopOperation(currentOperationId.current);
      console.log('[LayoutEngine] Stopped drag operation:', currentOperationId.current);
      currentOperationId.current = null;
    }
    setIsDragging(false);

    if (Array.isArray(layout)) {
      const now = Date.now();
      layout.forEach(item => {
        intendedPositionsRef.current.set(`${item.i}`, {
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
          timestamp: now
        });
      });
    }
  }, [unifiedLayoutState]);

  // Handle resize start
  const handleResizeStart = useCallback(() => {
    const operationId = `resize-${Date.now()}`;
    currentOperationId.current = operationId;
    setIsResizing(true);
    unifiedLayoutState.startOperation(operationId);
    console.log('[LayoutEngine] Started resize operation:', operationId);
  }, [unifiedLayoutState]);

  // Handle resize stop
  const handleResizeStop = useCallback((layout: any[], ..._args: any[]) => {
    if (currentOperationId.current) {
      unifiedLayoutState.stopOperation(currentOperationId.current);
      console.log('[LayoutEngine] Stopped resize operation:', currentOperationId.current);
      currentOperationId.current = null;
    }
    setIsResizing(false);

    if (Array.isArray(layout)) {
      const now = Date.now();
      layout.forEach(item => {
        intendedPositionsRef.current.set(`${item.i}`, {
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
          timestamp: now
        });
      });
    }
  }, [unifiedLayoutState]);

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
  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
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
      console.log('Parsed module data:', moduleData);
      
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
          ...moduleData.config
        },
        isDraggable: true,
        isResizable: true,
        static: false
      };
      
      console.log('Adding new item to layout:', newItem);
      
      // Add the item to the current layouts
      const updatedLayouts = { ...currentLayouts };
      Object.keys(updatedLayouts).forEach(breakpoint => {
        const currentLayout = updatedLayouts[breakpoint as keyof ResponsiveLayouts];
        if (currentLayout) {
          updatedLayouts[breakpoint as keyof ResponsiveLayouts] = [
            ...currentLayout,
            newItem
          ];
        }
      });
      
      // Update through unified state management
      unifiedLayoutState.updateLayouts(updatedLayouts, {
        source: 'drop-add',
        timestamp: Date.now()
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
    console.log(`[LayoutEngine] Removing item: ${itemId}`);
    
    // Create new layouts with the item removed from all breakpoints
    const updatedLayouts: ResponsiveLayouts = {
      mobile: currentLayouts.mobile?.filter((item: LayoutItem) => item.i !== itemId) || [],
      tablet: currentLayouts.tablet?.filter((item: LayoutItem) => item.i !== itemId) || [],
      desktop: currentLayouts.desktop?.filter((item: LayoutItem) => item.i !== itemId) || [],
      wide: currentLayouts.wide?.filter((item: LayoutItem) => item.i !== itemId) || [],
      ultrawide: currentLayouts.ultrawide?.filter((item: LayoutItem) => item.i !== itemId) || []
    };

    // Update through unified state management
    unifiedLayoutState.updateLayouts(updatedLayouts, {
      source: 'user-remove',
      timestamp: Date.now(),
      operationId: `remove-${itemId}-${Date.now()}`
    });

    // Clear selection if the removed item was selected
    if (selectedItem === itemId) {
      setSelectedItem(null);
    }

    // Call the external callback if provided
    onItemRemove?.(itemId);
  }, [currentLayouts, unifiedLayoutState, selectedItem, onItemRemove]);

  // Create ultra-stable module map with deep comparison
  const stableModuleMapRef = useRef<Record<string, ModuleConfig>>({});
  
  const moduleMap = useMemo(() => {
    const newModuleMap = modules.reduce((map, module) => {
      map[module.id] = module;
      return map;
    }, {} as Record<string, ModuleConfig>);
    
    // Only update if the actual content has changed (deep comparison)
    const currentHash = JSON.stringify(stableModuleMapRef.current);
    const newHash = JSON.stringify(newModuleMap);
    
    if (currentHash !== newHash) {
      stableModuleMapRef.current = newModuleMap;
    }
    
    return stableModuleMapRef.current;
  }, [modules]);

  // Render grid items
  const renderGridItems = useCallback(() => {
    const currentLayout = currentLayouts[currentBreakpoint as keyof ResponsiveLayouts] || currentLayouts.desktop || [];
    
    if (process.env.NODE_ENV === 'development') {
      console.log(`[LayoutEngine] RENDER TRIGGERED - Rendering ${currentLayout.length} items for breakpoint: ${currentBreakpoint}`, {
        availableModules: Object.keys(moduleMap),
        availableModuleDetails: Object.entries(moduleMap).map(([id, mod]) => ({ id, pluginId: mod.pluginId })),
        layoutItems: currentLayout.map((item: LayoutItem) => ({ i: item.i, moduleId: item.moduleId, pluginId: item.pluginId })),
        currentLayouts: Object.keys(currentLayouts),
        currentBreakpoint,
        stackTrace: new Error().stack?.split('\n').slice(0, 5).join('\n')
      });
    }
    
    return currentLayout.map((item: LayoutItem) => {
      // Try to find the module by moduleId with multiple strategies
      let module = moduleMap[item.moduleId];
      
      // If direct lookup fails, try alternative matching strategies
      if (!module) {
        // Strategy 1: Try without underscores (sanitized version)
        const sanitizedModuleId = item.moduleId.replace(/_/g, '');
        module = moduleMap[sanitizedModuleId];
        
        if (module) {
          if (process.env.NODE_ENV === 'development') {
            console.log(`[LayoutEngine] Found module using sanitized ID: ${sanitizedModuleId} for original: ${item.moduleId}`);
          }
        } else {
          // Strategy 2: Try finding by pluginId match
          for (const [moduleId, moduleConfig] of Object.entries(moduleMap)) {
            if (moduleConfig.pluginId === item.pluginId) {
              module = moduleConfig;
              if (process.env.NODE_ENV === 'development') {
                console.log(`[LayoutEngine] Found module by pluginId match: ${moduleId} for ${item.moduleId}`);
              }
              break;
            }
          }
        }
      }
      
      if (!module) {
        if (process.env.NODE_ENV === 'development') {
          console.warn(`[LayoutEngine] Module not found for moduleId: ${item.moduleId}`, {
            availableModules: Object.keys(moduleMap),
            availableModuleDetails: Object.entries(moduleMap).map(([id, mod]) => ({ id, pluginId: mod.pluginId })),
            layoutItem: item,
            searchedModuleId: item.moduleId,
            itemPluginId: item.pluginId
          });
        }
        
        // Instead of returning null, try to render with the layout item data directly
        // This allows the LegacyModuleAdapter to handle the module loading
        const isSelected = selectedItem === item.i;
        const isStudioMode = mode === RenderMode.STUDIO;

        // Try to extract pluginId from moduleId if item.pluginId is 'unknown'
        let fallbackPluginId = item.pluginId;
        if (!fallbackPluginId || fallbackPluginId === 'unknown') {
          // Try to extract plugin ID from the module ID pattern
          // e.g., "BrainDriveChat_1830586da8834501bea1ef1d39c3cbe8_BrainDriveChat_BrainDriveChat_1754404718788"
          const moduleIdParts = item.moduleId.split('_');
          if (moduleIdParts.length > 0) {
            const potentialPluginId = moduleIdParts[0];
            // Check if this matches any available plugin
            const availablePluginIds = ['BrainDriveBasicAIChat', 'BrainDriveChat', 'BrainDriveSettings'];
            if (availablePluginIds.includes(potentialPluginId)) {
              fallbackPluginId = potentialPluginId;
              if (process.env.NODE_ENV === 'development') {
                console.log(`[LayoutEngine] Extracted pluginId '${fallbackPluginId}' from moduleId '${item.moduleId}'`);
              }
            }
          }
        }

        // Extract simple module ID from complex ID - calculate directly to avoid useMemo in render loop
        // Pattern: BrainDriveBasicAIChat_59898811a4b34d9097615ed6698d25f6_1754507768265
        // We want: 59898811a4b34d9097615ed6698d25f6
        const parts = item.moduleId.split('_');
        const extractedModuleId = parts.length >= 2 ? parts[1] : item.moduleId;

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

        return (
          <div
            key={item.i}
            className={`layout-item react-grid-item ${isSelected ? 'layout-item--selected selected' : ''} ${isStudioMode ? 'layout-item--studio' : ''}`}
            onClick={() => handleItemClick(item.i)}
            data-grid={item}
            style={{ position: 'relative' }}
          >
            {/* Use the legacy GridItemControls component for consistent behavior */}
            {isStudioMode && (
              <GridItemControls
                isSelected={isSelected}
                onConfig={() => onItemConfig?.(item.i)}
                onRemove={() => handleItemRemove(item.i)}
              />
            )}
            <LegacyModuleAdapter
              pluginId={fallbackPluginId}
              moduleId={extractedModuleId}
              moduleName={undefined}
              moduleProps={item.config || {}}
              useUnifiedRenderer={true}
              mode={mode === RenderMode.STUDIO ? 'studio' : 'published'}
              breakpoint={breakpointConfig}
              lazyLoading={lazyLoading}
              priority={preloadPlugins.includes(item.pluginId) ? 'high' : 'normal'}
              enableMigrationWarnings={false}
              fallbackStrategy="on-error"
              performanceMonitoring={process.env.NODE_ENV === 'development'}
            />
          </div>
        );
      }

      const isSelected = selectedItem === item.i;
      const isStudioMode = mode === RenderMode.STUDIO;

      return (
        <div
          key={item.i}
          className={`layout-item react-grid-item ${isSelected ? 'layout-item--selected selected' : ''} ${isStudioMode ? 'layout-item--studio' : ''}`}
          onClick={() => handleItemClick(item.i)}
          data-grid={item}
          style={{ position: 'relative' }}
        >
          {/* Use the legacy GridItemControls component for consistent behavior */}
          {isStudioMode && (
            <GridItemControls
              isSelected={isSelected}
              onConfig={() => onItemConfig?.(item.i)}
              onRemove={() => handleItemRemove(item.i)}
            />
          )}
          
          <LegacyModuleAdapter
            pluginId={item.pluginId}
            moduleId={module._legacy?.moduleId || (() => {
              // Extract simple module ID from complex ID
              // Pattern: BrainDriveBasicAIChat_59898811a4b34d9097615ed6698d25f6_1754507768265
              // We want: 59898811a4b34d9097615ed6698d25f6
              const parts = item.moduleId.split('_');
              if (parts.length >= 2) {
                // The module ID is typically the second part (after plugin name)
                return parts[1];
              }
              return item.moduleId; // fallback to original if pattern doesn't match
            })()}
            moduleName={module._legacy?.moduleName}
            moduleProps={module._legacy?.originalConfig || item.config}
            useUnifiedRenderer={true}
            mode={mode === RenderMode.STUDIO ? 'studio' : 'published'}
            breakpoint={{
              name: currentBreakpoint,
              width: 0,
              height: 0,
              orientation: 'landscape',
              pixelRatio: 1,
              containerWidth: 1200,
              containerHeight: 800,
            }}
            lazyLoading={lazyLoading}
            priority={preloadPlugins.includes(item.pluginId) ? 'high' : 'normal'}
            enableMigrationWarnings={false}
            fallbackStrategy="on-error"
            performanceMonitoring={process.env.NODE_ENV === 'development'}
          />
        </div>
      );
    });
  }, [
    unifiedLayoutState.layouts,
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

  // Grid layout props - convert ResponsiveLayouts to react-grid-layout Layouts format
  const effectiveCanvasScale = mode === RenderMode.STUDIO ? (canvasScale ?? 1) : 1;
  const logicalWidth = mode === RenderMode.STUDIO ? canvasWidth : undefined;
  const logicalHeight = mode === RenderMode.STUDIO ? canvasHeight : undefined;

  const gridProps = useMemo(() => {
    // Convert ResponsiveLayouts to the format expected by react-grid-layout
    const reactGridLayouts: any = {};
    Object.entries(currentLayouts).forEach(([breakpoint, layout]) => {
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
        reactGridLayouts[gridBreakpoint] = layout;
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
      isDraggable: mode === RenderMode.STUDIO,
      isResizable: mode === RenderMode.STUDIO,
      draggableHandle: '.react-grid-dragHandleExample',
      compactType: 'vertical' as const,
      useCSSTransforms: true,
      preventCollision: false,
      allowOverlap: false,
      measureBeforeMount: false,
      transformScale: effectiveCanvasScale,
      ...defaultGridConfig,
    };
  }, [currentLayouts, mode, handleLayoutChange, handleDragStart, handleDragStop, handleResizeStart, handleResizeStop, effectiveCanvasScale]);

  // Memoize the rendered grid items with minimal stable dependencies
  const gridItems = useMemo(() => {
    if (process.env.NODE_ENV === 'development') {
      console.log(`[LayoutEngine] MEMO RECALCULATION - gridItems being recalculated`, {
        currentLayoutsKeys: Object.keys(currentLayouts),
        currentBreakpoint,
        moduleMapSize: Object.keys(moduleMap).length,
        selectedItem,
        mode,
        lazyLoading,
        preloadPluginsLength: preloadPlugins.length,
        stackTrace: new Error().stack?.split('\n').slice(0, 3).join('\n')
      });
    }
    return renderGridItems();
  }, [
    // Only include the most essential dependencies that should trigger re-render
    currentLayouts,
    currentBreakpoint,
    moduleMap,
    selectedItem,
    mode
    // Removed volatile dependencies: lazyLoading, preloadPlugins, callbacks
    // These don't affect the core rendering logic and cause unnecessary recalculations
  ]);

  const wrapperStyle = useMemo<React.CSSProperties>(() => {
    const style: React.CSSProperties = {};
    if (logicalWidth) {
      style.width = logicalWidth;
      style.minWidth = logicalWidth;
    }
    if (logicalHeight) {
      style.minHeight = logicalHeight;
    }
    if (effectiveCanvasScale !== 1) {
      style.transform = `scale(${effectiveCanvasScale})`;
      style.transformOrigin = 'top left';
    }
    return style;
  }, [logicalWidth, logicalHeight, effectiveCanvasScale]);

  useEffect(() => {
    if (mode === RenderMode.STUDIO && effectiveCanvasScale !== 1) {
      window.dispatchEvent(new Event('resize'));
    }
  }, [mode, effectiveCanvasScale, logicalWidth, logicalHeight]);

  return (
    <div
      className={`layout-engine-container ${isDragging ? 'layout-engine-container--dragging' : ''} ${isResizing ? 'layout-engine-container--resizing' : ''} ${isDragOver ? 'layout-engine-container--drag-over' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      style={{ overflow: 'auto' }}
    >
      <div style={wrapperStyle}>
        <ResponsiveGridLayout {...gridProps}>
          {gridItems}
        </ResponsiveGridLayout>
      </div>
    </div>
  );
}, (prevProps, nextProps) => {
  // Custom comparison function for React.memo
  if (process.env.NODE_ENV === 'development') {
    console.log('[LayoutEngine] MEMO COMPARISON - Checking if props are equal');
  }
  
  // Compare primitive props
  if (
    prevProps.mode !== nextProps.mode ||
    prevProps.lazyLoading !== nextProps.lazyLoading ||
    prevProps.pageId !== nextProps.pageId ||
    prevProps.canvasScale !== nextProps.canvasScale ||
    prevProps.canvasWidth !== nextProps.canvasWidth ||
    prevProps.canvasHeight !== nextProps.canvasHeight
  ) {
    if (process.env.NODE_ENV === 'development') {
      console.log('[LayoutEngine] MEMO COMPARISON - Primitive props changed, re-rendering');
    }
    return false;
  }
  
  // Compare arrays by length and content
  if (prevProps.modules.length !== nextProps.modules.length) {
    if (process.env.NODE_ENV === 'development') {
      console.log('[LayoutEngine] MEMO COMPARISON - Modules length changed, re-rendering');
    }
    return false;
  }
  
  if ((prevProps.preloadPlugins?.length || 0) !== (nextProps.preloadPlugins?.length || 0)) {
    if (process.env.NODE_ENV === 'development') {
      console.log('[LayoutEngine] MEMO COMPARISON - PreloadPlugins length changed, re-rendering');
    }
    return false;
  }
  
  // Compare layouts using JSON stringify for deep comparison
  const prevLayoutsStr = JSON.stringify(prevProps.layouts);
  const nextLayoutsStr = JSON.stringify(nextProps.layouts);
  
  if (prevLayoutsStr !== nextLayoutsStr) {
    if (process.env.NODE_ENV === 'development') {
      console.log('[LayoutEngine] MEMO COMPARISON - Layouts changed, re-rendering');
    }
    return false;
  }
  
  // Compare modules by ID (assuming modules have stable IDs)
  for (let i = 0; i < prevProps.modules.length; i++) {
    if (prevProps.modules[i].id !== nextProps.modules[i].id) {
      if (process.env.NODE_ENV === 'development') {
        console.log('[LayoutEngine] MEMO COMPARISON - Module IDs changed, re-rendering');
      }
      return false;
    }
  }
  
  // Skip callback function comparison - they change frequently but don't affect rendering
  // This is the key optimization: ignore callback prop changes
  
  if (process.env.NODE_ENV === 'development') {
    console.log('[LayoutEngine] MEMO COMPARISON - Props are equal, preventing re-render');
  }
  
  return true; // Props are equal, prevent re-render
});
