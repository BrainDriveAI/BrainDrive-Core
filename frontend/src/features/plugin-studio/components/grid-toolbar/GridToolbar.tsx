import React from 'react';
import { Box, Divider, Tooltip, IconButton, Chip } from '@mui/material';
import { ViewModeSelector } from './ViewModeSelector';
import { ZoomControls } from './ZoomControls';
import { PageSelector } from './PageSelector';
import { ToolbarActions } from './ToolbarActions';
import { usePluginStudio } from '../../hooks';
import './hidden-icons.css';
import AddIcon from '@mui/icons-material/Add';
import WarningIcon from '@mui/icons-material/Warning';
import SaveIcon from '@mui/icons-material/Save';
import SaveAltIcon from '@mui/icons-material/SaveAlt';
import PublishIcon from '@mui/icons-material/Publish';
import RouteIcon from '@mui/icons-material/AccountTree';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';

/**
 * Props for the GridToolbar component
 */
interface GridToolbarProps {
  /**
   * Optional callback for when a page is saved
   * @param pageId The ID of the page being saved
   */
  onSave?: (pageId: string) => Promise<void>;
}

/**
 * Main toolbar component for the grid layout
 * @param props The component props
 * @returns The grid toolbar component
 */
export const GridToolbar: React.FC<GridToolbarProps> = ({ onSave }) => {
  const {
    // Page state
    pages,
    currentPage,
    setCurrentPage,
    createPage,
    deletePage,
    renamePage,
    savePage,
    publishPage,
    
    // View mode state
    viewMode,
    setViewMode,
    previewMode,
    togglePreviewMode,
    
    // Selection state
    selectedItem,
    setSelectedItem,
    
    // Dialog state
    setConfigDialogOpen,
    setJsonViewOpen,
    setPageManagementOpen,
    setRouteManagementOpen,
    
    // Layout state
    removeItem
  } = usePluginStudio();
  
  /**
   * Handle opening the config dialog
   * @param item The selected item
   */
  const handleConfigOpen = (item: { i: string }) => {
    setSelectedItem(item);
    setConfigDialogOpen(true);
  };
  
  /**
   * Handle opening the JSON view dialog
   */
  const handleJsonViewOpen = () => {
    setJsonViewOpen(true);
  };
  
  /**
   * Handle removing an item from the layout
   * @param id The ID of the item to remove
   */
  const handleRemoveItem = (id: string) => {
    removeItem(id);
    setSelectedItem(null);
  };
  
  /**
   * Handle opening the publish dialog
   */
  const handlePublishDialogOpen = () => {
    setPageManagementOpen(true);
  };
  
  /**
   * Handle opening the route management dialog
   */
  const handleRouteManagementOpen = () => {
    setRouteManagementOpen(true);
  };
  
  /**
   * Handle saving the current page
   * @param pageId The ID of the page to save
   */
  const handleSavePage = async (pageId: string) => {
    if (onSave) {
      await onSave(pageId);
    } else {
      await savePage(pageId);
    }
  };
  
  return (
    <Box sx={{
      p: 1,
      display: 'flex',
      alignItems: 'center',
      borderBottom: 1,
      borderColor: 'divider',
      justifyContent: 'space-between',
      backgroundColor: 'background.paper'
    }}>
      {/* Left Section: Basic Actions and Page Management Controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flex: '1 0 auto' }}>
        {/* Page Selector - Always visible */}
        <PageSelector
          pages={pages}
          currentPage={currentPage}
          onPageChange={setCurrentPage}
          onCreatePage={createPage}
          onDeletePage={deletePage}
          onRenamePage={renamePage}
          onSavePage={savePage}
        />
        
        {currentPage ? (
          <>
            {/* Save Page Button */}
            <Tooltip title="Save Page">
              <IconButton
                onClick={() => handleSavePage(currentPage.id)}
                size="small"
                color="primary"
              >
                <SaveIcon />
              </IconButton>
            </Tooltip>
            
            {/* Rename Page Button */}
            <Tooltip title="Rename Page">
              <IconButton
                onClick={() => {
                  if (currentPage) {
                    // This would typically open a rename dialog
                    // For now, we'll just use the PageSelector's rename functionality
                    const pageSelector = document.querySelector('[data-rename-page]');
                    if (pageSelector) {
                      (pageSelector as HTMLElement).click();
                    }
                  }
                }}
                size="small"
                color="primary"
              >
                <EditIcon />
              </IconButton>
            </Tooltip>
            
            {/* Delete Page Button */}
            <Tooltip title="Delete Page">
              <span>
                <IconButton
                  onClick={() => {
                    if (currentPage) {
                      // This would typically open a delete dialog
                      // For now, we'll just use the PageSelector's delete functionality
                      const pageSelector = document.querySelector('[data-delete-page]');
                      if (pageSelector) {
                        (pageSelector as HTMLElement).click();
                      }
                    }
                  }}
                  size="small"
                  color="error"
                >
                  <DeleteIcon />
                </IconButton>
              </span>
            </Tooltip>
            
            {/* Page Management and Route Management */}
            {currentPage.is_local !== true && (
              <>
                <Tooltip title={currentPage.is_published ? "Manage Published Page" : "Publish Page"}>
                  <IconButton
                    onClick={handlePublishDialogOpen}
                    size="small"
                    color={currentPage.is_published ? "success" : "primary"}
                  >
                    <PublishIcon />
                  </IconButton>
                </Tooltip>
                
                <Tooltip title="Manage Routes">
                  <IconButton
                    onClick={handleRouteManagementOpen}
                    size="small"
                    color="primary"
                  >
                    <RouteIcon />
                  </IconButton>
                </Tooltip>
              </>
            )}
            
            {/* Save As Button for local pages */}
            {currentPage.is_local === true && (
              <>
                <Tooltip title="This is a temporary page that hasn't been saved to the backend. Click 'Save As' to create a permanent page.">
                  <Chip
                    icon={<WarningIcon />}
                    label="Unsaved Page"
                    color="warning"
                    size="small"
                    sx={{ ml: 1 }}
                  />
                </Tooltip>
                <Tooltip title="Save as a new page">
                  <IconButton
                    onClick={() => handleSavePage(currentPage.id)}
                    size="small"
                    color="primary"
                    sx={{ ml: 1 }}
                  >
                    <SaveAltIcon />
                  </IconButton>
                </Tooltip>
              </>
            )}
          </>
        ) : (
          <Box sx={{ ml: 1, color: 'text.secondary' }}>
            No pages available. Use the + button to create a new page.
          </Box>
        )}
      </Box>
      
      {/* Middle Section: View Mode Controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', flex: '0 1 auto', mx: 2, gap: 2 }}>
        <ViewModeSelector
          viewMode={viewMode}
          onViewModeChange={setViewMode}
        />
        <ZoomControls />
      </Box>
      
      {/* Right Section: Function Controls */}
      <Box sx={{ display: 'flex', alignItems: 'center', flex: '1 0 auto', justifyContent: 'flex-end' }}>
        <ToolbarActions
          selectedItem={selectedItem}
          previewMode={previewMode}
          isPagePublished={currentPage?.is_published}
          isLocalPage={currentPage?.is_local}
          onConfigOpen={handleConfigOpen}
          onJsonViewOpen={handleJsonViewOpen}
          onRemoveItem={handleRemoveItem}
          onPreviewModeChange={togglePreviewMode}
          onPublishDialogOpen={handlePublishDialogOpen}
          onRouteManagementOpen={handleRouteManagementOpen}
          onSavePage={handleSavePage}
          currentPageId={currentPage?.id}
        />
      </Box>
    </Box>
  );
};
