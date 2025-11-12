import { useState, useCallback, useEffect, useRef } from 'react';
import { Layouts, LayoutItem, GridItem, Page, ModuleDefinition } from '../../types';

const USER_LAYOUT_ORIGINS = new Set(['user-drag', 'user-resize', 'drop-add']);

const stripInternalConfig = (config?: Record<string, any>): Record<string, any> => {
  if (!config || typeof config !== 'object') {
    return {};
  }

  const {
    _pluginStudioItem,
    _originalItem,
    _originalModule,
    _legacy,
    ...rest
  } = config as Record<string, any>;

  return { ...rest };
};

const inferPluginIdFromModuleKey = (moduleKey?: string): string | undefined => {
  if (!moduleKey) return undefined;
  const parts = moduleKey.split('_').filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0]}_${parts[1]}`;
  }
  return parts[0];
};

const extractDefaultsFromDefinition = (moduleDef?: Record<string, any>): Record<string, any> => {
  if (!moduleDef) return {};

  if (moduleDef.configFields) {
    return Object.entries(moduleDef.configFields).reduce<Record<string, any>>((acc, [key, field]) => {
      if (field && typeof field === 'object' && 'default' in field) {
        acc[key] = (field as Record<string, any>).default;
      }
      return acc;
    }, {});
  }

  if (moduleDef.props) {
    return Object.entries(moduleDef.props).reduce<Record<string, any>>((acc, [key, prop]) => {
      if (prop && typeof prop === 'object' && 'default' in prop) {
        acc[key] = (prop as Record<string, any>).default;
      }
      return acc;
    }, {});
  }

  return {};
};

type LayoutChangeMetadata = {
  version?: number;
  hash?: string;
  origin?: {
    source?: string;
    [key: string]: unknown;
  } | null;
};

/**
 * Custom hook for managing layouts
 * @param initialPage The initial page to get layouts from
 * @param getModuleById Optional function to get module definition by ID
 * @returns Layout management functions and state
 */
export const useLayout = (
  initialPage: Page | null,
  getModuleById?: (pluginId: string, moduleId: string) => any
) => {
  const [layouts, setLayouts] = useState<Layouts | null>(initialPage?.layouts || null);
  
  // Phase 1: Add debug mode flag
  const isDebugMode = import.meta.env.VITE_LAYOUT_DEBUG === 'true';
  
  // Track performance metrics
  const performanceMetricsRef = useRef<{ lastUpdate: number }>({ lastUpdate: 0 });
  
  // Track the current page ID to detect page changes
  const currentPageIdRef = useRef<string | null>(null);
  
  // Update layouts when the page changes
  useEffect(() => {
    // Only update if the page ID actually changed
    if (currentPageIdRef.current === initialPage?.id) {
      return;
    }
    
    currentPageIdRef.current = initialPage?.id || null;
    
    
    if (initialPage?.layouts) {
      // Create a deep copy of the layouts to ensure we're not sharing references
      const layoutsCopy = JSON.parse(JSON.stringify(initialPage.layouts));
      
      setLayouts(layoutsCopy);
      // Reset the last processed layout when page changes
      lastProcessedLayoutRef.current = JSON.stringify(layoutsCopy);
    } else {
      setLayouts({
        desktop: [],
        tablet: [],
        mobile: []
      });
      lastProcessedLayoutRef.current = null;
    }
  }, [initialPage?.id]);
  
  /**
   * Handle layout changes
   * @param layout The new layout
   * @param newLayouts The new layouts for all device types
   */
  // Use refs to track layout changes and prevent rapid successive updates
  const lastProcessedLayoutRef = useRef<string | null>(null);
  const pendingLayoutRef = useRef<{ layout: any[]; newLayouts: Layouts; metadata?: LayoutChangeMetadata } | null>(null);
  const layoutUpdateTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isResizingRef = useRef(false);
  const resizeEndTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const pagePersistTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const pendingPersistLayoutsRef = useRef<Layouts | null>(null);
  
  const syncModulesWithLayouts = useCallback((layoutsToSync: Layouts) => {
    if (!initialPage) {
      return;
    }

    const existingModules = initialPage.modules || {};
    const nextModules: Record<string, ModuleDefinition> = {};
    const visited = new Set<string>();

    const mergeModule = (item: GridItem | LayoutItem | any) => {
      if (!item) return;

      const key = ('moduleUniqueId' in item ? item.moduleUniqueId : item.i) || item.i;
      if (!key || visited.has(key)) {
        return;
      }

      visited.add(key);

      const rawArgs =
        ('args' in item ? item.args : undefined) ||
        ('configOverrides' in item ? item.configOverrides : undefined) ||
        ('config' in item ? item.config : undefined) ||
        existingModules[key]?.config ||
        {};

      const cleanedConfig = stripInternalConfig(rawArgs);
      const previousEntry = existingModules[key];
      const inferredModuleId =
        cleanedConfig.moduleId ||
        ('moduleId' in item ? item.moduleId : undefined) ||
        previousEntry?.moduleId ||
        key;

      const candidatePluginId =
        ('pluginId' in item ? item.pluginId : undefined) ||
        cleanedConfig.pluginId ||
        previousEntry?.pluginId ||
        inferPluginIdFromModuleKey(inferredModuleId) ||
        inferPluginIdFromModuleKey(key) ||
        'unknown';

      let mergedConfig = {
        ...(previousEntry?.config || {}),
        ...cleanedConfig
      };

      if (getModuleById && (!previousEntry || Object.keys(previousEntry.config || {}).length === 0)) {
        const moduleDef = getModuleById(candidatePluginId, inferredModuleId);
        if (moduleDef) {
          const defaults = extractDefaultsFromDefinition(moduleDef);
          mergedConfig = { ...defaults, ...mergedConfig };
        }
      }

      nextModules[key] = {
        pluginId: candidatePluginId,
        moduleId: inferredModuleId,
        moduleName: previousEntry?.moduleName || mergedConfig.displayName || mergedConfig.moduleName || key,
        config: mergedConfig
      };
    };

    ['desktop', 'tablet', 'mobile'].forEach(deviceType => {
      (layoutsToSync[deviceType as keyof Layouts] || []).forEach(mergeModule);
    });

    const previousHash = JSON.stringify(existingModules);
    const nextHash = JSON.stringify(nextModules);
    if (previousHash === nextHash) {
      return;
    }

    initialPage.modules = nextModules;
    if (initialPage.content) {
      initialPage.content = {
        ...initialPage.content,
        modules: JSON.parse(JSON.stringify(nextModules)),
        layouts: initialPage.content.layouts || initialPage.layouts
      };
    } else {
      initialPage.content = {
        layouts: initialPage.layouts,
        modules: JSON.parse(JSON.stringify(nextModules))
      };
    }
  }, [initialPage, getModuleById]);

  const commitLayoutsToPage = useCallback((validatedLayouts: Layouts) => {
    if (!initialPage) {
      return;
    }

    const layoutsCopy = JSON.parse(JSON.stringify(validatedLayouts));
    initialPage.layouts = layoutsCopy;

    if (initialPage.content) {
      initialPage.content.layouts = layoutsCopy;
    }
  }, [initialPage]);

  const schedulePagePersistence = useCallback((validatedLayouts: Layouts, { immediate } = { immediate: false }) => {
    pendingPersistLayoutsRef.current = validatedLayouts;

    if (pagePersistTimeoutRef.current) {
      clearTimeout(pagePersistTimeoutRef.current);
      pagePersistTimeoutRef.current = null;
    }

    if (immediate) {
      commitLayoutsToPage(validatedLayouts);
      pendingPersistLayoutsRef.current = null;
      return;
    }

    pagePersistTimeoutRef.current = setTimeout(() => {
      if (pendingPersistLayoutsRef.current) {
        commitLayoutsToPage(pendingPersistLayoutsRef.current);
        pendingPersistLayoutsRef.current = null;
      }
      pagePersistTimeoutRef.current = null;
    }, 120);
  }, [commitLayoutsToPage]);

  const processLayoutChange = useCallback((_layout: any[], newLayouts: Layouts, options?: { metadata?: LayoutChangeMetadata; forcePersist?: boolean }) => {


    // Track performance metrics
    performanceMetricsRef.current.lastUpdate = Date.now();
    
    // Validate and ensure all layout items have required properties
    const validateLayouts = (layouts: Layouts): Layouts => {
      const result: Layouts = { desktop: [], tablet: [], mobile: [] };
      
      // Helper function to validate a single layout item
      const validateItem = (item: any) => {
        if (!item) return null;
        
        // Ensure all required properties exist and are valid numbers
        return {
          ...item,
          x: typeof item.x === 'number' ? item.x : 0,
          y: typeof item.y === 'number' ? item.y : 0,
          w: typeof item.w === 'number' ? item.w : 2,
          h: typeof item.h === 'number' ? item.h : 2,
          i: item.i || item.moduleUniqueId || `item_${item.x}_${item.y}_${item.w}_${item.h}`
        };
      };
      
      // Helper function to deduplicate items by ID
      const deduplicateItems = (items: any[]) => {
        const seen = new Set();
        return items.filter(item => {
          if (!item) return false;
          
          const id = item.i || item.moduleUniqueId;
          if (seen.has(id)) return false;
          
          seen.add(id);
          return true;
        });
      };
      
      // Validate each layout array
      Object.entries(layouts).forEach(([deviceType, layoutItems]) => {
        if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
          // First deduplicate items, then validate them
          result[deviceType as keyof Layouts] = deduplicateItems(layoutItems)
            .map(validateItem)
            .filter(Boolean) as (GridItem | LayoutItem)[];
        }
      });
      
      return result;
    };
    
    // Validate the layouts
    const validatedLayouts = validateLayouts(newLayouts);
    syncModulesWithLayouts(validatedLayouts);

    // Update state with validated layouts
    setLayouts(validatedLayouts);
    schedulePagePersistence(validatedLayouts, { immediate: options?.forcePersist ?? false });
  }, [schedulePagePersistence, syncModulesWithLayouts]);

  const handleLayoutChange = useCallback((layout: any[], newLayouts: Layouts, metadata?: LayoutChangeMetadata) => {
    // Phase 1: Log layout change event
    if (isDebugMode && metadata) {
      const version = metadata.version || 0;
      const hash = metadata.hash || '';
      console.log(`[useLayout] Apply v${version} hash:${hash}`, {
        origin: metadata.origin,
        timestamp: Date.now()
      });
    }
    
    // RECODE V2 BLOCK: Log item dimensions when applying layout changes
    if (metadata?.origin?.source === 'user-resize') {
      const desktopItems = newLayouts?.desktop || [];
      console.log('[RECODE_V2_BLOCK] useLayout apply - resize dimensions', {
        source: metadata.origin.source,
        version: metadata.version,
        hash: metadata.hash,
        desktopItemDimensions: desktopItems.map((item: any) => ({
          id: item.i,
          dimensions: { w: item.w, h: item.h, x: item.x, y: item.y }
        })),
        timestamp: Date.now()
      });
    }
    
    // Create a stable hash of the new layouts for comparison
    const newLayoutsHash = JSON.stringify(newLayouts);
    
    // Check if this is the exact same layout we just processed
    const timeSinceLastUpdate = Date.now() - performanceMetricsRef.current.lastUpdate;
    const isImmediateDuplicate = lastProcessedLayoutRef.current === newLayoutsHash && timeSinceLastUpdate < 200;
    
    if (isImmediateDuplicate) {
      
      return;
    }
    
    const originSource = metadata?.origin?.source;
    const treatAsUserAction = USER_LAYOUT_ORIGINS.has(originSource || '') || !metadata?.origin;

    if (treatAsUserAction) {
      if (layoutUpdateTimeoutRef.current) {
        clearTimeout(layoutUpdateTimeoutRef.current);
        layoutUpdateTimeoutRef.current = null;
      }
      pendingLayoutRef.current = null;

      if (newLayoutsHash === lastProcessedLayoutRef.current) {

        return;
      }

      lastProcessedLayoutRef.current = newLayoutsHash;
      processLayoutChange(layout, newLayouts, { metadata });
      return;
    }

    // Store the pending layout update for non-user origins
    pendingLayoutRef.current = { layout, newLayouts, metadata };

    if (layoutUpdateTimeoutRef.current) {
      clearTimeout(layoutUpdateTimeoutRef.current);
    }

    layoutUpdateTimeoutRef.current = setTimeout(() => {
      if (pendingLayoutRef.current) {
        const { layout: pendingLayout, newLayouts: pendingNewLayouts, metadata: pendingMetadata } = pendingLayoutRef.current;
        const pendingHash = JSON.stringify(pendingNewLayouts);

        if (pendingHash === lastProcessedLayoutRef.current) {

          pendingLayoutRef.current = null;
          layoutUpdateTimeoutRef.current = null;
          return;
        }

        lastProcessedLayoutRef.current = pendingHash;
        processLayoutChange(pendingLayout, pendingNewLayouts, { metadata: pendingMetadata });
        pendingLayoutRef.current = null;
      }
      layoutUpdateTimeoutRef.current = null;
    }, 120);
  }, [processLayoutChange, isDebugMode]);
  
  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (layoutUpdateTimeoutRef.current) {
        clearTimeout(layoutUpdateTimeoutRef.current);
      }
      if (resizeEndTimeoutRef.current) {
        clearTimeout(resizeEndTimeoutRef.current);
      }
      if (pagePersistTimeoutRef.current) {
        clearTimeout(pagePersistTimeoutRef.current);
      }
    };
  }, []);
  
  /**
   * Remove an item from all layouts
   * @param id The ID of the item to remove
   */
  const removeItem = useCallback((id: string) => {
    if (!layouts) return;
    
    const updatedLayouts = Object.entries(layouts).reduce<Layouts>((acc, [deviceType, layout]) => {
      if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
        acc[deviceType as keyof Layouts] = layout.filter((item: GridItem | LayoutItem) =>
          'i' in item ? item.i !== id : (item as LayoutItem).moduleUniqueId !== id
        );
      }
      return acc;
    }, { desktop: [], tablet: [], mobile: [] });
    
    setLayouts(updatedLayouts);
  }, [layouts]);
  
  /**
   * Copy layout from one device type to another
   * @param from Source device type
   * @param to Target device type
   */
  const copyLayout = useCallback((from: keyof Layouts, to: keyof Layouts) => {
    if (!layouts || !layouts[from]) return;
    
    const sourceLayout = layouts[from];
    const colRatio = to === 'mobile' ? 4/12 : to === 'tablet' ? 8/12 : 1;
    
    // Helper function to validate and ensure all required properties
    const validateAndAdjustItem = (item: any) => {
      if (!item) return null;
      
      // Ensure all required properties exist and are valid numbers
      const x = typeof item.x === 'number' ? item.x : 0;
      const y = typeof item.y === 'number' ? item.y : 0;
      const w = typeof item.w === 'number' ? item.w : 2;
      const h = typeof item.h === 'number' ? item.h : 2;
      
      // Adjust width based on column differences
      const adjustedWidth = Math.min(
        Math.floor(w * colRatio),
        to === 'mobile' ? 4 : to === 'tablet' ? 8 : 12
      );
      
      // For mobile and tablet, ensure items are stacked vertically
      const adjustedX = to === 'mobile' ? 0 : to === 'tablet' ? Math.min(x, 4) : x;
      
      return {
        ...item,
        x: adjustedX,
        y: y,
        w: adjustedWidth,
        h: h,
        i: item.i || item.moduleUniqueId || `item_${item.x}_${item.y}_${item.w}_${item.h}`
      };
    };
    
    const updatedLayouts = {
      ...layouts,
      [to]: sourceLayout
        .map(validateAndAdjustItem)
        .filter(Boolean) // Remove any null items
    };
    
    setLayouts(updatedLayouts);
  }, [layouts]);
  
  /**
   * Add an item to all layouts
   * @param item The item to add
   * @param activeDeviceType The currently active device type
   */
  const addItem = useCallback((item: GridItem | LayoutItem, activeDeviceType: keyof Layouts) => {
    if (!layouts) return;
    
    // Create a copy of the current layouts
    const currentLayouts = { ...layouts };
    
    // Ensure all layout arrays exist
    if (!currentLayouts.desktop) currentLayouts.desktop = [];
    if (!currentLayouts.tablet) currentLayouts.tablet = [];
    if (!currentLayouts.mobile) currentLayouts.mobile = [];
    
    // Create a layout item with moduleUniqueId for better compatibility
    const layoutItem = {
      ...item,
      moduleUniqueId: item.i, // Add moduleUniqueId for compatibility with LayoutItem
    };
    
    // Helper function to remove any existing items with the same ID
    const removeExistingItems = (layouts: any[], itemId: string) => {
      return layouts.filter(existingItem => {
        const existingId = 'i' in existingItem ? existingItem.i : existingItem.moduleUniqueId;
        return existingId !== itemId;
      });
    };
    
    // Helper function to safely calculate max y position
    const safeCalculateMaxY = (layouts: any[]) => {
      if (layouts.length === 0) return 0;
      
      // Filter out items without valid y and h properties
      const validItems = layouts.filter(i =>
        i && typeof i.y === 'number' && typeof i.h === 'number' &&
        typeof i.x === 'number' && typeof i.w === 'number'
      );
      
      if (validItems.length === 0) return 0;
      return Math.max(...validItems.map(i => i.y + i.h));
    };
    
    // Add the item to all layouts
    // Ensure all required properties are set with valid values
    const ensureValidLayoutItem = (baseItem: any, deviceType: keyof Layouts) => {
      const result = { ...baseItem };
      
      // Ensure x is a valid number
      result.x = activeDeviceType === deviceType ?
        (typeof item.x === 'number' ? item.x : 0) : 0;
      
      // Ensure y is a valid number
      result.y = activeDeviceType === deviceType ?
        (typeof item.y === 'number' ? item.y : 0) :
        safeCalculateMaxY(currentLayouts[deviceType] || []);
      
      // Ensure w is a valid number and fits the device
      if (deviceType === 'mobile') {
        result.w = Math.min(typeof item.w === 'number' ? item.w : 2, 4);
      } else if (deviceType === 'tablet') {
        result.w = Math.min(typeof item.w === 'number' ? item.w : 4, 8);
      } else {
        result.w = typeof item.w === 'number' ? item.w : 3;
      }
      
      // Ensure h is a valid number
      result.h = typeof item.h === 'number' ? item.h : 2;
      
      return result;
    };
    
    // Remove any existing items with the same ID before adding the new one
    const desktopWithoutDuplicates = removeExistingItems(currentLayouts.desktop, item.i);
    const tabletWithoutDuplicates = removeExistingItems(currentLayouts.tablet, item.i);
    const mobileWithoutDuplicates = removeExistingItems(currentLayouts.mobile, item.i);
    
    const updatedLayouts = {
      desktop: [...desktopWithoutDuplicates, ensureValidLayoutItem(layoutItem, 'desktop')],
      tablet: [...tabletWithoutDuplicates, ensureValidLayoutItem(layoutItem, 'tablet')],
      mobile: [...mobileWithoutDuplicates, ensureValidLayoutItem(layoutItem, 'mobile')]
    };
    
    console.log('Updated layouts after adding item:', updatedLayouts);
    
    // Update the layouts state
    setLayouts(updatedLayouts);
    
    // If initialPage is provided, update the page's modules and layouts
    if (initialPage) {
      // Check if the item is a GridItem (has args property)
      const isGridItem = 'pluginId' in item && 'args' in item;
      
      // Use the usePlugins hook to get module information
      // We can't directly import it here since this is inside a callback
      // Instead, we'll need to pass it as a dependency to the useCallback
      
      // Get the module ID and plugin ID
      const moduleId = isGridItem ? (item as GridItem).args?.moduleId || '' : '';
      const pluginId = isGridItem ? (item as GridItem).pluginId : '';
      
      // Get the full module definition from the plugin registry if getModuleById is available
      const moduleDef = getModuleById ? getModuleById(pluginId, moduleId) : null;
      
      // Create a module entry for the page's modules with complete configuration
      const moduleEntry = {
        pluginId: pluginId,
        moduleId: moduleId,
        moduleName: moduleDef?.name || moduleId,
        config: {}
      };
      
      // Add configuration from the module definition
      if (moduleDef) {
        // Add config fields from the module definition
        if (moduleDef.configFields) {
          // Extract default values from configFields
          const defaultConfig: Record<string, any> = {};
          Object.entries(moduleDef.configFields).forEach(([key, field]) => {
            // Add type assertion for field
            const configField = field as Record<string, any>;
            if ('default' in configField) {
              defaultConfig[key] = configField.default;
            }
          });
          moduleEntry.config = { ...defaultConfig };
        }
        // Also check for props if configFields is not available
        else if (moduleDef.props) {
          const defaultConfig: Record<string, any> = {};
          Object.entries(moduleDef.props).forEach(([key, prop]) => {
            const propField = prop as Record<string, any>;
            if ('default' in propField) {
              defaultConfig[key] = propField.default;
            }
          });
          moduleEntry.config = { ...defaultConfig };
        }
        
        // Add any args from the item
        if (isGridItem && (item as GridItem).args) {
          moduleEntry.config = { ...moduleEntry.config, ...(item as GridItem).args };
        }
        
        // Add layoutConfig if available in the module definition
        if (moduleDef.layoutConfig) {
          (moduleEntry as any).layoutConfig = JSON.parse(JSON.stringify(moduleDef.layoutConfig));
        }
      } else {
        // Fallback to just using the args if module definition not found
        moduleEntry.config = isGridItem ? (item as GridItem).args || {} : {};
      }
      
      console.log('Adding module to page with complete config:', moduleEntry);
      
      // Update the initialPage's modules (this won't persist until savePage is called)
      // Create a consistent module ID format - replace underscore with nothing between pluginId and moduleId
      const moduleKey = item.i.replace(/_/g, '');
      
      console.log('Adding module with key:', moduleKey, 'original item.i:', item.i);
      
      if (initialPage.modules) {
        initialPage.modules[moduleKey] = moduleEntry;
      } else {
        initialPage.modules = { [moduleKey]: moduleEntry };
      }
      
      // Create a layout item with moduleUniqueId
      const layoutItem = {
        moduleUniqueId: item.i,
        i: item.i,
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
        minW: 'minW' in item ? item.minW : undefined,
        minH: 'minH' in item ? item.minH : undefined
      };
      
      // Update the initialPage's layouts
      if (!initialPage.layouts) {
        initialPage.layouts = { desktop: [], tablet: [], mobile: [] };
      }
      
      // Ensure all layout arrays exist
      if (!initialPage.layouts.desktop) initialPage.layouts.desktop = [];
      if (!initialPage.layouts.tablet) initialPage.layouts.tablet = [];
      if (!initialPage.layouts.mobile) initialPage.layouts.mobile = [];
      
      // Remove any existing items with the same ID before adding the new one
      initialPage.layouts.desktop = removeExistingItems(initialPage.layouts.desktop, item.i);
      initialPage.layouts.tablet = removeExistingItems(initialPage.layouts.tablet, item.i);
      initialPage.layouts.mobile = removeExistingItems(initialPage.layouts.mobile, item.i);
      
      // Add the item to all layouts in initialPage.layouts
      initialPage.layouts.desktop.push(ensureValidLayoutItem(layoutItem, 'desktop'));
      initialPage.layouts.tablet.push(ensureValidLayoutItem(layoutItem, 'tablet'));
      initialPage.layouts.mobile.push(ensureValidLayoutItem(layoutItem, 'mobile'));
      
      // Update the initialPage's content.layouts as well
      if (initialPage.content) {
        if (!initialPage.content.layouts) {
          initialPage.content.layouts = { desktop: [], tablet: [], mobile: [] };
        }
        
        // Add the item to all content layouts
        if (!initialPage.content.layouts.desktop) initialPage.content.layouts.desktop = [];
        if (!initialPage.content.layouts.tablet) initialPage.content.layouts.tablet = [];
        if (!initialPage.content.layouts.mobile) initialPage.content.layouts.mobile = [];
        
        // Remove any existing items with the same ID before adding the new one
        initialPage.content.layouts.desktop = removeExistingItems(initialPage.content.layouts.desktop, item.i);
        initialPage.content.layouts.tablet = removeExistingItems(initialPage.content.layouts.tablet, item.i);
        initialPage.content.layouts.mobile = removeExistingItems(initialPage.content.layouts.mobile, item.i);
        
        initialPage.content.layouts.desktop.push(ensureValidLayoutItem(layoutItem, 'desktop'));
        initialPage.content.layouts.tablet.push(ensureValidLayoutItem(layoutItem, 'tablet'));
        initialPage.content.layouts.mobile.push(ensureValidLayoutItem(layoutItem, 'mobile'));
      }
      
      // Log the updated modules and layouts
      console.log('Updated page modules:', initialPage.modules);
      console.log('Updated page layouts:', initialPage.layouts);
      console.log('Updated page content.layouts:', initialPage.content?.layouts);
    }
  }, [layouts, initialPage, getModuleById]);
  
  /**
   * Update an item in all layouts
   * @param id The ID of the item to update
   * @param updates The updates to apply to the item
   */
  const updateItem = useCallback((id: string, updates: Partial<GridItem | LayoutItem>) => {
    if (!layouts) return;
    
    // Helper function to validate a layout item
    const validateItem = (item: any) => {
      if (!item) return null;
      
      // Ensure all required properties exist and are valid numbers
      return {
        ...item,
        x: typeof item.x === 'number' ? item.x : 0,
        y: typeof item.y === 'number' ? item.y : 0,
        w: typeof item.w === 'number' ? item.w : 2,
        h: typeof item.h === 'number' ? item.h : 2,
        i: item.i || item.moduleUniqueId || `item_${item.x}_${item.y}_${item.w}_${item.h}`
      };
    };
    
    const updatedLayouts = Object.entries(layouts).reduce<Layouts>((acc, [deviceType, layout]) => {
      if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
        acc[deviceType as keyof Layouts] = layout.map((item: GridItem | LayoutItem) => {
          const itemId = 'i' in item ? item.i : (item as LayoutItem).moduleUniqueId;
          if (itemId === id) {
            // Apply updates and validate the result
            return validateItem({ ...item, ...updates });
          }
          // Validate existing items as well
          return validateItem(item);
        }).filter(Boolean) as (GridItem | LayoutItem)[];
      }
      return acc;
    }, { desktop: [], tablet: [], mobile: [] });
    
    setLayouts(updatedLayouts);
  }, [layouts]);
  
  /**
   * Handle resize start event
   */
  const handleResizeStart = useCallback(() => {
    isResizingRef.current = true;
    
    // Clear any existing resize end timeout
    if (resizeEndTimeoutRef.current) {
      clearTimeout(resizeEndTimeoutRef.current);
    }
  }, []);
  
  /**
   * Handle resize stop event
   */
  const handleResizeStop = useCallback(() => {
    // Set a timeout to mark resize as ended after a delay
    // This ensures the longer debounce is used for the entire resize operation
    resizeEndTimeoutRef.current = setTimeout(() => {
      isResizingRef.current = false;
    }, 200);
  }, []);
  
  /**
   * Phase 3: Flush pending layout changes
   * Returns a promise that resolves when all pending layout changes have been processed
   */
  const flush = useCallback((): Promise<void> => {
    return new Promise((resolve) => {
      // If there's a pending layout update, wait for it to complete
      if (layoutUpdateTimeoutRef.current) {
        // Clear the existing timeout
        clearTimeout(layoutUpdateTimeoutRef.current);
        
        // Process the pending layout immediately if there is one
        if (pendingLayoutRef.current) {
          const { layout: pendingLayout, newLayouts: pendingNewLayouts } = pendingLayoutRef.current;
          const pendingHash = JSON.stringify(pendingNewLayouts);
          
          // Only process if it's different from the last processed layout
        if (pendingHash !== lastProcessedLayoutRef.current) {
          lastProcessedLayoutRef.current = pendingHash;
          processLayoutChange(pendingLayout, pendingNewLayouts, { metadata: pendingMetadata, forcePersist: true });
        }

        pendingLayoutRef.current = null;
      }
        
        if (pendingPersistLayoutsRef.current) {
          commitLayoutsToPage(pendingPersistLayoutsRef.current);
          pendingPersistLayoutsRef.current = null;
        }
        if (pagePersistTimeoutRef.current) {
          clearTimeout(pagePersistTimeoutRef.current);
          pagePersistTimeoutRef.current = null;
        }

        layoutUpdateTimeoutRef.current = null;
        
        // Wait a bit to ensure the change has propagated
        setTimeout(resolve, 50);
      } else {
        if (pendingPersistLayoutsRef.current) {
          commitLayoutsToPage(pendingPersistLayoutsRef.current);
          pendingPersistLayoutsRef.current = null;
        }
        if (pagePersistTimeoutRef.current) {
          clearTimeout(pagePersistTimeoutRef.current);
          pagePersistTimeoutRef.current = null;
        }
        // No pending changes, resolve immediately
        resolve();
      }
    });
  }, [processLayoutChange, commitLayoutsToPage]);
  
  return {
    layouts,
    setLayouts,
    handleLayoutChange,
    removeItem,
    copyLayout,
    addItem,
    updateItem,
    handleResizeStart,
    handleResizeStop,
    flush // Phase 3: Expose flush method
  };
};
