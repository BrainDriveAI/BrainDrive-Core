import React, { useState } from 'react';
import { Box, Switch, FormControlLabel, Tooltip } from '@mui/material';
import { PluginToolbar } from './toolbar/PluginToolbar';
import { usePluginStudio } from '../hooks/usePluginStudio';
import { usePluginStudioDevMode } from '../../../hooks/usePluginStudioDevMode';
import {
  JsonViewDialog,
  ConfigDialog,
  PageManagementDialog,
  RouteManagementDialog
} from './dialogs';
import { ErrorBoundary, LoadingIndicator } from './common';
import { PLUGIN_TOOLBAR_WIDTH } from '../constants';

// Import the unified renderer and Plugin Studio adapter
import { PluginStudioAdapter } from '../../unified-dynamic-page-renderer/adapters/PluginStudioAdapter';
import { PluginCanvas } from './canvas/PluginCanvas'; // Fallback to legacy canvas

/**
 * Enhanced Plugin Studio Layout with Unified Renderer Integration
 * 
 * This component provides a migration path from the legacy Plugin Studio
 * to the unified dynamic page renderer while maintaining backward compatibility.
 */
export const PluginStudioLayoutUnified: React.FC = () => {
  const {
    isLoading,
    error,
    currentPage,
    layouts,
    handleLayoutChange,
    savePage,
    previewMode,
    selectedItem,
    setSelectedItem,
    zoom,
    canvas,
    jsonViewOpen,
    setJsonViewOpen,
    configDialogOpen,
    setConfigDialogOpen,
    pageManagementOpen,
    setPageManagementOpen,
    routeManagementOpen,
    setRouteManagementOpen,
    flushLayoutChanges // Phase 3: Get flush method from context
  } = usePluginStudio();

  // Wrapper function to match the adapter's expected signature
  // QUICK MITIGATION: Accept options parameter with layoutOverride
  const handleSave = async (pageId: string, options?: { layoutOverride?: any }): Promise<void> => {
    console.log('[PluginStudioLayoutUnified] handleSave called with pageId:', pageId, 'options:', options);
    try {
      await savePage(pageId, options);
      console.log('[PluginStudioLayoutUnified] Save completed successfully');
    } catch (error) {
      console.error('[PluginStudioLayoutUnified] Save failed:', error);
      throw error;
    }
  };
  
  // Phase 3: Use the actual flush method from the layout hook
  const handleLayoutChangeFlush = async (): Promise<void> => {
    console.log('[PluginStudioLayoutUnified] Layout change flush requested');
    if (flushLayoutChanges) {
      await flushLayoutChanges();
      console.log('[PluginStudioLayoutUnified] Layout changes flushed');
    } else {
      // Fallback: Wait a bit to allow debounced updates to complete
      console.log('[PluginStudioLayoutUnified] No flush method available, using timeout fallback');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  };

  // Migration control state
  // Default: unified if VITE_USE_UNIFIED_RENDERER=true, otherwise dev default is unified, prod default is legacy
  const [useUnifiedRenderer, setUseUnifiedRenderer] = useState(() => {
    const flag = (import.meta as any).env?.VITE_USE_UNIFIED_RENDERER;
    if (typeof flag === 'string') return flag === 'true';
    return import.meta.env.MODE === 'development';
  });
  const [unifiedError, setUnifiedError] = useState<Error | null>(null);
  
  // Get dev mode features - MUST be called before any conditional returns
  const { features: devModeFeatures } = usePluginStudioDevMode();

  // Handle unified renderer errors and fallback to legacy
  const handleUnifiedError = (error: Error) => {
    console.warn('[PluginStudioLayoutUnified] Unified renderer failed, falling back to legacy:', error);
    setUnifiedError(error);
    setUseUnifiedRenderer(false);
  };

  // Handle item selection from unified renderer
  const handleItemSelect = (itemId: string | null) => {
    setSelectedItem(itemId ? { i: itemId } : null);
  };

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

  return (
    <Box sx={{ display: 'flex', height: '100%', width: '100%' }}>
      {/* Plugin Toolbar */}
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
        
        {/* Migration Control Panel (Development Only) */}
        {import.meta.env.MODE === 'development' && devModeFeatures.rendererSwitch && (
          <Box sx={{
            p: 2,
            borderTop: 1,
            borderColor: 'divider',
            bgcolor: 'background.default'
          }}>
            <FormControlLabel
              control={
                <Switch
                  checked={useUnifiedRenderer}
                  onChange={(e) => {
                    setUseUnifiedRenderer(e.target.checked);
                    setUnifiedError(null);
                  }}
                  size="small"
                />
              }
              label={
                <Tooltip title="Toggle between unified renderer and legacy Plugin Studio">
                  <span style={{ fontSize: '0.75rem' }}>
                    Unified Renderer
                  </span>
                </Tooltip>
              }
            />
            
            {unifiedError && (
              <Box sx={{ 
                mt: 1, 
                p: 1, 
                bgcolor: 'error.light', 
                color: 'error.contrastText',
                borderRadius: 1,
                fontSize: '0.7rem'
              }}>
                Error: {unifiedError.message}
              </Box>
            )}
          </Box>
        )}
      </Box>
      
      {/* Main Content Area */}
      <Box sx={{ 
        flex: 1,
        position: 'relative',
        overflow: 'hidden',
        width: '100%',
        maxWidth: '100%',
        minWidth: 0
      }}>
        <ErrorBoundary>
          {useUnifiedRenderer && currentPage && layouts ? (
            // Use Unified Renderer
            <PluginStudioAdapter
              page={currentPage}
              layouts={layouts}
              onLayoutChange={handleLayoutChange}
              onLayoutChangeFlush={handleLayoutChangeFlush}
              onSave={handleSave}
              previewMode={previewMode}
              selectedItem={selectedItem}
              onItemSelect={handleItemSelect}
              onError={handleUnifiedError}
              setConfigDialogOpen={setConfigDialogOpen}
              setSelectedItem={setSelectedItem}
              enableUnifiedFeatures={true}
              performanceMonitoring={import.meta.env.MODE === 'development'}
              zoom={zoom}
              canvas={canvas}
            />
          ) : (
            // Fallback to Legacy Plugin Canvas
            <PluginCanvas />
          )}
        </ErrorBoundary>
        
        {/* Renderer Status Indicator (Plugin Studio Dev Mode Only) */}
        {import.meta.env.MODE === 'development' && devModeFeatures.unifiedIndicator && (
          <Box sx={{
            position: 'absolute',
            top: 8,
            left: 8,
            bgcolor: useUnifiedRenderer ? 'success.main' : 'warning.main',
            color: 'white',
            px: 1,
            py: 0.5,
            borderRadius: 1,
            fontSize: '0.75rem',
            fontWeight: 'bold',
            zIndex: 1000
          }}>
            {useUnifiedRenderer ? 'UNIFIED' : 'LEGACY'}
          </Box>
        )}
      </Box>
      
      {/* Dialogs - These remain unchanged */}
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

export default PluginStudioLayoutUnified;
