import React from 'react';
import { Box } from '@mui/material';
import { PluginToolbar } from './toolbar/PluginToolbar';
import { PluginCanvas } from './canvas/PluginCanvas';
import { usePluginStudio } from '../hooks/usePluginStudio';
import {
  JsonViewDialog,
  ConfigDialog,
  PageManagementDialog,
  RouteManagementDialog
} from './dialogs';
import { ErrorBoundary, LoadingIndicator } from './common';
import { PLUGIN_TOOLBAR_WIDTH } from '../constants';

/**
 * Main layout component for the Plugin Studio
 * @returns The Plugin Studio layout component
 */
export const PluginStudioLayout: React.FC = () => {
  const {
    isLoading,
    error,
    jsonViewOpen,
    setJsonViewOpen,
    configDialogOpen,
    setConfigDialogOpen,
    pageManagementOpen,
    setPageManagementOpen,
    routeManagementOpen,
    setRouteManagementOpen
  } = usePluginStudio();
  
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
      </Box>
      
      {/* Main Content Area */}
      <Box sx={{ 
        flex: 1,
        overflow: 'hidden'
      }}>
        <ErrorBoundary>
          <PluginCanvas />
        </ErrorBoundary>
      </Box>
      
      {/* Dialogs */}
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
