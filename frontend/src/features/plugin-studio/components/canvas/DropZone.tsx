import React, { useState } from 'react';
import { Box, Snackbar, Alert } from '@mui/material';
import { usePluginStudio } from '../../hooks';
import { DragData } from '../../types';
import { VIEW_MODE_LAYOUTS } from '../../constants';

interface DropZoneProps {
  children: React.ReactNode;
  onNewItem?: (itemId: string | null) => void;
}

/**
 * Component that handles drag and drop operations from the PluginToolbar
 * @param props The component props
 * @returns The drop zone component
 */
export const DropZone: React.FC<DropZoneProps> = ({ children, onNewItem }) => {
  const { viewMode, currentPage, savePage, addItem } = usePluginStudio();
  const [isDragOver, setIsDragOver] = useState(false);
  const [warningOpen, setWarningOpen] = useState(false);
  
  /**
   * Handle drag over
   * @param e The drag event
   */
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    
    // Check if the dataTransfer contains module data
    const types = e.dataTransfer.types;
    const hasModuleData = types.includes('module') || types.includes('text/plain');
    
    if (hasModuleData) {
      setIsDragOver(true);
      console.log('Drag over with module data');
    }
  };
  
  /**
   * Handle drag leave
   */
  const handleDragLeave = () => {
    setIsDragOver(false);
  };
  
  /**
   * Handle drag enter
   * @param e The drag event
   */
  const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    // Additional logging for debugging
    console.log('Drag enter event:', e.dataTransfer.types);
  };
  
  /**
   * Handle drop
   * @param e The drop event
   */
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    
    // Check if a page exists
    if (!currentPage) {
      console.error('No page exists to add module to');
      // Show warning snackbar instead of alert
      setWarningOpen(true);
      return;
    }
    
    try {
      // Log available data types
      console.log('Drop event data types:', e.dataTransfer.types);
      
      // Try to get the module data from the drag event
      let moduleDataStr = e.dataTransfer.getData('module');
      
      // If module data is not available, try text/plain as fallback
      if (!moduleDataStr) {
        moduleDataStr = e.dataTransfer.getData('text/plain');
        console.log('Using text/plain data:', moduleDataStr);
      }
      
      if (!moduleDataStr) {
        console.error('No module data found in drop event');
        return;
      }
      
      // Parse the module data
      const moduleData = JSON.parse(moduleDataStr) as DragData;
      console.log('Parsed module data:', moduleData);
      
      // Calculate the drop position
      const rect = e.currentTarget.getBoundingClientRect();
      const x = Math.floor((e.clientX - rect.left) / 100); // Approximate grid position
      const y = Math.floor((e.clientY - rect.top) / 100); // Approximate grid position
      
      // Get default size from module layout or use default from view mode config
      const moduleLayout = moduleData.layout;
      const viewModeConfig = VIEW_MODE_LAYOUTS[viewMode.type];
      
      // Create a unique ID for the module
      const uniqueId = `${moduleData.pluginId}_${moduleData.moduleId}_${Date.now()}`;
      
      // Calculate width and height based on available properties
      let width = moduleLayout?.defaultWidth || moduleLayout?.minWidth || viewModeConfig.defaultItemSize.w;
      let height = moduleLayout?.defaultHeight || moduleLayout?.minHeight || viewModeConfig.defaultItemSize.h;
      
      console.log('Adding item to layout:', {
        uniqueId,
        x,
        y,
        width,
        height,
        pluginId: moduleData.pluginId,
        moduleId: moduleData.moduleId
      });
      
      // Add the item to the layout
      addItem({
        i: uniqueId,
        x,
        y,
        w: width,
        h: height,
        minW: moduleLayout?.minWidth,
        minH: moduleLayout?.minHeight,
        pluginId: moduleData.pluginId,
        islocal: moduleData.isLocal,
        args: {
          moduleId: moduleData.moduleId, // Store moduleId in args for easier access
          displayName: moduleData.displayName || moduleData.moduleName // Store display name for easier access
        }
      }, viewMode.type === 'custom' ? 'desktop' : viewMode.type);
      
      // Notify parent component about the new item
      if (onNewItem) {
        onNewItem(uniqueId);
      }
      
      // Clear the new item ID after animation completes
      setTimeout(() => {
        // Notify parent component that animation is complete
        if (onNewItem) {
          onNewItem(null);
        }
      }, 1000);
      
      // Use debounced save instead of immediate save
      if (currentPage) {
        console.log('Using debounced save after dropping module');
        savePage(currentPage.id);
      }
    } catch (error) {
      console.error('Error adding module:', error);
    }
  };
  
  return (
    <>
      <Box
        onDragOver={handleDragOver}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        sx={{
          height: '100%',
          transition: 'background-color 0.2s',
          backgroundColor: isDragOver ? 'rgba(0, 0, 0, 0.05)' : 'transparent',
          borderRadius: 2,
          border: isDragOver ? '2px dashed rgba(25, 118, 210, 0.5)' : '2px dashed transparent'
        }}
      >
        {children}
      </Box>
      
      {/* Warning Snackbar */}
      <Snackbar
        open={warningOpen}
        autoHideDuration={4000}
        onClose={() => setWarningOpen(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setWarningOpen(false)}
          severity="warning"
          variant="filled"
          elevation={6}
        >
          Please create a page first before adding modules.
        </Alert>
      </Snackbar>
    </>
  );
};
