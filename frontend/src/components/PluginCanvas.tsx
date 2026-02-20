import React, { useState, useCallback, useEffect } from 'react';
import { Box, Typography, Button, CircularProgress } from '@mui/material';
import GridLayout, { Layout } from 'react-grid-layout';
import { PluginModuleRenderer } from './PluginModuleRenderer';
import { GridToolbar } from './GridToolbar';
import { ConfigDialog } from './ConfigDialog';
import { JsonViewDialog } from './JsonViewDialog';
import { PageManagementDialog } from './PageManagementDialog';
import { RouteManagementDialog } from './RouteManagementDialog';
import { GridItem, LayoutItem, ModuleDefinition, Layouts } from '../types/index';
import { pageService } from '../services/pageService';
import { plugins } from '../plugins';
import { DeviceType, ViewModeState, Page } from '../pages';
import { DragData } from '../types/index';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import ComponentErrorBoundary from './ComponentErrorBoundary'; // Import the ComponentErrorBoundary component
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import CloseIcon from '@mui/icons-material/Close';

// Breakpoints for different device types (in pixels)
const DEVICE_BREAKPOINTS = {
  mobile: 480,   // 0-480px is mobile
  tablet: 768,   // 481-768px is tablet
  desktop: 1200  // >768px is desktop
};

// We'll use direct pixel values instead of ratios

const VIEW_MODE_COLS: Record<DeviceType | 'custom', number> = {
  mobile: 4,    // 4 columns for mobile
  tablet: 8,    // 8 columns for tablet
  desktop: 12,  // 12 columns for desktop
  custom: 12    // Use desktop columns for custom mode
};

const VIEW_MODE_LAYOUTS: Record<DeviceType | 'custom', Partial<Layout> & { rowHeight: number; margin: [number, number]; padding: [number, number] }> = {
  mobile: { w: 4, h: 4, rowHeight: 50, margin: [4, 4], padding: [8, 8] },   // Compact for mobile
  tablet: { w: 4, h: 4, rowHeight: 60, margin: [8, 8], padding: [12, 12] },  // Medium spacing for tablet
  desktop: { w: 3, h: 4, rowHeight: 70, margin: [12, 12], padding: [16, 16] },  // Comfortable spacing for desktop
  custom: { w: 3, h: 4, rowHeight: 70, margin: [12, 12], padding: [16, 16] }   // Same as desktop by default
};

// Helper function to convert a LayoutItem to a GridItem for compatibility
const convertLayoutItemToGridItem = (
  layoutItem: LayoutItem, 
  moduleDefinition: ModuleDefinition
): GridItem => {
  return {
    i: layoutItem.moduleUniqueId,
    x: layoutItem.x,
    y: layoutItem.y,
    w: layoutItem.w,
    h: layoutItem.h,
    minW: layoutItem.minW,
    minH: layoutItem.minH,
    pluginId: moduleDefinition.pluginId,
    args: {
      moduleId: moduleDefinition.moduleId,
      moduleName: moduleDefinition.moduleName,
      ...moduleDefinition.config,
      ...(layoutItem.configOverrides || {})
    }
  };
};

// Helper function to convert a Layout to a GridItem for compatibility
const convertLayoutToGridItem = (layout: Layout): GridItem => {
  return {
    i: layout.i,
    x: layout.x,
    y: layout.y,
    w: layout.w,
    h: layout.h,
    pluginId: 'unknown', // This is a placeholder, should be replaced with actual data
    args: {}
  };
};

interface PluginCanvasProps {
  layouts: Layouts | null;
  onLayoutChange?: (layout: any[], layouts: Layouts) => void;
  pages: Page[];
  currentPage: Page | null;
  onPageChange: (page: Page) => void;
  onCreatePage: (pageName: string) => void;
  onDeletePage: (pageId: string) => void | Promise<void>;
  onRenamePage: (pageId: string, newName: string) => void;
}

export const PluginCanvas: React.FC<PluginCanvasProps> = ({
  layouts,
  onLayoutChange,
  pages,
  currentPage,
  onPageChange,
  onCreatePage,
  onDeletePage,
  onRenamePage
}) => {
  const [selectedItem, setSelectedItem] = useState<GridItem | null>(null);
  const [configDialogOpen, setConfigDialogOpen] = useState(false);
  const [jsonViewOpen, setJsonViewOpen] = useState(false);
  const [pageManagementOpen, setPageManagementOpen] = useState(false);
  const [routeManagementOpen, setRouteManagementOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewModeState>({ type: 'desktop' });
  const [containerWidth, setContainerWidth] = useState(0);
  const [previewMode, setPreviewMode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Determine the effective device type based on container width
  const getEffectiveDeviceType = (width: number): DeviceType => {
    if (width <= DEVICE_BREAKPOINTS.mobile) return 'mobile';
    if (width <= DEVICE_BREAKPOINTS.tablet) return 'tablet';
    return 'desktop';
  };

  // Set a default container width for initial render
  useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        const newWidth = containerRef.current.offsetWidth;
        setContainerWidth(newWidth);
      }
    };

    // Set a default width immediately for the initial render
    setContainerWidth(1200); // Default to desktop width
    
    // Then update with the actual width once the component is mounted
    updateWidth();
    
    // Force a second update after a short delay to ensure correct sizing
    const timer = setTimeout(() => {
      updateWidth();
    }, 100);
    
    // Add resize listener for subsequent changes
    window.addEventListener('resize', updateWidth);
    
    return () => {
      window.removeEventListener('resize', updateWidth);
      clearTimeout(timer);
    };
  }, []);

  // Helper function to check if an item is a GridItem
  const isGridItem = (item: any): item is GridItem => {
    return 'i' in item && 'pluginId' in item;
  };

  // Helper function to check if an item is a LayoutItem
  const isLayoutItem = (item: any): item is LayoutItem => {
    return 'moduleUniqueId' in item;
  };

  const handleConfigOpen = (item: { i: string } | { moduleUniqueId: string }) => {
    console.log('PluginCanvas - handleConfigOpen called with item:', item);
    
    // If we already have a selected item, use it directly
    if (selectedItem) {
      console.log('PluginCanvas - handleConfigOpen - using existing selectedItem:', selectedItem);
      setConfigDialogOpen(true);
      return;
    }
    
    // If no item is already selected, find it in the layout
    const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
    const currentLayout = layouts?.[deviceType] || [];
    
    let foundItem;
    
    if ('i' in item) {
      // Legacy GridItem format
      // First try to find using isGridItem check
      foundItem = currentLayout.find(gi => isGridItem(gi) && gi.i === item.i) as GridItem;
      
      // If not found, try a more lenient approach - just match by i
      if (!foundItem) {
        foundItem = currentLayout.find(gi => gi.i === item.i) as GridItem;
      }
      
      // If still not found, log and return
      if (!foundItem) {
        console.error('PluginCanvas - handleConfigOpen - item not found:', item.i);
        console.log('Current layout:', currentLayout);
        return;
      }
      
      console.log('PluginCanvas - handleConfigOpen - item found:', foundItem);
      console.log('PluginCanvas - handleConfigOpen - moduleId:', foundItem.args?.moduleId);
      setSelectedItem(foundItem);
      setConfigDialogOpen(true);
    } else if ('moduleUniqueId' in item) {
      // New LayoutItem format
      const layoutItem = currentLayout.find(li => isLayoutItem(li) && li.moduleUniqueId === item.moduleUniqueId) as LayoutItem;
      
      if (!layoutItem || !currentPage?.modules) return;
      
      const moduleDefinition = currentPage.modules[layoutItem.moduleUniqueId];
      
      if (!moduleDefinition) return;
      
      console.log('PluginCanvas - handleConfigOpen - moduleUniqueId:', layoutItem.moduleUniqueId);
      console.log('PluginCanvas - handleConfigOpen - moduleDefinition:', moduleDefinition);
      
      // For now, we'll convert the module to a GridItem format for compatibility
      const gridItem: GridItem = {
        i: layoutItem.moduleUniqueId,
        x: layoutItem.x,
        y: layoutItem.y,
        w: layoutItem.w,
        h: layoutItem.h,
        minW: layoutItem.minW,
        minH: layoutItem.minH,
        pluginId: moduleDefinition.pluginId,
        args: {
          moduleId: moduleDefinition.moduleId,
          moduleName: moduleDefinition.moduleName,
          ...moduleDefinition.config,
          ...(layoutItem.configOverrides || {})
        }
      };
      
      setSelectedItem(gridItem);
      setConfigDialogOpen(true);
    }
  };

  const handleConfigClose = () => {
    setSelectedItem(null);
    setConfigDialogOpen(false);
  };

  const handleConfigSave = (newConfig: Record<string, any>, newPluginId?: string) => {
    if (!selectedItem || !onLayoutChange || !layouts || !currentPage) return;

    // Get the current layout for the active view mode
    const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
    const currentLayout = layouts[deviceType] || [];
    
    const updatedLayout = currentLayout.map(item => {
      if (isGridItem(item) && item.i === selectedItem.i) {
        return {
          ...item,
          args: newConfig,
          pluginId: newPluginId || item.pluginId
        };
      } else if (isLayoutItem(item) && item.moduleUniqueId === selectedItem.i) {
        // For LayoutItem, update the configOverrides
        return {
          ...item,
          configOverrides: {
            ...item.configOverrides,
            ...newConfig
          }
        };
      }
      return item;
    });

    const updatedLayouts = {
      ...layouts,
      [deviceType]: updatedLayout
    };

    // Update the current page with the new layouts
    const updatedPage = {
      ...currentPage,
      layouts: updatedLayouts
    };
    
    // If we're updating a module, also update the module definition
    if (isLayoutItem(selectedItem) && currentPage.modules) {
      const moduleUniqueId = selectedItem.moduleUniqueId;
      const moduleDefinition = currentPage.modules[moduleUniqueId];
      
      if (moduleDefinition) {
        // Create a new modules object with the updated module definition
        const updatedModules = {
          ...currentPage.modules,
          [moduleUniqueId]: {
            ...moduleDefinition,
            pluginId: newPluginId || moduleDefinition.pluginId,
            config: {
              ...moduleDefinition.config,
              ...newConfig
            }
          }
        };
        
        // Update the page with the new modules
        updatedPage.modules = updatedModules;
      }
    }
    
    // Update the current page
    onPageChange(updatedPage);

    // Convert to GridItems for compatibility with onLayoutChange
    const gridItems = convertLayoutToGridItems(updatedLayout);
    onLayoutChange(gridItems, updatedLayouts);
    handleConfigClose();
  };

  const handleJsonViewOpen = () => {
    setJsonViewOpen(true);
  };

  const handleJsonViewClose = () => {
    setSelectedItem(null);
    setJsonViewOpen(false);
  };
  
  const handlePublishDialogOpen = () => {
    setPageManagementOpen(true);
  };
  
  const handlePublishDialogClose = () => {
    setPageManagementOpen(false);
  };
  
  const handleRouteManagementOpen = () => {
    setRouteManagementOpen(true);
  };
  
  const handleRouteManagementClose = () => {
    setRouteManagementOpen(false);
  };
  
  // Use a ref to track if a page creation is in progress
  const pageCreationInProgressRef = React.useRef(false);
  
  // Handle page operations
  const handleCreatePage = async (pageName: string) => {
    try {
      // Check if a page creation is already in progress
      if (pageCreationInProgressRef.current) {
        console.log('Page creation already in progress, skipping duplicate call');
        return null;
      }
      
      // Set the flag to indicate that page creation is in progress
      pageCreationInProgressRef.current = true;
      
      console.log(`Creating new page: ${pageName}`);
      
      // Forward the call to the parent component's handler
      // This ensures only one page creation request is made
      onCreatePage(pageName);
      
      // Reset the flag after a short delay to allow for future page creations
      setTimeout(() => {
        pageCreationInProgressRef.current = false;
      }, 1000);
      
      return null;
    } catch (error: any) {
      console.error('Error in handleCreatePage:', error);
      
      // Reset the flag in case of error
      pageCreationInProgressRef.current = false;
      
      // You might want to show an error message to the user here
      throw error;
    }
  };
  
  const handleRenamePage = async (pageId: string, newName: string) => {
    try {
      console.log(`Renaming page ${pageId} to ${newName}`);
      
      // Update the page name
      const updatedPage = await pageService.updatePage(pageId, {
        name: newName
      });
      
      // Call the parent handler to update the UI
      onRenamePage(pageId, newName);
      
      // Update the current page
      onPageChange(updatedPage);
    } catch (error: any) {
      console.error('Error renaming page:', error);
      // You might want to show an error message to the user here
    }
  };
  
  const handleDeletePage = async (pageId: string) => {
    try {
      console.log(`Deleting page ${pageId}`);
      
      // Delegate deletion to parent handler (which performs backend delete + state update)
      await Promise.resolve(onDeletePage(pageId));
      
      // If the current page was deleted, switch to another page
      if (currentPage && currentPage.id === pageId && pages.length > 1) {
        const newCurrentPage = pages.find(p => p.id !== pageId);
        if (newCurrentPage) {
          onPageChange(newCurrentPage);
        }
      }
    } catch (error: any) {
      console.error('Error deleting page:', error);
      // You might want to show an error message to the user here
    }
  };
  
  const handlePublishPage = async (pageId: string, publish: boolean) => {
    try {
      console.log(`Publishing page ${pageId}: ${publish}`);
      
      // Call the API to publish/unpublish the page
      const updatedPage = await pageService.publishPage(pageId, publish);
      
      // Update the current page state
      onPageChange(updatedPage);
      
      return Promise.resolve();
    } catch (error: any) {
      console.error('Error publishing page:', error);
      return Promise.reject(error);
    }
  };
  
  const handleBackupPage = async (pageId: string) => {
    try {
      console.log(`Creating backup for page ${pageId}`);
      
      // Call the API to create a backup
      const updatedPage = await pageService.backupPage(pageId);
      
      // Update the current page state
      onPageChange(updatedPage);
      
      return Promise.resolve();
    } catch (error: any) {
      console.error('Error creating backup:', error);
      return Promise.reject(error);
    }
  };
  
  const handleRestorePage = async (pageId: string) => {
    try {
      console.log(`Restoring page ${pageId} from backup`);
      
      // Call the API to restore from backup
      const updatedPage = await pageService.restorePage(pageId);
      
      // Update the current page state
      onPageChange(updatedPage);
      
      return Promise.resolve();
    } catch (error: any) {
      console.error('Error restoring page:', error);
      return Promise.reject(error);
    }
  };
  
  const handleUpdatePage = async (pageId: string, updates: Partial<Page>) => {
    try {
      console.log(`Updating page ${pageId}:`, updates);
      
      // Call the API to update the page
      const updatedPage = await pageService.updatePage(pageId, updates);
      
      // Update the current page state
      onPageChange(updatedPage);
      
      return Promise.resolve();
    } catch (error: any) {
      console.error('Error updating page:', error);
      return Promise.reject(error);
    }
  };
  
  // Save the current page to the database
  const handleSavePage = async (pageId: string) => {
    try {
      console.log(`Saving page ${pageId}`);
      
      if (!layouts || !currentPage) {
        console.error('No layouts or current page to save');
        return;
      }
      
      // Prepare the content object with the current layouts and modules
      const content = {
        layouts: layouts,
        modules: currentPage.modules || {}
      };
      
      // Check if this is a local page
      const isLocalPage = currentPage.is_local === true;
      
      if (isLocalPage) {
        console.log('This is a local page. Creating a new page instead of updating.');
        
        // Create a new page with a proper UUID
        const newPageName = currentPage.name === 'Blank Page' ? 'New Page' : currentPage.name;
        const pageSlug = newPageName.toLowerCase().replace(/\s+/g, '-');
        
        try {
          // Create a unique route by adding a timestamp
          const uniquePageSlug = `${pageSlug}-${Date.now()}`;
          
          // Create a new page with the current content
          const newPage = await pageService.createPage({
            name: newPageName,
            route: uniquePageSlug,
            description: currentPage.description || '',
            content: content
          });
          
          console.log('New page created successfully:', newPage);
          
          // Transform the page to match the frontend expected format
          const transformedPage = {
            ...newPage,
            layouts: content.layouts,
            modules: content.modules
          };
          
          // Add the new page to the pages list
          const updatedPages = [...pages, transformedPage];
          
          // Update the pages list with the new page
          // We don't call onCreatePage to avoid duplicate entries
          
          // Switch to the new page
          onPageChange(transformedPage);
          
          return Promise.resolve(transformedPage);
        } catch (createError) {
          console.error('Error creating new page:', createError);
          return Promise.reject(createError);
        }
      } else {
        // Normal case - update existing page
        // Call the API to update the page with the current layouts and modules
        const updatedPage = await pageService.updatePage(pageId, {
          content: content
        });
        
        console.log('Page saved successfully:', updatedPage);
        
        // Transform the page to match the frontend expected format
        const transformedPage = {
          ...updatedPage,
          layouts: content.layouts,
          modules: content.modules
        };
        
        // Update the pages list with the updated page
        const updatedPages = pages.map(p => 
          p.id === pageId ? transformedPage : p
        );
        
        // Update the current page state
        onPageChange(transformedPage);
        
        return Promise.resolve(transformedPage);
      }
    } catch (error: any) {
      console.error('Error saving page:', error);
      return Promise.reject(error);
    }
  };

  const handleJsonLayoutChange = (newLayouts: any) => {
    if (!onLayoutChange || !currentPage) return;
    
    // Check if we're dealing with the new format (with id, name, description, layouts)
    if (newLayouts.layouts && newLayouts.id) {
      const currentLayout = newLayouts.layouts[viewMode.type] || [];
      // Ensure we have a valid array of GridItems
      const validLayout = Array.isArray(currentLayout) ? currentLayout : [];
      
      // Create a valid layouts object
      const validLayouts: Layouts = {
        desktop: newLayouts.layouts.desktop || [],
        tablet: newLayouts.layouts.tablet || [],
        mobile: newLayouts.layouts.mobile || []
      };
      
      // Update the modules in the current page if they exist in the new layouts
      if (newLayouts.modules) {
        // Create a new page object with the updated modules and layouts
        const updatedPage = {
          ...currentPage,
          modules: newLayouts.modules,
          layouts: validLayouts
        };
        
        // Update the current page
        onPageChange(updatedPage);
      }
      
      onLayoutChange(validLayout, validLayouts);
    } else {
      // Fallback to the old format
      const currentLayout = newLayouts[viewMode.type] || [];
      // Ensure we have a valid array of GridItems
      const validLayout = Array.isArray(currentLayout) ? currentLayout : [];
      
      // Create a valid layouts object
      const validLayouts: Layouts = {
        desktop: newLayouts.desktop || [],
        tablet: newLayouts.tablet || [],
        mobile: newLayouts.mobile || []
      };
      
      // Update the current page with the new layouts
      const updatedPage = {
        ...currentPage,
        layouts: validLayouts
      };
      
      // Update the current page
      onPageChange(updatedPage);
      
      onLayoutChange(validLayout, validLayouts);
    }
  };

  const handleRemoveItem = (id: string) => {
    if (!onLayoutChange || !layouts || !currentPage) return;

    // Get the current layout for the active view mode
    const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
    const currentLayout = layouts[deviceType] || [];
    
    // Find the item to remove
    const itemToRemove = currentLayout.find((item: GridItem | LayoutItem) => item.i === id);
    if (!itemToRemove) return;
    
    // Remove the item from all layouts
    const updatedLayouts = Object.entries(layouts).reduce<Layouts>((acc, [deviceType, layout]) => {
      if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
        acc[deviceType] = (layout || []).filter((item: GridItem | LayoutItem) => item.i !== id);
      }
      return acc;
    }, { desktop: [], tablet: [], mobile: [] });
    
    // If the item is a LayoutItem, remove the module from the modules object
    if (isLayoutItem(itemToRemove) && currentPage.modules) {
      const moduleUniqueId = itemToRemove.moduleUniqueId;
      
      // Create a new modules object without the removed module
      const updatedModules = { ...currentPage.modules };
      delete updatedModules[moduleUniqueId];
      
      // Update the page with the new modules and layouts
      const updatedPage = {
        ...currentPage,
        modules: updatedModules,
        layouts: updatedLayouts
      };
      
      // Update the current page
      onPageChange(updatedPage);
    } else {
      // For legacy GridItem, just update the layouts
      // Update the current page with the new layouts
      const updatedPage = {
        ...currentPage,
        layouts: updatedLayouts
      };
      
      // Update the current page
      onPageChange(updatedPage);
    }

    // Update the layouts
    const updatedLayout = updatedLayouts[deviceType] || [];
    const gridItems = convertLayoutToGridItems(updatedLayout);
    onLayoutChange(gridItems, updatedLayouts);
    setSelectedItem(null);
  };

  // Helper function to convert layout items to GridItems for compatibility with onLayoutChange
  const convertLayoutToGridItems = (layout: (GridItem | LayoutItem)[] | undefined): GridItem[] => {
    if (!layout || !Array.isArray(layout)) {
      return [];
    }
    
    return layout.map(item => {
      if (isGridItem(item)) {
        return item;
      } else if (isLayoutItem(item) && currentPage?.modules) {
        const moduleDefinition = currentPage.modules[item.moduleUniqueId];
        if (moduleDefinition) {
          return convertLayoutItemToGridItem(item, moduleDefinition);
        }
      }
      // Fallback for unknown item types
      return {
        i: 'unknown',
        x: 0,
        y: 0,
        w: 1,
        h: 1,
        pluginId: 'unknown',
        args: {}
      };
    });
  };

  const handleLayoutChange = (layout: Layout[]) => {
    if (!onLayoutChange || !layouts || !currentPage) return;

    // Get the current layout for the active view mode
    const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
    const currentLayout = layouts[deviceType] || [];
    
    const updatedLayout = layout.map(l => {
      // Find the existing item by ID
      const existingItem = currentLayout.find(item => {
        if (isGridItem(item)) {
          return item.i === l.i;
        } else if (isLayoutItem(item)) {
          return item.moduleUniqueId === l.i;
        }
        return false;
      });
      
      if (!existingItem) {
        console.error('Missing existing item for layout:', l);
        return convertLayoutToGridItem(l);
      }
      
      // Update the position and size
      if (isGridItem(existingItem)) {
        return {
          ...existingItem,
          x: l.x,
          y: l.y,
          w: l.w,
          h: l.h
        };
      } else if (isLayoutItem(existingItem)) {
        return {
          ...existingItem,
          x: l.x,
          y: l.y,
          w: l.w,
          h: l.h
        };
      }
      
      // Fallback
      return convertLayoutToGridItem(l);
    });

    const updatedLayouts = {
      ...layouts,
      [deviceType]: updatedLayout
    };

    // Update the current page with the new layouts
    const updatedPage = {
      ...currentPage,
      layouts: updatedLayouts
    };
    
    // Update the current page
    onPageChange(updatedPage);

    // Convert to GridItems for compatibility with onLayoutChange
    const gridItems = convertLayoutToGridItems(updatedLayout);
    onLayoutChange(gridItems, updatedLayouts);
  };

  const handleItemClick = (item: GridItem | LayoutItem) => {
    if (isGridItem(item)) {
      setSelectedItem(item);
    } else if (isLayoutItem(item) && currentPage?.modules) {
      const moduleDefinition = currentPage.modules[item.moduleUniqueId];
      if (moduleDefinition) {
        // Convert LayoutItem to GridItem for compatibility
        const gridItem = convertLayoutItemToGridItem(item, moduleDefinition);
        setSelectedItem(gridItem);
      }
    }
  };

  const handleCopyLayout = (from: DeviceType, to: DeviceType) => {
    if (!onLayoutChange || !layouts || !layouts[from] || !currentPage) return;

    const sourceLayout = layouts[from];
    const updatedLayouts = {
      ...layouts,
      [to]: sourceLayout.map(item => ({
        ...item,
        // Adjust widths based on column differences
        w: Math.min(item.w * (VIEW_MODE_COLS[to] / VIEW_MODE_COLS[from]), VIEW_MODE_COLS[to])
      }))
    };

    // Update the current page with the new layouts
    const updatedPage = {
      ...currentPage,
      layouts: updatedLayouts
    };
    
    // Update the current page
    onPageChange(updatedPage);

    // Update the current view mode to the target mode after copying
    setViewMode({ type: to });
    
    // Convert to GridItems for compatibility with onLayoutChange
    const gridItems = convertLayoutToGridItems(updatedLayouts[to]);
    onLayoutChange(gridItems, updatedLayouts);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (!onLayoutChange || !layouts || !currentPage) return;

    // Check for module data first (new format)
    const moduleData = e.dataTransfer.getData('module');
    if (moduleData) {
      try {
        const module = JSON.parse(moduleData) as DragData;
        console.log('PluginCanvas - module data:', module);
        const rect = e.currentTarget.getBoundingClientRect();
        
        // Generate a unique ID for the module
        const moduleUniqueId = `${module.pluginId}-${module.moduleId}-${Date.now()}`;
        
        // Create the module definition
        const moduleDefinition: ModuleDefinition = {
          pluginId: module.pluginId,
          moduleId: module.moduleId,
          moduleName: module.moduleName,
          config: {
            displayName: module.displayName,
            description: module.description,
            category: module.category,
            tags: module.tags,
            type: module.type,
            priority: module.priority,
            dependencies: module.dependencies
          }
        };
        
        // Add the module to the modules object
        const updatedModules = {
          ...(currentPage.modules || {}),
          [moduleUniqueId]: moduleDefinition
        };
        
        // Calculate drop position relative to the grid
        const x = Math.floor((e.clientX - rect.left) / (viewWidth / cols));
        const y = Math.floor((e.clientY - rect.top) / rowHeight);

        // Get default layout values for the current device type
        const defaultW = VIEW_MODE_LAYOUTS[effectiveDeviceType].w || 3;
        const defaultH = VIEW_MODE_LAYOUTS[effectiveDeviceType].h || 4;
        
        // Get module-specific layout settings if available
        const moduleDefaultWidth = module.layout?.defaultWidth || defaultW;
        const moduleDefaultHeight = module.layout?.defaultHeight || defaultH;
        const moduleMinWidth = module.layout?.minWidth || 1;
        const moduleMinHeight = module.layout?.minHeight || 1;

        // Create new layout item
        const newItem: LayoutItem = {
          moduleUniqueId,
          i: moduleUniqueId, // Required by react-grid-layout
          x: Math.min(x, cols - moduleDefaultWidth),
          y,
          w: moduleDefaultWidth,
          h: moduleDefaultHeight,
          minW: moduleMinWidth,
          minH: moduleMinHeight
        };

        // Create a copy of the current layouts to avoid mutating the original
        const currentLayouts = { ...layouts };
        
        // Ensure all layout arrays exist
        if (!currentLayouts.desktop) currentLayouts.desktop = [];
        if (!currentLayouts.tablet) currentLayouts.tablet = [];
        if (!currentLayouts.mobile) currentLayouts.mobile = [];
        
        // Add the layout item to all layouts
        const updatedLayouts = {
          desktop: [...currentLayouts.desktop, {
            ...newItem,
            x: viewMode.type === 'desktop' ? newItem.x : 0,
            y: viewMode.type === 'desktop' ? newItem.y : 
               currentLayouts.desktop.length > 0 ? 
               Math.max(...currentLayouts.desktop.map((item: any) => item.y + item.h)) : 0
          }],
          tablet: [...currentLayouts.tablet, {
            ...newItem,
            x: viewMode.type === 'tablet' ? newItem.x : 0,
            y: viewMode.type === 'tablet' ? newItem.y : 
               currentLayouts.tablet.length > 0 ? 
               Math.max(...currentLayouts.tablet.map((item: any) => item.y + item.h)) : 0
          }],
          mobile: [...currentLayouts.mobile, {
            ...newItem,
            x: viewMode.type === 'mobile' ? newItem.x : 0,
            y: viewMode.type === 'mobile' ? newItem.y : 
               currentLayouts.mobile.length > 0 ? 
               Math.max(...currentLayouts.mobile.map((item: any) => item.y + item.h)) : 0
          }]
        };

        console.log(`Added ${module.isLocal ? 'local' : 'remote'} module to canvas:`, 
          `${module.pluginId}/${module.moduleName}`);
        
        // Update the page with the new modules
        const updatedPage = {
          ...currentPage,
          modules: updatedModules,
          layouts: updatedLayouts
        };
        
        // Update the current page
        onPageChange(updatedPage);
        
        // Update the layouts
        const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
        const currentLayout = updatedLayouts[deviceType] || [];
        onLayoutChange(currentLayout, updatedLayouts);
        
        return;
      } catch (error) {
        console.error('Error parsing module data:', error);
      }
    }

    // Fall back to plugin data (legacy format)
    const pluginData = e.dataTransfer.getData('plugin');
    if (!pluginData) return;

    try {
      const plugin = JSON.parse(pluginData);
      const rect = e.currentTarget.getBoundingClientRect();
      
      // Generate a unique ID for the module
      const moduleUniqueId = `${plugin.id}-${Date.now()}`;
      
      // Get the module ID from the plugin
      const moduleId = plugin.modules && plugin.modules.length > 0 
        ? plugin.modules[0].id || plugin.modules[0].name 
        : 'default';
      
      // Get the module name from the plugin
      const moduleName = plugin.modules && plugin.modules.length > 0 
        ? plugin.modules[0].name 
        : 'Default';
      
      // Create the module definition
      const moduleDefinition: ModuleDefinition = {
        pluginId: plugin.id,
        moduleId,
        moduleName,
        config: {}
      };
      
      // Add the module to the modules object
      const updatedModules = {
        ...currentPage.modules,
        [moduleUniqueId]: moduleDefinition
      };
      
      // Calculate drop position relative to the grid
      const x = Math.floor((e.clientX - rect.left) / (viewWidth / cols));
      const y = Math.floor((e.clientY - rect.top) / rowHeight);

      // Get default layout values for the current device type
      const defaultW = VIEW_MODE_LAYOUTS[effectiveDeviceType].w || 3;
      const defaultH = VIEW_MODE_LAYOUTS[effectiveDeviceType].h || 4;
      
      // Create new layout item
      const newItem: LayoutItem = {
        moduleUniqueId,
        i: moduleUniqueId, // Required by react-grid-layout
        x: Math.min(x, cols - defaultW),
        y,
        w: defaultW,
        h: defaultH,
        minW: 1,
        minH: 1
      };

      // Add the layout item to all layouts
      const updatedLayouts = Object.entries(layouts).reduce<Layouts>((acc, [deviceType, layout]) => {
        // For non-active layouts, place the item at position 0,0 or another default position
        if (deviceType !== viewMode.type) {
          if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
            acc[deviceType] = [...(layout || []), {
              ...newItem,
              x: 0,
              y: layout && layout.length > 0 ? Math.max(...layout.map((item: any) => item.y + item.h)) : 0
            }];
          }
        } else {
          // For the active layout, use the drop position
          if (deviceType === 'desktop' || deviceType === 'tablet' || deviceType === 'mobile') {
            acc[deviceType] = [...(layout || []), newItem];
          }
        }
        return acc;
      }, { desktop: [], tablet: [], mobile: [] });

      console.log(`Added ${plugin.islocal ? 'local' : 'remote'} plugin to canvas:`, plugin.id);
      
      // Update the page with the new modules and layouts
      const updatedPage = {
        ...currentPage,
        modules: updatedModules,
        layouts: updatedLayouts
      };
      
      // Update the current page
      onPageChange(updatedPage);
      
      // Update the layouts
      const deviceType = viewMode.type === 'custom' ? effectiveDeviceType : viewMode.type;
      const currentLayout = updatedLayouts[deviceType] || [];
      onLayoutChange(currentLayout, updatedLayouts);
    } catch (error) {
      console.error('Error parsing plugin data:', error);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };

  // Get the effective device type and layout based on current mode and width
  const effectiveDeviceType = viewMode.type === 'custom' 
    ? getEffectiveDeviceType(containerWidth)
    : viewMode.type;

  // Initialize layouts with empty arrays if undefined or null
  const initializedLayouts = layouts ? {
    desktop: layouts.desktop || [],
    tablet: layouts.tablet || [],
    mobile: layouts.mobile || []
  } : {
    desktop: [],
    tablet: [],
    mobile: []
  };

  // Use the appropriate layout based on mode and width
  const currentLayout = viewMode.type === 'custom'
    ? initializedLayouts[effectiveDeviceType]
    : initializedLayouts[viewMode.type];

  // Calculate view width based on view mode with fixed values for each mode
  const calculateViewWidth = () => {
    // Default minimum width to ensure grid is always visible
    const MIN_WIDTH = 320;
    
    // Use fixed widths for each view mode to ensure consistency
    switch (viewMode.type) {
      case 'mobile':
        // Mobile view is exactly 480px wide
        return 480;
      
      case 'tablet':
        // Tablet view is exactly 768px wide
        return 768;
      
      case 'desktop':
        // Desktop view is 1200px or 90% of container width, whichever is smaller
        return Math.min(1200, containerWidth * 0.9);
      
      case 'custom':
      default:
        // Custom view uses the container width
        return Math.max(MIN_WIDTH, containerWidth);
    }
  };
  
  const viewWidth = calculateViewWidth();
  const { rowHeight, margin, padding } = VIEW_MODE_LAYOUTS[effectiveDeviceType];
  const cols = VIEW_MODE_COLS[effectiveDeviceType];

  // We're now fully using the new module-based layout system
  const renderGridItem = (item: GridItem | LayoutItem) => {
    // Handle legacy GridItem format for backward compatibility
    if (isGridItem(item)) {
      const { pluginId, args = {}, islocal } = item;
      
      // Debug logging for legacy grid item
      console.log(`PluginCanvas - renderGridItem - legacy item:`, {
        id: item.i,
        pluginId,
        moduleId: args.moduleId,
        moduleName: args.moduleName,
        args
      });
      
      return (
        <Box 
          key={item.i} 
          className="grid-item"
          sx={{
            width: '100%',
            height: '100%',
            overflow: 'hidden',
            borderRadius: 1,
            bgcolor: 'background.paper',
            display: 'flex',
            flexDirection: 'column',
            position: 'relative'
          }}
        >
          {!previewMode && (
            <Box
              className="drag-handle"
              sx={{
                position: 'absolute',
                top: 4,
                right: 4,
                zIndex: 10,
                cursor: 'move',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 24,
                height: 24,
                borderRadius: '50%',
                backgroundColor: 'rgba(255, 255, 255, 0.7)',
                '&:hover': {
                  backgroundColor: 'rgba(255, 255, 255, 0.9)',
                }
              }}
            >
              <DragIndicatorIcon fontSize="small" />
            </Box>
          )}
          <Box 
            sx={{ 
              flexGrow: 1, 
              overflow: 'hidden', 
              height: '100%', 
              width: '100%', 
              p: 0.5
            }}
          >
            <ComponentErrorBoundary>
              <PluginModuleRenderer 
                pluginId={pluginId} 
                moduleId={args.moduleId}
                moduleName={args.moduleName}
                moduleProps={args}
                isLocal={islocal}
              />
            </ComponentErrorBoundary>
          </Box>
        </Box>
      );
    }
    
    // Handle new LayoutItem format
    const moduleUniqueId = item.moduleUniqueId;
    const moduleDefinition = currentPage?.modules?.[moduleUniqueId];
    
    if (!moduleDefinition) {
      console.error(`Module definition not found for moduleUniqueId: ${moduleUniqueId}`);
      return null;
    }
    
    // Merge the default config with any layout-specific overrides
    const mergedConfig = {
      ...moduleDefinition.config,
      ...(item.configOverrides || {})
    };
    
    // Debug logging for module-based grid item
    console.log(`PluginCanvas - renderGridItem - module:`, {
      moduleUniqueId,
      pluginId: moduleDefinition.pluginId,
      moduleId: moduleDefinition.moduleId,
      moduleName: moduleDefinition.moduleName,
      config: mergedConfig
    });
    
    return (
      <Box 
        key={moduleUniqueId} 
        className="grid-item"
        sx={{
          width: '100%',
          height: '100%',
          overflow: 'hidden',
          borderRadius: 1,
          bgcolor: 'background.paper',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative'
        }}
      >
        {!previewMode && (
          <Box
            className="drag-handle"
            sx={{
              position: 'absolute',
              top: 4,
              right: 4,
              zIndex: 10,
              cursor: 'move',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 24,
              height: 24,
              borderRadius: '50%',
              backgroundColor: 'rgba(255, 255, 255, 0.7)',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.9)',
              }
            }}
          >
            <DragIndicatorIcon fontSize="small" />
          </Box>
        )}
        <Box 
          sx={{ 
            flexGrow: 1, 
            overflow: 'hidden', 
            height: '100%', 
            width: '100%', 
            p: 0.5
          }}
        >
          <ComponentErrorBoundary>
            <PluginModuleRenderer 
              pluginId={moduleDefinition.pluginId} 
              moduleId={moduleDefinition.moduleId}
              moduleName={moduleDefinition.moduleName}
              moduleProps={mergedConfig}
              isLocal={false} // Determine this based on the plugin
            />
          </ComponentErrorBoundary>
        </Box>
      </Box>
    );
  };

  // Show loading state if no layouts or current page
  if (!layouts || !currentPage) {
    return (
      <Box 
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          justifyContent: 'center',
          alignItems: 'center'
        }}
      >
        <CircularProgress />
        <Box sx={{ mt: 2 }}>
          {error ? (
            <Box sx={{ color: 'error.main', textAlign: 'center', p: 2 }}>
              {error}
              <Box sx={{ mt: 2 }}>
                <Button 
                  variant="contained" 
                  color="primary"
                  onClick={() => handleCreatePage('Home')}
                >
                  Create Default Page
                </Button>
              </Box>
            </Box>
          ) : (
            'Loading page data...'
          )}
        </Box>
      </Box>
    );
  }
  
  // Check if the current page is a local page that hasn't been saved to the backend
  const isLocalPage = currentPage.is_local === true;
  
  // Add a notification to the console for debugging
  if (isLocalPage) {
    console.log('Current page is a local page that has not been saved to the backend');
  }

  return (
    <Box 
      ref={containerRef}
      sx={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflowY: 'auto',
        p: 3,
        bgcolor: previewMode ? 'transparent' : 'background.default',
        transition: 'background-color 0.2s'
      }}
    >
      <GridToolbar
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        selectedItem={selectedItem ? { i: selectedItem.i } : undefined}
        onConfigOpen={handleConfigOpen}
        onJsonViewOpen={handleJsonViewOpen}
        onRemoveItem={handleRemoveItem}
        onCopyLayout={handleCopyLayout}
        previewMode={previewMode}
        onPreviewModeChange={setPreviewMode}
        pages={pages}
        currentPage={currentPage}
        onPageChange={onPageChange}
        onCreatePage={handleCreatePage}
        onDeletePage={handleDeletePage}
        onRenamePage={handleRenamePage}
        onSavePage={handleSavePage}
        onPublishDialogOpen={handlePublishDialogOpen}
        onRouteManagementOpen={handleRouteManagementOpen}
        isPagePublished={currentPage?.is_published}
      />
      
      <Box
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        sx={{
          width: viewWidth,
          mx: 'auto',
          position: 'relative',
          minHeight: `calc(100vh - 200px)`, // Make grid extend to near the bottom of the viewport
          border: '2px dashed rgba(0, 0, 0, 0.1)', // Always show grid outline
          borderRadius: 1,
          flexGrow: 1, // Allow the grid to grow and fill available space
          display: 'flex',
          flexDirection: 'column',
          '&:empty': {
            padding: 2
          }
        }}
      >
        <GridLayout
          width={viewWidth}
          cols={cols}
          rowHeight={rowHeight}
          margin={margin as [number, number]}
          containerPadding={padding as [number, number]}
          layout={currentLayout}
          onLayoutChange={handleLayoutChange}
          isDraggable={!previewMode}
          isResizable={!previewMode}
          draggableHandle=".drag-handle"
          style={{ minHeight: '100%', flexGrow: 1 }}
          autoSize={true} // Automatically adjust size to fit content
        >
          {currentLayout.map((item) => (
            <Box
              key={item.i}
              onClick={() => !previewMode && handleItemClick(item)}
              sx={{
                bgcolor: 'background.paper',
                borderRadius: 1,
                boxShadow: !previewMode ? 1 : 0,
                border: item.i === selectedItem?.i ? 1 : 0,
                borderColor: 'primary.main',
                transition: 'all 0.2s',
                overflow: 'hidden',
                '&:hover': !previewMode ? {
                  boxShadow: 2,
                } : undefined
              }}
            >
              {renderGridItem(item)}
            </Box>
          ))}
        </GridLayout>
      </Box>

      {selectedItem && (
        <ConfigDialog
          open={configDialogOpen}
          item={selectedItem}
          onClose={handleConfigClose}
          onSave={handleConfigSave}
          currentDeviceType={effectiveDeviceType}
          // Pass the module-specific props if the selected item is a LayoutItem
          moduleUniqueId={isLayoutItem(selectedItem) ? selectedItem.moduleUniqueId : undefined}
          moduleDefinition={isLayoutItem(selectedItem) && currentPage?.modules ? 
            currentPage.modules[selectedItem.moduleUniqueId] : undefined}
          layoutItem={isLayoutItem(selectedItem) ? 
            currentLayout.find(item => isLayoutItem(item) && item.moduleUniqueId === selectedItem.i) as LayoutItem : 
            undefined}
        />
      )}
      <JsonViewDialog
        open={jsonViewOpen}
        item={selectedItem || undefined}
        layouts={{
          pageId: currentPage?.id || '',
          pageName: currentPage?.name || '',
          pageDescription: currentPage?.description || '',
          layouts: layouts,
          modules: currentPage?.modules || {}
        }}
        onClose={handleJsonViewClose}
        onLayoutChange={handleJsonLayoutChange}
      />
      
      {currentPage && (
        <PageManagementDialog
          open={pageManagementOpen}
          onClose={handlePublishDialogClose}
          page={currentPage}
          onPublish={handlePublishPage}
          onBackup={handleBackupPage}
          onRestore={handleRestorePage}
          onUpdatePage={handleUpdatePage}
        />
      )}
      
      {/* Route Management Dialog */}
      <RouteManagementDialog
        open={routeManagementOpen}
        onClose={handleRouteManagementClose}
        pages={pages}
        onUpdatePage={handleUpdatePage}
        onRefreshPages={async () => {
          // Refresh pages by calling the parent's onPageChange with the current page
          // This will trigger a re-fetch of all pages
          if (currentPage) {
            onPageChange(currentPage);
          }
        }}
      />
    </Box>
  );
};
