import React, { useRef, useEffect, useState } from 'react';
import { Box, Snackbar, Alert } from '@mui/material';
import { GridToolbar } from '../grid-toolbar';
import { GridContainer } from './GridContainer';
import { DropZone } from './DropZone';
import { usePluginStudio } from '../../hooks';
import { LayoutCommitBadge } from '../../../unified-dynamic-page-renderer/components/LayoutCommitBadge';

/**
 * Component that renders the grid layout where plugins are placed
 * @returns The plugin canvas component
 */
export const PluginCanvas: React.FC = () => {
  const {
    layouts,
    handleLayoutChange,
    handleResizeStart,
    handleResizeStop,
    currentPage,
    savePage,
    canvas,
    zoom,
    viewMode,
    viewWidth,
    setContainerWidth
  } = usePluginStudio();
  const containerRef = useRef<HTMLDivElement>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [newItemId, setNewItemId] = useState<string | null>(null);
  
  // Handle save button click
  const handleSave = async () => {
    if (!currentPage) return;
    
    try {
      await savePage(currentPage.id);
      setSaveSuccess(true);
    } catch (error) {
      console.error('Error saving page:', error);
      setSaveError(true);
    }
  };
  
  // Update container width when it changes
  useEffect(() => {
    if (containerRef.current) {
      const resizeObserver = new ResizeObserver(entries => {
        const { width } = entries[0].contentRect;
        setContainerWidth(width);
      });
      
      resizeObserver.observe(containerRef.current);
      return () => resizeObserver.disconnect();
    }
  }, [setContainerWidth]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <GridToolbar onSave={handleSave} />
      
      <Box
        ref={containerRef}
        sx={{ p: 0, flex: 1, overflow: 'auto' }}
      >
        <DropZone onNewItem={setNewItemId}>
          <GridContainer
            layouts={layouts}
            onLayoutChange={handleLayoutChange}
            onResizeStart={handleResizeStart}
            onResizeStop={handleResizeStop}
            viewMode={viewMode}
            viewWidth={viewWidth}
            newItemId={newItemId}
            canvasWidth={canvas.width}
            canvasHeight={canvas.height}
            zoom={zoom}
          />
        </DropZone>
      </Box>
      
      {/* Success Snackbar */}
      <Snackbar
        open={saveSuccess}
        autoHideDuration={3000}
        onClose={() => setSaveSuccess(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert onClose={() => setSaveSuccess(false)} severity="success">
          Page saved successfully!
        </Alert>
      </Snackbar>
      
      {/* Error Snackbar */}
      <Snackbar
        open={saveError}
        autoHideDuration={3000}
        onClose={() => setSaveError(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert onClose={() => setSaveError(false)} severity="error">
          Failed to save page. Please try again.
        </Alert>
      </Snackbar>
      
      {/* Phase 1: Add layout commit badge for debugging - moved to bottom-left to avoid grid overlap */}
      <LayoutCommitBadge position="bottom-left" />
    </Box>
  );
};
