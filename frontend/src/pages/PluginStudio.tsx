import React, { useState, useEffect, useCallback } from 'react';
import { Box, CircularProgress } from '@mui/material';
import { PluginCanvas } from '../components/PluginCanvas';
import { PluginToolbar } from '../components/PluginToolbar';
import { GridItem } from '../types';
import { PluginProvider } from '../contexts/PluginContext';
import { Page, Layouts } from '../pages';
import { getAvailablePlugins } from '../plugins';
import { useTheme } from '../contexts/ServiceContext';
import ComponentErrorBoundary from '../components/ComponentErrorBoundary';
import { pageService } from '../services/pageService';

const GLOBAL_PAGES_REFRESH_EVENT = 'braindrive:pages:refresh';

const PluginStudio = () => {
  const [allPages, setAllPages] = useState<Page[]>([]);
  const [currentPage, setCurrentPage] = useState<Page | null>(null);
  const [layouts, setLayouts] = useState<Layouts | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();

  const broadcastGlobalPageRefresh = useCallback(() => {
    if (typeof window === 'undefined') {
      return;
    }

    window.dispatchEvent(new Event(GLOBAL_PAGES_REFRESH_EVENT));
  }, []);
  
  // Use a ref to track if we've already created a default page
  // This ensures we only create one default page even if the component re-renders
  const defaultPageCreatedRef = React.useRef(false);
  
  // Initial fetch of pages - only run once on component mount
  useEffect(() => {
    const initializePages = async () => {
      try {
        setLoading(true);
        
        // Check if we've already created a default page
        if (defaultPageCreatedRef.current) {
          console.log('Default page already created, skipping initialization');
          return;
        }
        
        const response = await pageService.getPages();
        
        if (response && response.pages && response.pages.length > 0) {
          // Pages exist, just load them
          console.log('Found existing pages:', response.pages.length);
          const transformedPages = response.pages.map(page => ({
            ...page,
            layouts: page.content?.layouts || {
              desktop: [],
              tablet: [],
              mobile: []
            },
            modules: page.content?.modules || {}
          }));
          
          setAllPages(transformedPages);
          setCurrentPage(transformedPages[0]);
          setLayouts(transformedPages[0].layouts);
        } else if (!defaultPageCreatedRef.current) {
          // No pages exist and we haven't created a default page yet
          console.log('No pages found, creating default page');
          
          // Set the flag immediately to prevent duplicate creation
          defaultPageCreatedRef.current = true;
          
          try {
            const defaultPage = await createDefaultPage();
            console.log('Default page created:', defaultPage.id);
            
            setAllPages([defaultPage]);
            setCurrentPage(defaultPage);
            setLayouts({
              desktop: [],
              tablet: [],
              mobile: []
            });
          } catch (createError) {
            console.error('Error creating default page:', createError);
            setError('Failed to create a default page. Please try again later.');
            
            // Set empty state to prevent errors
            setAllPages([]);
            setCurrentPage(null);
            setLayouts({
              desktop: [],
              tablet: [],
              mobile: []
            });
          }
        }
      } catch (fetchError) {
        console.error('Error fetching pages:', fetchError);
        setError('Failed to load pages. Please try again later.');
        
        // Set empty state to prevent errors
        setAllPages([]);
        setCurrentPage(null);
        setLayouts({
          desktop: [],
          tablet: [],
          mobile: []
        });
      } finally {
        setLoading(false);
      }
    };
    
    initializePages();
  }, []);
  
  // Create a default page if none exist
  const createDefaultPage = async () => {
    try {
      // Create a unique route by adding a timestamp
      const uniquePageSlug = `home-${Date.now()}`;
      
      const newPage = await pageService.createPage({
        name: 'Home',
        route: uniquePageSlug,
        description: 'Default home page',
        content: {
          layouts: {
            desktop: [],
            tablet: [],
            mobile: []
          },
          modules: {}
        }
      });
      
      return {
        ...newPage,
        layouts: {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: {}
      };
    } catch (error) {
      console.error('Error creating default page:', error);
      
      // If we can't create a page on the backend, create a local one
      console.log('Creating a local default page instead');
      
      // Generate a unique ID for the page
      const pageId = `page-${Date.now()}`;
      
      // Create a local page object
      const localPage: Page = {
        id: pageId,
        name: 'Blank Page',
        description: 'Default blank page (not saved)',
        layouts: {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: {},
        route: 'blank-page',
        is_local: true // Mark this as a local page
      };
      
      return localPage;
    }
  };

  // Initialize layouts with empty arrays for all device types
  const initializeLayouts = (layouts: Layouts): Layouts => {
    return {
      desktop: layouts.desktop || [],
      tablet: layouts.tablet || [],
      mobile: layouts.mobile || []
    };
  };

  // Update layouts when page changes
  useEffect(() => {
    if (currentPage && currentPage.layouts) {
      setLayouts(initializeLayouts(currentPage.layouts));
    }
  }, [currentPage]);

  const handlePageChange = (newPage: Page) => {
    setCurrentPage(newPage);
  };

  const handleLayoutChange = (layout: any[], newLayouts: any) => {
    const initializedLayouts = initializeLayouts(newLayouts as Layouts);
    setLayouts(initializedLayouts);
  };

  const handleCreatePage = async (pageName: string) => {
    try {
      console.log(`Creating new page: ${pageName}`);
      
      // Create a unique route by adding a timestamp
      const uniquePageSlug = `${pageName.toLowerCase().replace(/\s+/g, '-')}-${Date.now()}`;
      
      // Create a new page in the backend
      const newPage = await pageService.createPage({
        name: pageName,
        route: uniquePageSlug,
        description: '',
        content: {
          layouts: {
            desktop: [],
            tablet: [],
            mobile: []
          },
          modules: {}
        }
      });
      
      // Transform the page to match the frontend expected format
      const transformedPage = {
        ...newPage,
        layouts: {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: {}
      };
      
      // Update the local state directly without refreshing from backend
      // This prevents duplicate entries
      setAllPages(prev => [...prev, transformedPage]);
      
      // Switch to the new page
      setCurrentPage(transformedPage);
      broadcastGlobalPageRefresh();
      
      return transformedPage;
    } catch (error) {
      console.error('Error creating page:', error);
      
      // If we can't create a page on the backend, create a local one
      console.log('Creating a local page instead');
      
      // Generate a unique ID for the page
      const pageId = `page-${Date.now()}`;
      
      // Create a local page object
      const localPage: Page = {
        id: pageId,
        name: pageName,
        description: 'Local page (not saved to backend)',
        layouts: {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: {},
        route: pageName.toLowerCase().replace(/\s+/g, '-'),
        is_local: true
      };
      
      // Update the local state
      setAllPages(prev => [...prev, localPage]);
      setCurrentPage(localPage);
      broadcastGlobalPageRefresh();
      
      return localPage;
    }
  };

  const handleDeletePage = async (pageId: string) => {
    try {
      // Don't allow deleting the last page
      if (allPages.length <= 1) return;
      
      // Delete the page from the backend
      await pageService.deletePage(pageId);
      
      // Update the local state
      setAllPages(prev => {
        const newPages = prev.filter(p => p.id !== pageId);
        // If we're deleting the current page, switch to another page
        if (currentPage && pageId === currentPage.id) {
          setCurrentPage(newPages[0]);
        }
        return newPages;
      });
      broadcastGlobalPageRefresh();
    } catch (error) {
      console.error('Error deleting page:', error);
    }
  };

  const handleRenamePage = async (pageId: string, newName: string) => {
    try {
      // Update the page name in the backend
      const updatedPage = await pageService.updatePage(pageId, {
        name: newName
      });
      
      // Transform the updated page to match frontend format
      const transformedPage = {
        ...updatedPage,
        layouts: updatedPage.content?.layouts || {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: updatedPage.content?.modules || {}
      };
      
      // Update the local state
      setAllPages(prev => {
        const newPages = prev.map(p => 
          p.id === pageId ? { ...p, name: newName } : p
        );
        
        // If the current page was renamed, update it
        if (currentPage && pageId === currentPage.id) {
          setCurrentPage(transformedPage);
        }
        
        return newPages;
      });
      broadcastGlobalPageRefresh();
    } catch (error) {
      console.error('Error renaming page:', error);
    }
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', width: '100%' }}>
      <PluginProvider plugins={getAvailablePlugins()}>
        <Box sx={{ 
          display: 'flex', 
          height: '100%',
          width: '100%',
          bgcolor: 'background.default',
          position: 'relative'
        }}>
          {/* Plugin Toolbar */}
          <Box sx={{ 
            width: 280,
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
            overflow: 'auto'
          }}>
            <ComponentErrorBoundary>
              <PluginCanvas
                layouts={layouts}
                onLayoutChange={handleLayoutChange}
                pages={allPages}
                currentPage={currentPage}
                onPageChange={handlePageChange}
                onCreatePage={handleCreatePage}
                onDeletePage={handleDeletePage}
                onRenamePage={handleRenamePage}
              />
            </ComponentErrorBoundary>
          </Box>
        </Box>
      </PluginProvider>
    </Box>
  );
};

export default PluginStudio;
