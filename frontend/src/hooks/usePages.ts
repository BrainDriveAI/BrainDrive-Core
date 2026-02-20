import { useState, useEffect } from 'react';
import { Page } from '../pages';
import { pageService } from '../services/pageService';

export interface PageHierarchy {
  root: Page[];
  [parentRoute: string]: Page[];
}

const PAGES_REFRESH_EVENT = 'braindrive:pages:refresh';

export function usePages() {
  const [pages, setPages] = useState<Page[]>([]);
  const [pageHierarchy, setPageHierarchy] = useState<PageHierarchy>({ root: [] });
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  
  // Function to force a refresh of the pages
  const refreshPages = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event(PAGES_REFRESH_EVENT));
      return;
    }

    setRefreshTrigger(prev => prev + 1);
  };

  // Sync page refresh across all usePages consumers (sidebar, dynamic routes, dialogs, etc.).
  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const handleRefresh = () => {
      setRefreshTrigger(prev => prev + 1);
    };

    window.addEventListener(PAGES_REFRESH_EVENT, handleRefresh);
    return () => {
      window.removeEventListener(PAGES_REFRESH_EVENT, handleRefresh);
    };
  }, []);
  
  useEffect(() => {
    const fetchPages = async () => {
      try {
        setIsLoading(true);
        const publishedPages = await pageService.getPublishedPages();
        setPages(publishedPages);
        
        // console.log('Fetched published pages:', publishedPages);
        
        // Organize pages into a hierarchy
        const hierarchy: PageHierarchy = { 
          root: [],
          dashboard: [],
          'plugin-studio': [],
          settings: []
        };
        
        // First pass: identify all parent routes
        publishedPages.forEach(page => {
          if (page.is_parent_page) {
            hierarchy[page.route || ''] = [];
          }
        });
        
    // Utility function to normalize UUIDs by removing hyphens
    const normalizeUuid = (id: string): string => {
      if (!id) return id;
      return id.replace(/-/g, '');
    };
    
    // Second pass: categorize pages by parent type, parent route, and navigation route
    publishedPages.forEach(page => {
      // console.log(`Categorizing page: ${page.name}, parent_type: ${page.parent_type}, parent_route: ${page.parent_route}, navigation_route_id: ${page.navigation_route_id}`);
      
      // First check if the page has a navigation_route_id
      if (page.navigation_route_id) {
        // Pages with navigation routes
        // console.log(`Page ${page.name} has navigation_route_id: ${page.navigation_route_id}`);
        hierarchy.root.push(page);
      }
      // Then check if the page has a parent_type that's a core route
      else if (page.parent_type && page.parent_type !== 'page') {
        // Pages with core route parents should go to the corresponding core route section
        if (hierarchy[page.parent_type]) {
          // console.log(`Adding ${page.name} to ${page.parent_type} section (as a sub-page of the core route)`);
          hierarchy[page.parent_type].push(page);
        } else {
          // console.log(`Warning: parent_type ${page.parent_type} not found in hierarchy`);
          // Add to root as fallback
          // console.log(`Adding ${page.name} to root level as fallback`);
          hierarchy.root.push(page);
        }
      } else if (page.parent_route) {
        // Pages with page parents
        if (hierarchy[page.parent_route]) {
          // console.log(`Adding ${page.name} as child of ${page.parent_route}`);
          hierarchy[page.parent_route].push(page);
        } else {
          // console.log(`Warning: parent_route ${page.parent_route} not found in hierarchy`);
          
          // Check if this is a route that starts with a core section but wasn't properly categorized
          if (page.route && page.route.startsWith('dashboard/')) {
            // console.log(`Route starts with dashboard/ - adding ${page.name} to dashboard section`);
            hierarchy['dashboard'].push(page);
          } else if (page.route && page.route.startsWith('plugin-studio/')) {
            // console.log(`Route starts with plugin-studio/ - adding ${page.name} to plugin-studio section`);
            hierarchy['plugin-studio'].push(page);
          } else if (page.route && page.route.startsWith('settings/')) {
            // console.log(`Route starts with settings/ - adding ${page.name} to settings section`);
            hierarchy['settings'].push(page);
          } else {
            // If we can't find a parent, add to root as a fallback
            // console.log(`Adding ${page.name} to root level as fallback`);
            hierarchy.root.push(page);
          }
        }
      } else {
        // Root level pages
        // console.log(`Adding ${page.name} to root level`);
        hierarchy.root.push(page);
      }
    });
        
        // console.log('Final hierarchy:', hierarchy);
        setPageHierarchy(hierarchy);
        setError(null);
      } catch (err) {
        console.error('Error fetching pages:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch pages');
      } finally {
        setIsLoading(false);
      }
    };
    
    fetchPages();
  }, [refreshTrigger]); // Re-fetch when refreshTrigger changes
  
  /**
   * Get a page by its ID
   */
  const getPageById = (pageId: string): Page | undefined => {
    return pages.find(page => page.id === pageId);
  };
  
  /**
   * Get a page by its route
   */
  const getPageByRoute = (route: string): Page | undefined => {
    return pages.find(page => page.route === route);
  };
  
  /**
   * Get child pages for a parent route
   */
  const getChildPages = (parentRoute: string): Page[] => {
    return pageHierarchy[parentRoute] || [];
  };
  
  /**
   * Check if a page has children
   */
  const hasChildren = (pageId: string): boolean => {
    const page = getPageById(pageId);
    return page ? !!(page.is_parent_page && pageHierarchy[page.route || '']?.length) : false;
  };
  
  /**
   * Get the full path for a page, including parent routes
   */
  const getFullPath = (pageId: string): string => {
    const page = getPageById(pageId);
    if (!page) return '';
    
    return page.route || '';
  };
  
  return { 
    pages, 
    pageHierarchy, 
    isLoading, 
    error,
    getPageById,
    getPageByRoute,
    getChildPages,
    hasChildren,
    getFullPath,
    refreshPages
  };
}
