import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Box, useTheme } from '@mui/material';
import { LayoutCommitBadge } from '../components/LayoutCommitBadge';
import { UnifiedPageRenderer } from '../components/UnifiedPageRenderer';
import { ResponsiveContainer } from '../components/ResponsiveContainer';
import { PageProvider } from '../contexts/PageContext';
import { RenderMode, PageData, ResponsiveLayouts, LayoutItem, ModuleConfig } from '../types';
import { usePluginStudioDevMode } from '../../../hooks/usePluginStudioDevMode';
import { generateLayoutHash } from '../utils/layoutChangeManager';

// Import Plugin Studio types and components
import {
  Page as PluginStudioPage,
  Layouts as PluginStudioLayouts,
  GridItem as PluginStudioGridItem,
  ModuleDefinition as PluginStudioModuleDefinition
} from '../../plugin-studio/types';
import { GridToolbar } from '../../plugin-studio/components/grid-toolbar/GridToolbar';

/**
 * Plugin Studio Adapter Props
 */
export interface PluginStudioAdapterProps {
  // Plugin Studio data
  page: PluginStudioPage | null;
  layouts: PluginStudioLayouts | null;
  
  // Studio functionality
  onLayoutChange?: (layout: any[], newLayouts: PluginStudioLayouts) => void;
  onLayoutChangeFlush?: () => Promise<void>; // Quick Mitigation: Add flush callback
  onPageLoad?: (page: PageData) => void;
  onError?: (error: Error) => void;
  onSave?: (pageId: string, options?: { layoutOverride?: PluginStudioLayouts }) => Promise<void>; // Quick Mitigation: Add options parameter
  
  // UI state
  previewMode?: boolean;
  selectedItem?: { i: string } | null;
  onItemSelect?: (itemId: string | null) => void;
  
  // Config dialog functionality
  onItemConfig?: (itemId: string) => void;
  setConfigDialogOpen?: (open: boolean) => void;
  setSelectedItem?: (item: { i: string } | null) => void;
  
  // Migration options
  enableUnifiedFeatures?: boolean;
  fallbackToLegacy?: boolean;
  performanceMonitoring?: boolean;

  // Studio canvas state
  zoom?: number;
  canvas?: {
    width?: number;
    height?: number;
  };
}

/**
 * Plugin Studio Adapter
 * 
 * This adapter bridges the Plugin Studio system with the Unified Dynamic Page Renderer.
 * It handles data conversion, maintains backward compatibility, and enables enhanced features.
 */
export const PluginStudioAdapter: React.FC<PluginStudioAdapterProps> = ({
  page,
  layouts,
  onLayoutChange,
  onLayoutChangeFlush,
  onPageLoad,
  onError,
  onSave,
  previewMode = false,
  selectedItem,
  onItemSelect,
  onItemConfig,
  setConfigDialogOpen,
  setSelectedItem,
  enableUnifiedFeatures = true,
  fallbackToLegacy = false,
  performanceMonitoring = import.meta.env.MODE === 'development',
  zoom = 1,
  canvas,
}) => {
  // Get Material-UI theme for dark mode support
  const theme = useTheme();
  // Get dev mode features - MUST be called before any conditional returns
  const { features: devModeFeatures } = usePluginStudioDevMode();
  const DEFAULT_CANVAS_WIDTH = 1440;
  const DEFAULT_CANVAS_HEIGHT = 2400;
  const canvasWidth = canvas?.width ?? DEFAULT_CANVAS_WIDTH;
  const canvasHeight = canvas?.height ?? DEFAULT_CANVAS_HEIGHT;
  
  // Phase 1: Add debug mode flag
  const isDebugMode = import.meta.env.VITE_LAYOUT_DEBUG === 'true';
  // State for converted data
  const [convertedPageData, setConvertedPageData] = useState<PageData | null>(null);
  const [conversionError, setConversionError] = useState<Error | null>(null);
  const [isConverting, setIsConverting] = useState(false);

  // Safeguard to prevent infinite loops during module updates
  const isUpdatingModulesRef = useRef(false);
  
  // Quick Mitigation: Add ref to store unified layout state
  const unifiedLayoutStateRef = useRef<any>(null);

  // Performance tracking
  const [performanceMetrics, setPerformanceMetrics] = useState<{
    conversionTime?: number;
    renderTime?: number;
    lastUpdate: number;
  }>({ lastUpdate: Date.now() });


  // Handle module selection from unified renderer
  const handleUnifiedModuleSelect = useCallback((moduleId: string | null) => {
    onItemSelect?.(moduleId);
  }, [onItemSelect]);

  // Handle clicking outside to deselect
  const handleContainerClick = useCallback((event: React.MouseEvent) => {
    if (event.target === event.currentTarget) {
      onItemSelect?.(null);
    }
  }, [onItemSelect]);

  // Handle module configuration
  const handleModuleConfig = useCallback((moduleId: string) => {
    console.log('Configure module:', moduleId);
    
    // Set the selected item to the module being configured
    if (setSelectedItem) {
      setSelectedItem({ i: moduleId });
    }
    
    // Open the config dialog
    if (setConfigDialogOpen) {
      setConfigDialogOpen(true);
    }
    
    // Also call the external callback if provided
    onItemConfig?.(moduleId);
  }, [setSelectedItem, setConfigDialogOpen, onItemConfig]);

  // Handle module deletion
  const handleModuleDelete = useCallback((moduleId: string) => {
    console.log('Delete module:', moduleId);
    // TODO: Implement delete confirmation and removal
  }, []);

  /**
   * Convert Plugin Studio GridItem to Unified LayoutItem
   */
  const convertGridItemToLayoutItem = useCallback((
    item: PluginStudioGridItem | any,
    moduleDefinitions?: Record<string, PluginStudioModuleDefinition>
  ): LayoutItem => {
    // Extract module definition if available
    const moduleDefinition = moduleDefinitions?.[item.i];
    // Prefer pluginId from module definition or original item
    const originalPluginId = item?.args?._originalItem?.pluginId;
    const extractComposite = (s?: string): string => {
      if (!s) return '';
      const parts = String(s).split('_');
      if (parts.length >= 2) return `${parts[0]}_${parts[1]}`;
      return parts[0] || '';
    };
    let pluginId: string = moduleDefinition?.pluginId
      || originalPluginId
      || (item.pluginId && item.pluginId !== 'unknown' ? item.pluginId : '')
      || extractComposite(item.i)
      || extractComposite(item.moduleId)
      || 'unknown';
    
    return {
      i: item.i,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      minW: item.minW,
      minH: item.minH,
      moduleId: item.i, // keep PS identity for module map lookups
      pluginId: pluginId,
      config: {
        ...item.args,
        ...moduleDefinition?.config,
        // Preserve Plugin Studio specific properties
        _pluginStudioItem: true,
        _originalItem: item
      } as ModuleConfig,
      isDraggable: !previewMode,
      isResizable: !previewMode,
      static: previewMode
    };
  }, [previewMode]);

  /**
   * Convert Plugin Studio Layouts to Unified ResponsiveLayouts
   */
  const convertLayouts = useCallback((
    pluginStudioLayouts: PluginStudioLayouts,
    moduleDefinitions?: Record<string, PluginStudioModuleDefinition>
  ): ResponsiveLayouts => {
    const convertLayoutArray = (items: (PluginStudioGridItem | any)[] = []): LayoutItem[] => {
      return items
        .filter(item => item && item.i && typeof item.x === 'number' && typeof item.y === 'number')
        .map(item => convertGridItemToLayoutItem(item, moduleDefinitions));
    };

    return {
      mobile: convertLayoutArray(pluginStudioLayouts.mobile),
      tablet: convertLayoutArray(pluginStudioLayouts.tablet),
      desktop: convertLayoutArray(pluginStudioLayouts.desktop),
      wide: convertLayoutArray(pluginStudioLayouts.desktop), // Fallback to desktop
      ultrawide: convertLayoutArray(pluginStudioLayouts.desktop) // Fallback to desktop
    };
  }, [convertGridItemToLayoutItem]);

  /**
   * Convert Plugin Studio modules to Unified ModuleConfig array
   */
  const convertModules = useCallback((
    moduleDefinitions: Record<string, PluginStudioModuleDefinition> = {}
  ): ModuleConfig[] => {
    return Object.entries(moduleDefinitions).map(([key, module]) => ({
      id: key,
      pluginId: module.pluginId,
      type: 'component',
      ...module.config,
      // Preserve original module definition
      _pluginStudioModule: true,
      _originalModule: module,
      // Performance hints
      lazy: true,
      priority: 'normal' as const
    }));
  }, []);

  /**
   * Convert Plugin Studio Page to Unified PageData
   */
  const convertPageData = useCallback((
    pluginStudioPage: PluginStudioPage,
    pluginStudioLayouts: PluginStudioLayouts
  ): PageData => {
    const startTime = performance.now();

    try {
      const convertedLayouts = convertLayouts(pluginStudioLayouts, pluginStudioPage.modules);
      const convertedModules = convertModules(pluginStudioPage.modules);

      const pageData: PageData = {
        id: pluginStudioPage.id,
        name: pluginStudioPage.name,
        route: pluginStudioPage.route || `/plugin-studio/${pluginStudioPage.id}`,
        layouts: convertedLayouts,
        modules: convertedModules,
        metadata: {
          title: pluginStudioPage.name,
          description: pluginStudioPage.description,
          lastModified: new Date()
        } as any, // Allow additional properties for Plugin Studio compatibility
        isPublished: pluginStudioPage.is_published || false
      };

      // Track performance
      if (performanceMonitoring) {
        const conversionTime = performance.now() - startTime;
        setPerformanceMetrics(prev => ({
          ...prev,
          conversionTime,
          lastUpdate: Date.now()
        }));
        
      }

      return pageData;
    } catch (error) {
      console.error('[PluginStudioAdapter] Failed to convert page data:', error);
      throw error;
    }
  }, [convertLayouts, convertModules, performanceMonitoring]);

  /**
   * Convert unified layout changes back to Plugin Studio format
   * Following the same pattern as the working legacy GridContainer
   */
  const handleUnifiedLayoutChange = useCallback((
    unifiedLayouts: ResponsiveLayouts,
    metadata?: { version?: number; hash?: string; origin?: any }
  ) => {
    if (!onLayoutChange) return;

    try {
      // Phase 1: Log conversion event
      const hash = metadata?.hash || generateLayoutHash(unifiedLayouts);
      const version = metadata?.version || 0;
      
      if (isDebugMode) {
        console.log(`[PluginStudioAdapter] Convert v${version} hash:${hash}`, {
          origin: metadata?.origin,
          timestamp: Date.now()
        });
      }
      // Phase 5: Always update converted page data with new layouts to ensure saves use latest state
      // The UnifiedLayoutState already handles deduplication, so we should trust its updates
      setConvertedPageData(prev => {
        if (!prev) return prev;
        
        // Always update layouts when we receive a change event
        // This ensures convertedPageData stays in sync with the actual layout state
        if (isDebugMode) {
          console.log('[PluginStudioAdapter] Updating convertedPageData with new layouts');
        }
        
        return {
          ...prev,
          layouts: unifiedLayouts
        };
      });

      // Convert unified layouts back to Plugin Studio format - optimized
      const convertUnifiedToPluginStudio = (items: LayoutItem[] = [], originalItems: (PluginStudioGridItem | any)[] = []): (PluginStudioGridItem | any)[] => {
        return items.map(item => {
          // Find the original item to preserve its properties
          const originalItem = originalItems.find(orig => orig.i === item.i);
          
          if (originalItem) {
            // Check if anything actually changed
            const hasChanges =
              originalItem.x !== item.x ||
              originalItem.y !== item.y ||
              originalItem.w !== item.w ||
              originalItem.h !== item.h ||
              originalItem.minW !== item.minW ||
              originalItem.minH !== item.minH;
            
            if (!hasChanges) {
              return originalItem; // No changes, return original reference
            }
            
            // Preserve all properties but update position and size
            return {
              ...originalItem,
              x: item.x,
              y: item.y,
              w: item.w,
              h: item.h,
              minW: item.minW,
              minH: item.minH
            };
          } else {
            // Fallback - create from unified item
            return {
              i: item.i,
              x: item.x,
              y: item.y,
              w: item.w,
              h: item.h,
              minW: item.minW,
              minH: item.minH,
              pluginId: item.pluginId,
              args: item.config ? {
                ...item.config,
                // Remove internal properties
                _pluginStudioItem: undefined,
                _originalItem: undefined
              } : undefined
            };
          }
        });
      };

      // Convert using original layouts to preserve properties
      // Plugin Studio has no 'wide' breakpoint; map unified 'wide' into 'desktop' when needed
      const desktopUnified = (unifiedLayouts.desktop && unifiedLayouts.desktop.length > 0)
        ? unifiedLayouts.desktop
        : (unifiedLayouts.wide || []);

      const pluginStudioLayouts: PluginStudioLayouts = {
        desktop: convertUnifiedToPluginStudio(desktopUnified, layouts?.desktop),
        tablet: convertUnifiedToPluginStudio(unifiedLayouts.tablet, layouts?.tablet),
        mobile: convertUnifiedToPluginStudio(unifiedLayouts.mobile, layouts?.mobile)
      };

      // Call onLayoutChange immediately to persist changes
      // Pass the layout for the active PS breakpoint (desktop), using 'wide' as fallback
      onLayoutChange(desktopUnified, pluginStudioLayouts);
      
      // Phase 1: Log successful conversion
      if (isDebugMode) {
        // Generate hash using JSON stringify for Plugin Studio layouts
        const layoutStr = JSON.stringify(pluginStudioLayouts);
        let hash = 0;
        for (let i = 0; i < layoutStr.length; i++) {
          const char = layoutStr.charCodeAt(i);
          hash = ((hash << 5) - hash) + char;
          hash = hash & hash;
        }
        const convertedHash = Math.abs(hash).toString(16).padStart(8, '0');
        console.log(`[PluginStudioAdapter] Conversion complete hash:${convertedHash}`);
      }
    } catch (error) {
      console.error('[PluginStudioAdapter] Failed to convert layout changes:', error);
      onError?.(error as Error);
    }
  }, [onLayoutChange, layouts, onError, isDebugMode]);

  /**
   * Handle unified page load events
   */
  const handleUnifiedPageLoad = useCallback((pageData: PageData) => {
    if (performanceMonitoring) {
      setPerformanceMetrics(prev => ({
        ...prev,
        renderTime: Date.now() - prev.lastUpdate
      }));
    }
    
    onPageLoad?.(pageData);
  }, [onPageLoad, performanceMonitoring]);

  /**
   * Convert page data when inputs change
   * Fixed: Only convert on initial load, not on layout changes
   */
  useEffect(() => {
    if (!page || !layouts) {
      setConvertedPageData(null);
      return;
    }

    setIsConverting(true);
    setConversionError(null);

    try {
      const converted = convertPageData(page, layouts);
      setConvertedPageData(converted);
    } catch (error) {
      console.error('[PluginStudioAdapter] Conversion failed:', error);
      setConversionError(error as Error);
      onError?.(error as Error);
    } finally {
      setIsConverting(false);
    }
  }, [page?.id, page?.name, convertPageData, onError]); // Keep layouts out to prevent circular dependency

  /**
   * Separate effect to handle initial layouts conversion
   */
  useEffect(() => {
    if (!page || !layouts || convertedPageData) return;
    
    // Only run this effect once when we first get layouts
    setIsConverting(true);
    try {
      const converted = convertPageData(page, layouts);
      setConvertedPageData(converted);
    } catch (error) {
      console.error('[PluginStudioAdapter] Initial conversion failed:', error);
      setConversionError(error as Error);
      onError?.(error as Error);
    } finally {
      setIsConverting(false);
    }
  }, [layouts]); // Only depend on layouts for initial conversion

  /**
   * Watch for changes in page modules and update converted data
   * Fixed: Removed convertedPageData from dependency array to prevent infinite loop
   * Added safeguard to prevent recursive updates
   */
  useEffect(() => {
    if (!page || !convertedPageData || isUpdatingModulesRef.current) return;
    
    // Check if modules have changed
    const currentModulesHash = JSON.stringify(page.modules);
    const convertedModulesHash = JSON.stringify(convertedPageData.modules.reduce((acc, mod) => {
      acc[mod.id] = mod._originalModule;
      return acc;
    }, {} as Record<string, any>));
    
    if (currentModulesHash !== convertedModulesHash) {
      console.log('[PluginStudioAdapter] Modules changed, updating converted data');
      isUpdatingModulesRef.current = true;
      try {
        const updated = convertPageData(page, layouts || { desktop: [], tablet: [], mobile: [] });
        setConvertedPageData(updated);
      } catch (error) {
        console.error('[PluginStudioAdapter] Module update failed:', error);
        setConversionError(error as Error);
      } finally {
        isUpdatingModulesRef.current = false;
      }
    }
  }, [page?.modules, convertPageData, layouts]);

  /**
   * Determine render mode based on preview state
   */
  const renderMode: RenderMode = useMemo(() => {
    return previewMode ? RenderMode.PREVIEW : RenderMode.STUDIO;
  }, [previewMode]);

  // Phase 3: Add method to get committed Plugin Studio layouts
  const getCommittedPluginStudioLayouts = useCallback((): PluginStudioLayouts | null => {
    // Get committed layouts from unified state if available
    const committedLayouts = unifiedLayoutStateRef.current?.getCommittedLayouts?.();
    if (!committedLayouts) {
      console.warn('[PluginStudioAdapter] No committed layouts available from unified state');
      return null;
    }
    
    // Convert unified layouts to Plugin Studio format
    const convertUnifiedToPluginStudio = (items: LayoutItem[] = []): (PluginStudioGridItem | any)[] => {
      return items.map(item => {
        // Try to restore original item properties
        const originalItem = item.config?._originalItem;
        
        return {
          ...originalItem,
          i: item.i,
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
          minW: item.minW,
          minH: item.minH,
          pluginId: item.pluginId,
          args: {
            ...originalItem?.args,
            ...item.config,
            // Remove internal properties
            _pluginStudioItem: undefined,
            _originalItem: undefined
          }
        };
      });
    };
    
    return {
      desktop: convertUnifiedToPluginStudio(committedLayouts.desktop),
      tablet: convertUnifiedToPluginStudio(committedLayouts.tablet),
      mobile: convertUnifiedToPluginStudio(committedLayouts.mobile)
    };
  }, []);

  // Handle save functionality for the GridToolbar - MUST be defined before conditional returns
  const handleSave = useCallback(async (pageId?: string) => {
    if (!page || !convertedPageData) {
      console.error('[PluginStudioAdapter] Cannot save - missing page or convertedPageData');
      return;
    }
    
    try {
      console.log('[PluginStudioAdapter] Starting save operation for page:', pageId || page?.id);
      
      // RECODE V2 BLOCK: Get committed layouts directly from unified state for accurate dimensions
      let layoutsToSave = convertedPageData.layouts;
      
      // Try to get the committed layouts from unified state
      const committedLayouts = getCommittedPluginStudioLayouts();
      if (committedLayouts) {
        layoutsToSave = committedLayouts as any; // Type cast to match expected format
        console.log('[RECODE_V2_BLOCK] Using committed layouts from unified state for save');
      } else {
        console.log('[RECODE_V2_BLOCK] Fallback to convertedPageData layouts for save');
      }
      
      // RECODE V2 BLOCK: Log item dimensions at save time
      const desktopItems = layoutsToSave?.desktop || [];
      console.log('[RECODE_V2_BLOCK] PluginStudioAdapter save - item dimensions', {
        pageId: pageId || page?.id,
        source: committedLayouts ? 'committed' : 'convertedPageData',
        desktopItemDimensions: desktopItems.map((item: any) => ({
          id: item.i,
          dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
        })),
        timestamp: Date.now()
      });
      
      // Phase 5: Generate hash for save tracing
      const layoutStr = JSON.stringify(layoutsToSave);
      let hash = 0;
      for (let i = 0; i < layoutStr.length; i++) {
        const char = layoutStr.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash;
      }
      const saveHash = Math.abs(hash).toString(16).padStart(8, '0');
      console.log(`[PluginStudioAdapter] Save serialization - hash:${saveHash}`);
      
      // Convert the current unified layouts back to Plugin Studio format for saving
      const convertUnifiedToPluginStudio = (items: LayoutItem[] = []): (PluginStudioGridItem | any)[] => {
        return items.map(item => {
          // Try to restore original item properties
          const originalItem = item.config?._originalItem;
          
          return {
            ...originalItem,
            i: item.i,
            x: item.x,
            y: item.y,
            w: item.w,
            h: item.h,
            minW: item.minW,
            minH: item.minH,
            pluginId: item.pluginId,
            args: {
              ...originalItem?.args,
              ...item.config,
              // Remove internal properties
              _pluginStudioItem: undefined,
              _originalItem: undefined
            }
          };
        });
      };

      // Plugin Studio has no 'wide' breakpoint; map unified 'wide' into 'desktop' when needed
      const desktopSaveSource = (layoutsToSave.desktop && layoutsToSave.desktop.length > 0)
        ? layoutsToSave.desktop
        : (layoutsToSave.wide || []);

      const pluginStudioLayouts: PluginStudioLayouts = {
        desktop: convertUnifiedToPluginStudio(desktopSaveSource),
        tablet: convertUnifiedToPluginStudio(layoutsToSave.tablet),
        mobile: convertUnifiedToPluginStudio(layoutsToSave.mobile)
      };

      console.log('[PluginStudioAdapter] Converted layouts for save:', pluginStudioLayouts);

      // Phase 5: The layouts are already committed through handleUnifiedLayoutChange
      // No need to flush here as the convertedPageData is already up-to-date
      console.log('[PluginStudioAdapter] Layouts already committed and synced');

      // Update Plugin Studio state with the committed layouts
      if (onLayoutChange) {
        console.log('[PluginStudioAdapter] Calling onLayoutChange to sync state before save');
        onLayoutChange(desktopSaveSource, pluginStudioLayouts);
      }
      
      // Wait for layout changes to flush through the Plugin Studio system
      // This ensures the Plugin Studio state is fully updated before saving
      if (onLayoutChangeFlush) {
        console.log('[PluginStudioAdapter] Awaiting onLayoutChangeFlush to ensure state sync');
        await onLayoutChangeFlush();
      } else {
        // Fallback: Wait a bit to allow debounced updates to complete
        console.log('[PluginStudioAdapter] No onLayoutChangeFlush available, using timeout fallback');
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      
      // Pass the layoutOverride to savePage
      // This ensures we save exactly what we converted, bypassing any stale state
      if (onSave) {
        console.log('[PluginStudioAdapter] Calling onSave with layoutOverride');
        await onSave(pageId || page!.id, { layoutOverride: pluginStudioLayouts });
        console.log('[PluginStudioAdapter] Backend save completed');
      } else {
        console.error('[PluginStudioAdapter] onSave callback is missing - cannot save to backend!');
      }
      
      console.log('[PluginStudioAdapter] Save operation completed');
    } catch (error) {
      console.error('[PluginStudioAdapter] Save failed:', error);
      onError?.(error as Error);
    }
  }, [page, convertedPageData, onLayoutChange, onSave, onError, onLayoutChangeFlush]);

  // Show loading state during conversion
  if (isConverting) {
    return (
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        height="100%"
        p={3}
      >
        <div>Converting Plugin Studio data...</div>
      </Box>
    );
  }

  // Show error state if conversion failed
  if (conversionError || !convertedPageData) {
    return (
      <Box
        display="flex"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
        height="100%"
        p={3}
        color="error.main"
      >
        <div>Failed to convert Plugin Studio data</div>
        {conversionError && (
          <div style={{ marginTop: 8, fontSize: '0.875rem' }}>
            {conversionError.message}
          </div>
        )}
      </Box>
    );
  }

  return (
    <div
      className="plugin-studio-adapter"
      data-performance-monitoring={performanceMonitoring}
      data-unified-features={enableUnifiedFeatures}
      style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column' }}
    >
      {/* Add the critical Plugin Studio GridToolbar */}
      <GridToolbar onSave={handleSave} />
      
      {/* Use the full UnifiedPageRenderer but hide the mode controller with CSS */}
      <div className="plugin-studio-adapter__canvas">
        <style>
          {`
            .plugin-studio-adapter__canvas {
              flex: 1;
              position: relative;
              overflow: hidden;
            }

            .plugin-studio-adapter__canvas-inner {
              position: absolute;
              inset: 0;
              overflow: auto;
            }

            /* Hide the unified renderer mode controller and editing tools */
            .unified-page-renderer .mode-controller,
            .unified-page-renderer .studio-mode-controller,
            .unified-page-renderer .editing-tools,
            .unified-page-renderer .ui-elements,
            .unified-page-renderer [data-testid="mode-controller"],
            .unified-page-renderer [data-testid="studio-toolbar"],
            .unified-page-renderer .studio-toolbar {
              display: none !important;
            }

            /* Ensure unified container never exceeds its parent width within studio */
            .plugin-studio-adapter .unified-page-renderer,
            .plugin-studio-adapter .unified-page-renderer .layout-engine,
            .plugin-studio-adapter .unified-page-renderer .responsive-container,
            .plugin-studio-adapter .unified-page-renderer .layout-engine-container,
            .plugin-studio-adapter .unified-page-renderer .layout-engine-center,
            .plugin-studio-adapter .unified-page-renderer .layout-engine-inner {
              height: 100% !important;
              width: 100% !important;
              min-width: 100% !important;
              max-width: none !important;
              overflow: visible !important;
            }

            .plugin-studio-adapter .unified-page-renderer .layout-engine-inner > div[style*="width: ${canvasWidth}px"] {
              width: 100% !important;
              min-width: 100% !important;
            }

            /* Create expandable grid background like legacy Plugin Studio - Theme aware */
            .unified-page-renderer .responsive-container::before {
              content: '';
              position: absolute;
              top: 0;
              left: 0;
              right: 0;
              bottom: 0;
              background-image:
                linear-gradient(to right, ${theme.palette.mode === 'dark' ? '#424242' : '#e0e0e0'} 1px, transparent 1px),
                linear-gradient(to bottom, ${theme.palette.mode === 'dark' ? '#424242' : '#e0e0e0'} 1px, transparent 1px);
              background-size: 20px 20px;
              background-color: ${theme.palette.mode === 'dark' ? '#303030' : '#f5f5f5'};
              z-index: -1;
              min-height: 100vh;
              /* IMPORTANT: limit to container width (not viewport) to prevent right-side dead space */
              min-width: 100%;
            }

            /* Ensure the grid container can expand beyond viewport */
            .unified-page-renderer .react-grid-layout {
              min-height: ${canvasHeight}px !important;
              position: relative !important;
              width: ${canvasWidth}px !important;
              min-width: ${canvasWidth}px !important;
              overflow-x: hidden !important;
            }

            /* Force proper grid layout behavior - CRITICAL for positioning */
            .unified-page-renderer .react-grid-layout .react-grid-item {
              position: absolute !important;
              transition: all 200ms ease !important;
              transition-property: left, top !important;
            }

            /* Grid item basic styling - Theme aware */
            .unified-page-renderer .react-grid-item {
              background: ${theme.palette.background.paper} !important;
              border: 1px solid ${theme.palette.divider} !important;
              border-radius: 8px !important;
              box-shadow: ${theme.palette.mode === 'dark'
                ? '0 2px 4px rgba(0, 0, 0, 0.3)'
                : '0 2px 4px rgba(0, 0, 0, 0.1)'} !important;
              overflow: hidden !important;
              cursor: pointer !important;
            }

            /* Hover effect - Theme aware */
            .unified-page-renderer .react-grid-item:hover {
              box-shadow: ${theme.palette.mode === 'dark'
                ? '0 4px 12px rgba(0, 0, 0, 0.4)'
                : '0 4px 12px rgba(0, 0, 0, 0.15)'} !important;
              border-color: ${theme.palette.mode === 'dark' ? '#666666' : '#c0c0c0'} !important;
            }

            /* Selected module styling - keep subtle to avoid "mode switch" look */
            .unified-page-renderer .react-grid-item.selected,
            .unified-page-renderer .react-grid-item.layout-item--selected {
              border: 1px solid ${theme.palette.divider} !important;
              box-shadow: ${theme.palette.mode === 'dark'
                ? '0 2px 4px rgba(0, 0, 0, 0.3)'
                : '0 2px 4px rgba(0, 0, 0, 0.1)'} !important;
            }

            /* Resize handles - only show when selected */
            .unified-page-renderer .react-grid-item .react-resizable-handle {
              opacity: 0;
              transition: opacity 0.2s ease;
            }

            .unified-page-renderer .react-grid-item.selected .react-resizable-handle,
            .unified-page-renderer .react-grid-item.layout-item--selected .react-resizable-handle {
              opacity: 1;
            }

            /* Style the southeast resize handle - Theme aware */
            .unified-page-renderer .react-grid-item .react-resizable-handle-se {
              background: ${theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)'};
              border-radius: 3px;
              width: 16px !important;
              height: 16px !important;
              bottom: 3px !important;
              right: 3px !important;
              z-index: 10000 !important;
            }

            .unified-page-renderer .react-grid-item .react-resizable-handle-se::after {
              content: '↘';
              color: ${theme.palette.mode === 'dark' ? '#000' : '#fff'};
              font-size: 10px;
              position: absolute;
              top: 50%;
              left: 50%;
              transform: translate(-50%, -50%);
              z-index: 10001 !important;
            }

            /* Placeholder styling during drag - Theme aware */
            .unified-page-renderer .react-grid-item.react-grid-placeholder {
              background: ${theme.palette.primary.main}1A !important;
              border: 2px dashed ${theme.palette.primary.main} !important;
              border-radius: 8px !important;
              z-index: 2 !important;
              user-select: none !important;
            }

            /* Remove any container constraints */
            .plugin-studio-adapter > div:last-child {
              overflow: visible !important;
            }

            /* Ensure all control icons appear above module content */
            .unified-page-renderer .react-grid-item .react-grid-dragHandleExample,
            .unified-page-renderer .react-grid-item button[title="Configure"],
            .unified-page-renderer .react-grid-item button[title="Remove"] {
              z-index: 10000 !important;
            }

            /* Hide performance badges and debug elements */
            .react-grid-item div[style*="background-color: rgb(68, 68, 68)"],
            .react-grid-item div[style*="background-color: rgba(68, 68, 68"],
            .react-grid-item div[style*="background: rgb(68, 68, 68)"],
            .react-grid-item div[style*="background: rgba(68, 68, 68"] {
              display: none !important;
            }

            /* Maintain subtle selection during drag and resize operations */
            .layout-engine-container--dragging .react-grid-item.selected,
            .layout-engine-container--resizing .react-grid-item.selected,
            .layout-engine-container--dragging .react-grid-item.layout-item--selected,
            .layout-engine-container--resizing .react-grid-item.layout-item--selected {
              border: 1px solid ${theme.palette.divider} !important;
              box-shadow: ${theme.palette.mode === 'dark'
                ? '0 2px 6px rgba(0, 0, 0, 0.35)'
                : '0 2px 6px rgba(0, 0, 0, 0.12)'} !important;
            }

            /* Enhanced visual feedback during operations */
            .layout-engine-container--dragging .react-grid-item.selected {
              opacity: 0.8;
              transform: rotate(2deg);
            }

            .layout-engine-container--resizing .react-grid-item.selected {
              border-color: ${theme.palette.warning.main} !important;
              box-shadow: 0 4px 16px ${theme.palette.warning.main}66 !important;
            }

            /* Drag and drop visual feedback - Theme aware */
            .layout-engine-container--drag-over {
              background-color: ${theme.palette.primary.main}0D !important;
              border: 2px dashed ${theme.palette.primary.main} !important;
              border-radius: 8px !important;
            }

            .layout-engine-container--drag-over::before {
              content: 'Drop module here';
              position: absolute;
              top: 50%;
              left: 50%;
              transform: translate(-50%, -50%);
              background: ${theme.palette.primary.main}E6;
              color: ${theme.palette.primary.contrastText};
              padding: 12px 24px;
              border-radius: 4px;
              font-size: 16px;
              font-weight: 500;
              z-index: 1000;
              pointer-events: none;
            }

            /* Theme-aware scrollbar styling */
            .unified-page-renderer ::-webkit-scrollbar {
              width: 12px;
              height: 12px;
            }

            .unified-page-renderer ::-webkit-scrollbar-track {
              background: ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
              border-radius: 6px;
            }

            .unified-page-renderer ::-webkit-scrollbar-thumb {
              background: ${theme.palette.mode === 'dark' ? '#666666' : '#c1c1c1'};
              border-radius: 6px;
              border: 2px solid ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
            }

            .unified-page-renderer ::-webkit-scrollbar-thumb:hover {
              background: ${theme.palette.mode === 'dark' ? '#888888' : '#a8a8a8'};
            }

            .unified-page-renderer ::-webkit-scrollbar-corner {
              background: ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
            }

            /* Also apply to the main container */
            .plugin-studio-adapter ::-webkit-scrollbar {
              width: 12px;
              height: 12px;
            }

            .plugin-studio-adapter ::-webkit-scrollbar-track {
              background: ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
              border-radius: 6px;
            }

            .plugin-studio-adapter ::-webkit-scrollbar-thumb {
              background: ${theme.palette.mode === 'dark' ? '#666666' : '#c1c1c1'};
              border-radius: 6px;
              border: 2px solid ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
            }

            .plugin-studio-adapter ::-webkit-scrollbar-thumb:hover {
              background: ${theme.palette.mode === 'dark' ? '#888888' : '#a8a8a8'};
            }

            .plugin-studio-adapter ::-webkit-scrollbar-corner {
              background: ${theme.palette.mode === 'dark' ? '#424242' : '#f1f1f1'};
            }
          `}
        </style>
        
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          <div className="plugin-studio-adapter__canvas-inner">
            <UnifiedPageRenderer
              pageData={convertedPageData}
              mode={renderMode}
              allowUnpublished={true}
              responsive={true}
              // Plugin Studio editing: disable container queries to avoid accidental
              // breakpoint flips on first interaction due to container reflow.
              containerQueries={false}
              lazyLoading={true}
              onPageLoad={handleUnifiedPageLoad}
              onLayoutChange={handleUnifiedLayoutChange}
              onItemSelect={handleUnifiedModuleSelect}
              onItemConfig={handleModuleConfig}
              onItemRemove={handleModuleDelete}
              onError={onError}
              studioScale={zoom}
              studioCanvasWidth={canvasWidth}
              studioCanvasHeight={canvasHeight}
            />
          </div>
        </div>
      </div>

      {/* Performance overlay in development */}
      {performanceMonitoring && import.meta.env.MODE === 'development' && devModeFeatures.debugPanels && (
        <div style={{
          position: 'fixed',
          bottom: 10,
          right: 10,
          background: 'rgba(0,0,0,0.8)',
          color: 'white',
          padding: '8px 12px',
          borderRadius: '4px',
          fontSize: '12px',
          zIndex: 9999,
          fontFamily: 'monospace'
        }}>
          <div>Plugin Studio Adapter</div>
          <div>Conversion: {performanceMetrics.conversionTime?.toFixed(2)}ms</div>
          <div>Render: {performanceMetrics.renderTime?.toFixed(2)}ms</div>
          <div>Mode: {renderMode}</div>
          <div>Items: {convertedPageData.layouts.desktop.length}</div>
        </div>
      )}

      {/* Dev: Layout commit status badge for unified path as well */}
      {import.meta.env.VITE_LAYOUT_DEBUG === 'true' && (
        <LayoutCommitBadge position="bottom-left" />
      )}
    </div>
  );
};

export default PluginStudioAdapter;
