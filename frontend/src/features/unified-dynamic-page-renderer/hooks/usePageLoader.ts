import { useState, useEffect } from 'react';
import { PageData, RenderMode, ResponsiveLayouts, ModuleConfig } from '../types';
import { pageService } from '../../../services/pageService';
import { defaultPageService } from '../../../services/defaultPageService';
import { Page } from '../../../pages';

export interface UsePageLoaderOptions {
  pageId?: string;
  route?: string;
  mode: RenderMode;
  allowUnpublished?: boolean;
}

export interface UsePageLoaderResult {
  pageData: PageData | null;
  loading: boolean;
  error: Error | null;
}

export function usePageLoader(options: UsePageLoaderOptions): UsePageLoaderResult {
  const { pageId, route, mode, allowUnpublished = false } = options;
  
  const [pageData, setPageData] = useState<PageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  // Convert legacy Page to unified PageData
  const convertLegacyPageToPageData = (legacyPage: Page): PageData => {
    // Convert layouts from legacy format to unified format
    const layouts: ResponsiveLayouts = {
      mobile: [],
      tablet: [],
      desktop: [],
      wide: [],
    };

    // Helper function to convert a layout item
    const convertLayoutItem = (item: any) => {
      const layoutKey = ('moduleUniqueId' in item) ? item.moduleUniqueId : item.i;
      const moduleDef = legacyPage.modules?.[layoutKey];
      const hasArgs = item.args && typeof item.args === 'object';
      const args = hasArgs ? item.args : {};

      let pluginId = ('pluginId' in item)
        ? item.pluginId
        : (args.pluginId || moduleDef?.pluginId);
      
      // If pluginId is still not found, try to extract it from the moduleId
      if (!pluginId || pluginId === 'unknown') {
        // For complex generated IDs like "BrainDriveChat_1830586da8834501bea1ef1d39c3cbe8_BrainDriveChat_BrainDriveChat_1754404718788"
        // Extract the plugin name (first part before underscore)
        if (layoutKey && typeof layoutKey === 'string' && layoutKey.includes('_')) {
          const extractedPluginId = layoutKey.split('_')[0];
          if (extractedPluginId && extractedPluginId !== 'unknown') {
            pluginId = extractedPluginId;
            if (process.env.NODE_ENV === 'development') {
              console.log('[usePageLoader] Extracted pluginId ' + pluginId + ' from moduleId ' + layoutKey);
            }
          }
        }
      }
      
      // Skip items without a valid pluginId to prevent infinite loops
      if (!pluginId || pluginId === 'unknown') {
        if (process.env.NODE_ENV === 'development') {
          console.warn('[usePageLoader] Skipping layout item with missing pluginId:', { item, layoutKey, pluginId });
        }
        return null;
      }

      const baseModuleId = args.moduleId || moduleDef?.moduleId || moduleDef?.moduleName || layoutKey;
      const config = {
        ...(moduleDef?.config || {}),
        ...args,
        moduleId: baseModuleId,
      };
      
      return {
        i: item.i,
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
        minW: item.minW,
        maxW: ('maxW' in item) ? item.maxW : undefined,
        minH: item.minH,
        maxH: ('maxH' in item) ? item.maxH : undefined,
        static: ('static' in item) ? item.static : false,
        isDraggable: ('isDraggable' in item) ? item.isDraggable !== false : true,
        isResizable: ('isResizable' in item) ? item.isResizable !== false : true,
        moduleId: baseModuleId,
        pluginId,
        config,
      };
    };

    // Convert layouts
    if (legacyPage.layouts?.desktop) {
      layouts.desktop = legacyPage.layouts.desktop.map(convertLayoutItem).filter(Boolean) as any[];
    }
    if (legacyPage.layouts?.tablet) {
      layouts.tablet = legacyPage.layouts.tablet.map(convertLayoutItem).filter(Boolean) as any[];
    }
    if (legacyPage.layouts?.mobile) {
      layouts.mobile = legacyPage.layouts.mobile.map(convertLayoutItem).filter(Boolean) as any[];
    }
    
    // Use desktop layout for wide breakpoint if available
    if (legacyPage.layouts?.desktop) {
      layouts.wide = legacyPage.layouts.desktop.map(convertLayoutItem).filter(Boolean) as any[];
    }

    // Convert modules to unified format
    const modules: ModuleConfig[] = [];
    if (legacyPage.modules) {
      Object.entries(legacyPage.modules).forEach(([moduleId, moduleDefinition]) => {
        let pluginId = moduleDefinition.pluginId;
        
        // If pluginId is missing, try to extract it from the moduleId
        if (!pluginId || pluginId === 'unknown') {
          // For complex generated IDs like "BrainDriveChat_1830586da8834501bea1ef1d39c3cbe8_BrainDriveChat_BrainDriveChat_1754404718788"
          // Extract the plugin name (first part before underscore)
          if (moduleId && typeof moduleId === 'string' && moduleId.includes('_')) {
            const extractedPluginId = moduleId.split('_')[0];
            if (extractedPluginId && extractedPluginId !== 'unknown') {
              pluginId = extractedPluginId;
              if (process.env.NODE_ENV === 'development') {
                console.log(`[usePageLoader] Extracted pluginId '${pluginId}' for module '${moduleId}'`);
              }
            }
          }
        }
        
        // Only create modules with valid pluginIds
        if (pluginId && pluginId !== 'unknown') {
          const moduleConfig = {
            id: moduleId,
            pluginId: pluginId,
            type: 'component',
            ...moduleDefinition.config,
            // Add legacy adapter metadata
            _legacy: {
              moduleId: moduleDefinition.moduleId,
              moduleName: moduleDefinition.moduleName,
              originalConfig: moduleDefinition.config,
            },
          };
          
          if (process.env.NODE_ENV === 'development') {
            console.log(`[usePageLoader] Created module with ID: "${moduleId}" and pluginId: "${pluginId}"`);
          }
          
          modules.push(moduleConfig);
        } else {
          if (process.env.NODE_ENV === 'development') {
            console.warn(`[usePageLoader] Skipping module with missing pluginId:`, { moduleId, pluginId });
          }
        }
      });
    }

    // Create unified PageData
    const pageData: PageData = {
      id: legacyPage.id,
      name: legacyPage.name,
      route: legacyPage.route || '',
      layouts,
      modules,
      metadata: {
        title: legacyPage.name,
        description: legacyPage.description,
        lastModified: new Date(),
      },
      isPublished: legacyPage.is_published !== false,
    };

    return pageData;
  };

  useEffect(() => {
    const loadPage = async () => {
      try {
        setLoading(true);
        setError(null);

        // Load page using existing services
        let loadedPage: Page | null = null;
        
        if (pageId) {
          loadedPage = allowUnpublished
            ? await defaultPageService.getDefaultPage(pageId)
            : await pageService.getPage(pageId);
        } else if (route) {
          loadedPage = await pageService.getPageByRoute(route);
        } else {
          throw new Error('No page ID or route provided');
        }

        if (!loadedPage) {
          throw new Error('Page not found');
        }

        if (!loadedPage.is_published && !loadedPage.is_local && !allowUnpublished) {
          throw new Error('Page is not published');
        }

        // Process the page data
        let processedPage = { ...loadedPage };
        
        // Extract layouts and modules if needed (backward compatibility)
        if (!processedPage.layouts && processedPage.content?.layouts) {
          processedPage.layouts = processedPage.content.layouts;
        }
        
        if (!processedPage.modules && processedPage.content?.modules) {
          processedPage.modules = processedPage.content.modules;
        }
        
        // Ensure layouts exist
        if (!processedPage.layouts) {
          processedPage.layouts = { desktop: [], tablet: [], mobile: [] };
        }

        // Generate default layouts if we have modules but empty layouts
        if (processedPage.modules && Object.keys(processedPage.modules).length > 0) {
          const hasAnyLayoutItems = Object.values(processedPage.layouts).some(layout => layout && layout.length > 0);
          
          if (!hasAnyLayoutItems) {
            console.log('usePageLoader: Generating default layouts for modules without layout data');
            
            // Generate default layout items for each module
            const moduleIds = Object.keys(processedPage.modules);
            const defaultLayoutItems = moduleIds.map((moduleId, index) => {
              const moduleDefinition = processedPage.modules![moduleId];
              return {
                i: moduleId,
                x: (index % 3) * 4, // 3 columns, each 4 units wide
                y: Math.floor(index / 3) * 4, // 4 units tall per row
                w: 4,
                h: 4,
                minW: 2,
                minH: 2,
                moduleUniqueId: moduleId,
                pluginId: moduleDefinition.pluginId,
                moduleId: moduleDefinition.moduleId || moduleDefinition.moduleName,
                configOverrides: {}
              };
            });
            
            // Apply the same layout to all breakpoints
            processedPage.layouts.desktop = [...defaultLayoutItems];
            processedPage.layouts.tablet = [...defaultLayoutItems];
            processedPage.layouts.mobile = defaultLayoutItems.map(item => ({
              ...item,
              x: 0, // Stack vertically on mobile
              w: 4, // Full width on mobile
            }));
            
            // Note: wide layout will be added by convertLegacyPageToPageData from desktop layout
            
            console.log(`usePageLoader: Generated ${defaultLayoutItems.length} default layout items`);
          }
        }

        // Convert to unified PageData format
        const convertedPageData = convertLegacyPageToPageData(processedPage);
        setPageData(convertedPageData);

      } catch (err) {
        const error = err instanceof Error ? err : new Error('Failed to load page');
        setError(error);
        console.error('Page loading error:', error);
      } finally {
        setLoading(false);
      }
    };

    if (pageId || route) {
      loadPage();
    } else {
      setLoading(false);
      setError(new Error('No page ID or route provided'));
    }
  }, [pageId, route, mode, allowUnpublished]);

  return { pageData, loading, error };
}

export default usePageLoader;