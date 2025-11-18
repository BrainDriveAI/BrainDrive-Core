import React, { useEffect, useState, useMemo } from 'react';
import { PluginStudioContext } from './PluginStudioContext';
import { usePages } from '../hooks/page/usePages';
import { useLayout } from '../hooks/layout/useLayout';
import { useViewMode } from '../hooks/ui/useViewMode';
import { useCanvas } from '../hooks/ui/useCanvas';
import { usePlugins } from '../hooks/plugin/usePlugins';
import { PluginProvider } from '../../../contexts/PluginContext';
import { ToolbarProvider } from './ToolbarContext';
import { DEFAULT_CANVAS_CONFIG } from '../constants/canvas.constants';

/**
 * Provider component for the PluginStudio context
 * @param children The child components
 * @returns The provider component
 */
export const PluginStudioProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Page state
  const {
    pages,
    currentPage,
    setCurrentPage,
    isLoading,
    error,
    createPage,
    deletePage,
    renamePage,
    savePage,
    publishPage,
    backupPage,
    restorePage,
    updatePage
  } = usePages();
  // Ensure currentPage has layouts and modules
  React.useEffect(() => {
    if (currentPage) {
      // console.log('Current page in PluginStudioProvider:', currentPage);
      // console.log('Current page layouts:', currentPage.layouts);
      // console.log('Current page content.layouts:', currentPage.content?.layouts);
      // console.log('Current page modules:', currentPage.modules);
      
      // Log the reference to help debug object identity issues
      // console.log('Current page reference:', Object.prototype.toString.call(currentPage));
    }
  }, [currentPage]);
  
  // Get plugins
  const { getModuleById } = usePlugins();
  
  // Layout state
  const {
    layouts,
    handleLayoutChange,
    removeItem,
    handleResizeStart,
    handleResizeStop,
    addItem,
    flush: flushLayoutChanges // Phase 3: Get flush method from useLayout
  } = useLayout(currentPage, getModuleById);
  // View mode state
  const {
    viewMode,
    setViewMode,
    previewMode,
    togglePreviewMode,
    containerWidth,
    setContainerWidth,
    viewWidth
  } = useViewMode();

  // Canvas + zoom state (initialize from current page if present)
  const {
    canvas,
    setCanvas,
    zoom,
    setZoom,
    zoomIn,
    zoomOut,
    zoomMode,
    setZoomMode,
    applyAutoZoom
  } = useCanvas(currentPage?.canvas || DEFAULT_CANVAS_CONFIG);

  // Auto-fit canvas width for desktop/custom modes when container width changes
  useEffect(() => {
    if (!canvas.width || !containerWidth) {
      return;
    }

    if (viewMode.type !== 'desktop' && viewMode.type !== 'custom') {
      return;
    }

    const nextZoom = containerWidth / canvas.width;
    applyAutoZoom(nextZoom);
  }, [canvas.width, containerWidth, viewMode.type, applyAutoZoom]);

  // Keep currentPage.canvas in sync (non-persistent until savePage)
  useEffect(() => {
    if (currentPage) {
      currentPage.canvas = { ...canvas };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvas, currentPage?.id]);
  
  // Plugin state
  const { availablePlugins } = usePlugins();
  
  // Selection state
  const [selectedItem, setSelectedItemState] = useState<{ i: string } | null>(null);
  const [lastSelectedItem, setLastSelectedItem] = useState<{ i: string } | null>(null);

  // Keep track of last non-null selection so toolbar can recover if selection is momentarily cleared
  const setSelectedItem = (item: { i: string } | null) => {
    setSelectedItemState(item);
    if (item) {
      setLastSelectedItem(item);
    }
  };
  
  // Dialog state
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [jsonViewOpen, setJsonViewOpen] = useState(false);
  const [pageManagementOpen, setPageManagementOpen] = useState(false);
  const [routeManagementOpen, setRouteManagementOpen] = useState(false);
  
  // Create the context value
  const contextValue = useMemo(() => ({
    // Page state
    pages,
    currentPage,
    setCurrentPage,
    createPage,
    deletePage,
    renamePage,
    savePage,
    publishPage,
    backupPage,
    restorePage,
    updatePage,
    
    // Layout state
    layouts,
    handleLayoutChange,
    removeItem,
    handleResizeStart,
    handleResizeStop,
    addItem,
    flushLayoutChanges, // Phase 3: Add flush method to context
    
    // Plugin state
    availablePlugins,
    
    // View mode state
    viewMode,
    setViewMode,
    previewMode,
    togglePreviewMode,
    viewWidth,
    containerWidth,
    setContainerWidth,
    
    // Canvas state
    canvas,
    setCanvas,
    zoom,
    setZoom,
    zoomIn,
    zoomOut,
    zoomMode,
    setZoomMode,
    applyAutoZoom,
    
    // Selection state
    selectedItem,
    lastSelectedItem,
    setSelectedItem,
    
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
  }), [
    // Page state
    pages, currentPage, setCurrentPage, createPage, deletePage, renamePage,
    savePage, publishPage, backupPage, restorePage, updatePage,
    
    // Layout state
    layouts, handleLayoutChange, removeItem, handleResizeStart, handleResizeStop, addItem, flushLayoutChanges,
    
    // Plugin state
    availablePlugins,
    
    // View mode state
    viewMode, setViewMode, previewMode, togglePreviewMode, viewWidth, containerWidth, setContainerWidth,
    
    // Selection state
    selectedItem, lastSelectedItem, setSelectedItem,
    
    // Dialog state
    configDialogOpen, jsonViewOpen, pageManagementOpen, routeManagementOpen,
    
    // Loading state
    isLoading, error,
    
    // Canvas state
    canvas, setCanvas, zoom, setZoom, zoomIn, zoomOut, zoomMode, setZoomMode, applyAutoZoom
  ]);
  
  return (
    <PluginStudioContext.Provider value={contextValue}>
      <PluginProvider plugins={availablePlugins}>
        <ToolbarProvider>
          {children}
        </ToolbarProvider>
      </PluginProvider>
    </PluginStudioContext.Provider>
  );
};
