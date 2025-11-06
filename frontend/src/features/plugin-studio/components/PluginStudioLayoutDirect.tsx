import React, { useCallback, useMemo } from 'react';
import { Box } from '@mui/material';
import { UnifiedPageRenderer } from '../../unified-dynamic-page-renderer/components/UnifiedPageRenderer';
import { RenderMode, PageData, ResponsiveLayouts } from '../../unified-dynamic-page-renderer/types';
import { PluginToolbar } from './toolbar/PluginToolbar';
import { GridToolbar } from './grid-toolbar/GridToolbar';
import { usePluginStudio } from '../hooks/usePluginStudio';
import { convertPluginStudioToUnified, convertUnifiedToPluginStudio, debugConversion } from '../utils/dataConverters';
import { PLUGIN_TOOLBAR_WIDTH } from '../constants';
import {
  JsonViewDialog,
  ConfigDialog,
  PageManagementDialog,
  RouteManagementDialog
} from './dialogs';
import { ErrorBoundary, LoadingIndicator } from './common';
import { DebugInfo } from './DebugInfo';

/**
 * Plugin Studio Layout - PURE UNIFIED ARCHITECTURE
 *
 * This component completely eliminates the PluginStudioAdapter layer:
 *
 * OLD: Plugin Studio UI → PluginStudioAdapter (822 lines) → UnifiedPageRenderer → Backend
 * NEW: Plugin Studio UI → UnifiedPageRenderer → Backend
 *
 * Architecture Benefits:
 * - ZERO adapter layers - direct UnifiedPageRenderer usage
 * - Simple data format conversion (not complex adapter logic)
 * - All Plugin Studio UI preserved (toolbar, grid, controls)
 * - Pure unified data flow throughout
 * - Fixes save issues by eliminating conversion bugs
 */
export const PluginStudioLayoutDirect: React.FC = () => {
  const {
    // Page state
    currentPage,
    setCurrentPage,
    layouts,
    handleLayoutChange,
    savePage,
    
    // UI state
    selectedItem,
    setSelectedItem,
    previewMode,
    zoom,
    canvas,
    
    // Dialog state
    configDialogOpen,
    setConfigDialogOpen,
    jsonViewOpen,
    setJsonViewOpen,
    pageManagementOpen,
    setPageManagementOpen,
    routeManagementOpen,
    setRouteManagementOpen,
    
    // Loading state
    isLoading,
    error
  } = usePluginStudio();

  // Convert Plugin Studio data to Unified format (simple, direct mapping)
  const unifiedPageData: PageData | null = useMemo(() => {
    if (!currentPage || !layouts) {
      console.log('[PluginStudioLayoutDirect] Missing data for conversion:', {
        hasCurrentPage: !!currentPage,
        hasLayouts: !!layouts
      });
      return null;
    }

    try {
      const converted = convertPluginStudioToUnified(currentPage, layouts);
      
      console.log('[PluginStudioLayoutDirect] Successfully converted page data:', {
        pageId: converted.id,
        layoutCounts: {
          desktop: converted.layouts.desktop?.length || 0,
          tablet: converted.layouts.tablet?.length || 0,
          mobile: converted.layouts.mobile?.length || 0
        }
      });
      
      // Debug conversion in development
      if (import.meta.env.MODE === 'development') {
        debugConversion(currentPage, layouts, converted);
      }
      
      return converted;
    } catch (error) {
      console.error('[PluginStudioLayoutDirect] Conversion failed:', error);
      return null;
    }
  }, [currentPage, layouts]);

  // Handle layout changes from UnifiedPageRenderer
  const handleUnifiedLayoutChange = useCallback((unifiedLayouts: ResponsiveLayouts) => {
    try {
      console.log('[PluginStudioLayoutDirect] Layout change received:', {
        desktop: unifiedLayouts.desktop?.length || 0,
        tablet: unifiedLayouts.tablet?.length || 0,
        mobile: unifiedLayouts.mobile?.length || 0
      });
      
      // Convert back to Plugin Studio format and save immediately
      const pluginStudioLayouts = convertUnifiedToPluginStudio(unifiedLayouts);
      
      console.log('[PluginStudioLayoutDirect] Converted to Plugin Studio format:', {
        desktop: pluginStudioLayouts.desktop?.length || 0,
        tablet: pluginStudioLayouts.tablet?.length || 0,
        mobile: pluginStudioLayouts.mobile?.length || 0
      });
      
      // Call the existing Plugin Studio layout change handler
      // This preserves all existing save logic and state management
      handleLayoutChange(unifiedLayouts.desktop, pluginStudioLayouts);
      
      // 🔧 FIX: Update currentPage.layouts AND content.layouts to ensure saves work
      if (currentPage) {
        const updatedPage = {
          ...currentPage,
          layouts: pluginStudioLayouts,
          content: {
            ...currentPage.content,
            layouts: pluginStudioLayouts,
            modules: currentPage.content?.modules || currentPage.modules || {}
          }
        };
        setCurrentPage(updatedPage);
        console.log('[PluginStudioLayoutDirect] Updated currentPage.layouts and content.layouts for save functionality');
        console.log('[PluginStudioLayoutDirect] Updated page layouts:', updatedPage.layouts);
        console.log('[PluginStudioLayoutDirect] Updated page content.layouts:', updatedPage.content?.layouts);
      }
      
      console.log('[PluginStudioLayoutDirect] Layout change processed successfully');
    } catch (error) {
      console.error('[PluginStudioLayoutDirect] Failed to process layout changes:', error);
    }
  }, [handleLayoutChange, currentPage, setCurrentPage]);

  // Handle module selection from UnifiedPageRenderer
  const handleItemSelect = useCallback((itemId: string | null) => {
    setSelectedItem(itemId ? { i: itemId } : null);
  }, [setSelectedItem]);

  // Handle module configuration
  const handleItemConfig = useCallback((itemId: string) => {
    // Set the selected item and open config dialog
    setSelectedItem({ i: itemId });
    setConfigDialogOpen(true);
  }, [setSelectedItem, setConfigDialogOpen]);

  // Handle module removal
  const handleItemRemove = useCallback((itemId: string) => {
    // This will be handled by the existing Plugin Studio logic
    // through the context's removeItem function
    console.log('[PluginStudioLayoutDirect] Remove item:', itemId);
  }, []);

  // Handle errors from UnifiedPageRenderer
  const handleError = useCallback((error: Error) => {
    console.error('[PluginStudioLayoutDirect] Unified renderer error:', error);
  }, []);

  // Show loading indicator while loading
  if (isLoading) {
    return <LoadingIndicator message="Loading Plugin Studio..." />;
  }

  // Show error message if there's an error
  if (error) {
    return (
      <Box sx={{ p: 3, color: 'error.main' }}>
        <h2>Error loading Plugin Studio</h2>
        <p>{error}</p>
      </Box>
    );
  }

  // Show loading if conversion is in progress
  if (!unifiedPageData) {
    return <LoadingIndicator message="Preparing Plugin Studio..." />;
  }

  return (
    <Box sx={{ display: 'flex', height: '100%', width: '100%', position: 'relative' }}>
      {/* Debug info for development */}
      <DebugInfo
        system="direct"
        pageData={unifiedPageData}
        layouts={unifiedPageData?.layouts}
      />
      {/* Keep existing Plugin Studio toolbar - NO CHANGES */}
      <Box sx={{ 
        width: PLUGIN_TOOLBAR_WIDTH,
        flexShrink: 0,
        bgcolor: 'background.paper',
        borderRight: 1,
        borderColor: 'divider',
        height: '100%',
        overflow: 'auto',
      }}>
        <PluginToolbar />
      </Box>
      
      {/* Main Content Area with GridToolbar and UnifiedPageRenderer */}
      <Box sx={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden'
      }}>
        {/* Import and add the GridToolbar back */}
        <GridToolbar onSave={async (pageId: string) => {
          try {
            console.log('[PluginStudioLayoutDirect] Saving page:', pageId);
            console.log('[PluginStudioLayoutDirect] Current page layouts before save:', currentPage?.layouts);
            console.log('[PluginStudioLayoutDirect] Current page content before save:', currentPage?.content);
            
            // Force a small delay to ensure state updates are complete
            await new Promise(resolve => setTimeout(resolve, 100));
            
            await savePage(pageId);
            console.log('[PluginStudioLayoutDirect] Page saved successfully');
          } catch (error) {
            console.error('[PluginStudioLayoutDirect] Save failed:', error);
            console.error('[PluginStudioLayoutDirect] Error details:', error);
          }
        }} />
        
        {/* UnifiedPageRenderer in the remaining space */}
        <Box sx={{ flex: 1, overflow: 'auto' }}>
          <ErrorBoundary>
            {/* Direct UnifiedPageRenderer - No Adapter! */}
            <UnifiedPageRenderer
              pageData={unifiedPageData}
              mode={previewMode ? RenderMode.PREVIEW : RenderMode.STUDIO}
              responsive={true}
              studioScale={zoom}
              studioCanvasWidth={canvas?.width}
              studioCanvasHeight={canvas?.height}
              onLayoutChange={(layouts) => {
                console.log('[PluginStudioLayoutDirect] UnifiedPageRenderer onLayoutChange called with:', layouts);
                handleUnifiedLayoutChange(layouts);
              }}
              onItemSelect={(itemId) => {
                console.log('[PluginStudioLayoutDirect] UnifiedPageRenderer onItemSelect called with:', itemId);
                handleItemSelect(itemId);
              }}
              onItemConfig={(itemId) => {
                console.log('[PluginStudioLayoutDirect] UnifiedPageRenderer onItemConfig called with:', itemId);
                handleItemConfig(itemId);
              }}
              onItemRemove={(itemId) => {
                console.log('[PluginStudioLayoutDirect] UnifiedPageRenderer onItemRemove called with:', itemId);
                handleItemRemove(itemId);
              }}
              onError={(error) => {
                console.error('[PluginStudioLayoutDirect] UnifiedPageRenderer onError called with:', error);
                handleError(error);
              }}
            />
          </ErrorBoundary>
        </Box>
      </Box>
      
      {/* Keep all existing dialogs - NO CHANGES */}
      <JsonViewDialog
        open={jsonViewOpen}
        onClose={() => setJsonViewOpen(false)}
      />
      
      <ConfigDialog
        open={configDialogOpen}
        onClose={() => setConfigDialogOpen(false)}
      />
      
      <PageManagementDialog
        open={pageManagementOpen}
        onClose={() => setPageManagementOpen(false)}
      />
      
      <RouteManagementDialog
        open={routeManagementOpen}
        onClose={() => setRouteManagementOpen(false)}
      />
    </Box>
  );
};

export default PluginStudioLayoutDirect;
