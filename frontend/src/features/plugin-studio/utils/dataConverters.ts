import { PageData, ResponsiveLayouts, LayoutItem, ModuleConfig, PageMetadata } from '../../unified-dynamic-page-renderer/types';
import { Page, Layouts, GridItem, LayoutItem as PluginStudioLayoutItem } from '../types';

const sanitizeConfig = (config?: Record<string, any>): Record<string, any> => {
  if (!config || typeof config !== 'object') {
    return {};
  }

  const {
    _originalItem,
    _pluginStudioItem,
    _originalModule,
    _legacy,
    ...rest
  } = config as Record<string, any>;

  return { ...rest };
};

const buildLayoutItemConfig = (
  item: GridItem | PluginStudioLayoutItem,
  moduleLookup: Record<string, any> = {}
): ModuleConfig => {
  const key = 'moduleUniqueId' in item ? item.moduleUniqueId : item.i;
  const moduleEntry = moduleLookup[key];
  const baseConfig = moduleEntry?.config || {};
  // Prefer explicit overrides present on the layout item
  const overrides =
    ('configOverrides' in item ? item.configOverrides : undefined) ||
    ('args' in item ? item.args : undefined) ||
    (item as any).config ||
    {};

  const merged = {
    ...baseConfig,
    ...overrides
  };

  return {
    ...merged,
    moduleId: moduleEntry?.moduleId || merged.moduleId || key,
    pluginId: moduleEntry?.pluginId || merged.pluginId,
    // Keep references for round-tripping/conversion debugging
    _originalItem: item,
    _pluginStudioItem: item,
    _originalModule: moduleEntry
  } as ModuleConfig;
};

/**
 * Convert Plugin Studio data to Unified format
 * Simple, direct mapping - no complex conversion logic
 */
export const convertPluginStudioToUnified = (
  page: Page,
  layouts: Layouts
): PageData => {
  const moduleLookup = page.modules || {};

  return {
    id: page.id,
    name: page.name,
    route: page.route || `/plugin-studio/${page.id}`,
    layouts: {
      desktop: layouts.desktop?.map(item => convertToLayoutItem(item, moduleLookup)) || [],
      tablet: layouts.tablet?.map(item => convertToLayoutItem(item, moduleLookup)) || [],
      mobile: layouts.mobile?.map(item => convertToLayoutItem(item, moduleLookup)) || [],
      wide: layouts.desktop?.map(item => convertToLayoutItem(item, moduleLookup)) || [], // Fallback
      ultrawide: layouts.desktop?.map(item => convertToLayoutItem(item, moduleLookup)) || [] // Fallback
    },
    modules: Object.entries(moduleLookup).map(([id, module]) => {
      const config = module?.config || {};
      return {
        id,
        pluginId: module.pluginId,
        moduleId: module.moduleId || config.moduleId || id,
        moduleName: module.moduleName,
        type: 'component',
        ...config,
        // Preserve original module data for reference
        _originalModule: module,
        _legacy: {
          moduleName: module.moduleName,
          originalConfig: { ...config }
        }
      } as ModuleConfig;
    }),
    metadata: {
      title: page.name,
      description: page.description,
      lastModified: new Date()
    } as PageMetadata,
    isPublished: page.is_published || false
  };
};

/**
 * Convert Unified layouts back to Plugin Studio format
 * Simple, direct mapping - preserve all original properties
 */
export const convertUnifiedToPluginStudio = (
  unifiedLayouts: ResponsiveLayouts
): Layouts => {
  return {
    desktop: unifiedLayouts.desktop?.map(convertLayoutItemToGridItem) || [],
    tablet: unifiedLayouts.tablet?.map(convertLayoutItemToGridItem) || [],
    mobile: unifiedLayouts.mobile?.map(convertLayoutItemToGridItem) || []
  };
};

/**
 * Convert Plugin Studio item (GridItem or LayoutItem) to Unified LayoutItem
 * Simple, direct mapping without complex logic
 */
const convertToLayoutItem = (
  item: GridItem | PluginStudioLayoutItem,
  moduleLookup: Record<string, any> = {}
): LayoutItem => {
  // Handle GridItem
  if ('pluginId' in item) {
    const gridItem = item as GridItem;
    return {
      i: gridItem.i,
      x: gridItem.x || 0,
      y: gridItem.y || 0,
      w: gridItem.w || 1,
      h: gridItem.h || 1,
      minW: gridItem.minW,
      minH: gridItem.minH,
      moduleId: gridItem.i,
      pluginId: gridItem.pluginId,
      config: buildLayoutItemConfig(gridItem, moduleLookup),
      isDraggable: true,
      isResizable: true,
      static: false
    };
  }
  
  // Handle LayoutItem (Plugin Studio version)
  const layoutItem = item as PluginStudioLayoutItem;
  // Extract pluginId from moduleUniqueId if possible
  let pluginId = 'unknown';
  if (layoutItem.moduleUniqueId) {
    const parts = layoutItem.moduleUniqueId.split('_');
    if (parts.length >= 1) {
      pluginId = parts[0];
    }
  }
  
  return {
    i: layoutItem.i,
    x: layoutItem.x || 0,
    y: layoutItem.y || 0,
    w: layoutItem.w || 1,
    h: layoutItem.h || 1,
    minW: layoutItem.minW,
    minH: layoutItem.minH,
    moduleId: layoutItem.moduleUniqueId,
    pluginId: pluginId,
    config: buildLayoutItemConfig(layoutItem, moduleLookup),
    isDraggable: true,
    isResizable: true,
    static: false
  };
};

/**
 * Convert Unified LayoutItem back to Plugin Studio GridItem
 * Simple, direct mapping preserving all properties
 */
const convertLayoutItemToGridItem = (item: LayoutItem): GridItem => {
  // Try to restore original item properties if available
  const originalItem = item.config?._originalItem as GridItem | PluginStudioLayoutItem;
  const sanitizedConfig = sanitizeConfig(item.config);
  
  if (originalItem) {
    // Preserve all original properties but update position/size
    const restored: GridItem = {
      ...(originalItem as GridItem),
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      minW: item.minW,
      minH: item.minH,
      maxW: item.maxW,
      maxH: item.maxH
    };

    if ('configOverrides' in (originalItem as any)) {
      (restored as any).configOverrides = sanitizedConfig;
    }

    return restored;
  }
  
  // Fallback - create from unified item
  const fallback: GridItem = {
    i: item.i,
    x: item.x,
    y: item.y,
    w: item.w,
    h: item.h,
    minW: item.minW,
    minH: item.minH,
    maxW: item.maxW,
    maxH: item.maxH,
    pluginId: item.pluginId,
    args: sanitizedConfig as Record<string, any>
  };

  // Preserve config overrides for layout-aware consumers
  (fallback as any).configOverrides = sanitizedConfig as Record<string, any>;

  return fallback;
};

/**
 * Validate that a conversion maintains data integrity
 */
export const validateConversion = (
  original: Page,
  converted: PageData,
  reconverted: Layouts
): boolean => {
  try {
    // Check basic properties
    if (original.id !== converted.id || original.name !== converted.name) {
      console.warn('[DataConverters] Basic properties mismatch');
      return false;
    }
    
    // Check layout item counts
    const originalDesktopCount = original.layouts?.desktop?.length || 0;
    const reconvertedDesktopCount = reconverted.desktop?.length || 0;
    
    if (originalDesktopCount !== reconvertedDesktopCount) {
      console.warn('[DataConverters] Layout item count mismatch');
      return false;
    }
    
    return true;
  } catch (error) {
    console.error('[DataConverters] Validation error:', error);
    return false;
  }
};

/**
 * Debug helper to compare data before and after conversion
 */
export const debugConversion = (
  original: Page,
  layouts: Layouts,
  converted: PageData
): void => {
  if (import.meta.env.MODE === 'development') {
    console.group('[DataConverters] Conversion Debug');
    console.log('Original Page:', original);
    console.log('Original Layouts:', layouts);
    console.log('Converted PageData:', converted);
    console.log('Desktop Items:', {
      original: layouts.desktop?.length || 0,
      converted: converted.layouts.desktop?.length || 0
    });
    console.groupEnd();
  }
};
