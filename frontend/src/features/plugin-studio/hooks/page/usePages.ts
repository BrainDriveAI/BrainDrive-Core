import { useState, useEffect, useCallback, useRef } from 'react';
import { Page, CreatePageParams, UpdatePageParams, PageHierarchyParams } from '../../types';
import { pageService } from '../../../../services/pageService';
import { normalizeObjectKeys } from '../../../../utils/caseConversion';
import { usePageState } from '../../../../contexts/PageStateContext';
import { DEFAULT_CANVAS_CONFIG } from '../../constants/canvas.constants';

/**
 * Custom hook for managing pages
 * @returns Page management functions and state
 */
export const usePages = () => {
  const [pages, setPages] = useState<Page[]>([]);
  const [currentPage, setCurrentPage] = useState<Page | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Get the clearCache function from PageStateContext
  const { clearCache } = usePageState();
  
  // Phase 1: Add debug mode flag
  const isDebugMode = import.meta.env.VITE_LAYOUT_DEBUG === 'true';
  // Remove hasPendingChanges state since we're not using it anymore
  
  /**
   * Fetch all pages from the backend
   */
  const fetchPages = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await pageService.getPages();
      
      if (response && response.pages && response.pages.length > 0) {
        // Transform pages to ensure all fields are preserved
        const transformedPages = response.pages.map(page => {
          // Create a complete copy of the page with all fields
          const transformedPage = {
            ...page,
            // Extract layouts and modules from content and place at root level
            layouts: page.content?.layouts || {
              desktop: [],
              tablet: [],
              mobile: []
            },
            modules: page.content?.modules ? (() => {
              console.log('usePages - Before normalization:', page.content?.modules);
              const normalized = normalizeObjectKeys(page.content.modules);
              console.log('usePages - After normalization:', normalized);
              return normalized;
            })() : {},
            canvas: page.content?.canvas || { ...DEFAULT_CANVAS_CONFIG }
          };
          
          // Ensure the layouts property is synchronized with content.layouts
          if (transformedPage.content && transformedPage.content.layouts) {
            transformedPage.layouts = JSON.parse(JSON.stringify(transformedPage.content.layouts));
          }
          
          // Log to verify all fields are preserved
          console.log('Transformed page:', transformedPage);
          console.log('Page layouts:', JSON.stringify(transformedPage.layouts));
          console.log('Page content.layouts:', JSON.stringify(transformedPage.content?.layouts));
          
          return transformedPage;
        });
        
        setPages(transformedPages);
        
        // If there's no current page, set the first page as current
        if (!currentPage) {
          setCurrentPage(transformedPages[0]);
        } else {
          // If there is a current page, find and update it
          const updatedCurrentPage = transformedPages.find(p => p.id === currentPage.id);
          if (updatedCurrentPage) {
            setCurrentPage(updatedCurrentPage);
          } else {
            // If the current page no longer exists, set the first page as current
            setCurrentPage(transformedPages[0]);
          }
        }
      } else {
        // Handle no pages case
        setPages([]);
        setCurrentPage(null);
      }
    } catch (error) {
      console.error('Failed to load pages:', error);
      setError('Failed to load pages');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  // Fetch pages on mount
  useEffect(() => {
    // Only fetch pages once on mount
    fetchPages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Remove fetchPages from dependency array to prevent infinite loop
  
  /**
   * Create a new page
   * @param pageName The name of the page to create
   * @returns The created page or null if creation failed
   */
  const createPage = useCallback(async (pageName: string): Promise<Page | null> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Create a unique route by adding a timestamp
      const uniquePageSlug = `${pageName.toLowerCase().replace(/\s+/g, '-')}-${Date.now()}`;
      
        const createParams: CreatePageParams = {
          name: pageName,
          route: uniquePageSlug,
          description: '',
          content: {
            layouts: {
              desktop: [],
              tablet: [],
              mobile: []
            },
            modules: {},
            canvas: { ...DEFAULT_CANVAS_CONFIG }
          }
        };
      
      // Create a new page in the backend
      const newPage = await pageService.createPage(createParams);
      
      // Transform the page to match the frontend expected format
      // Ensure all fields from the API response are preserved
      const transformedPage = {
        ...newPage,
        layouts: newPage.content?.layouts || {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: newPage.content?.modules ? normalizeObjectKeys(newPage.content.modules) : {},
        canvas: newPage.content?.canvas || { ...DEFAULT_CANVAS_CONFIG }
      };
      
      // Log to verify all fields are preserved
      console.log('Created page:', transformedPage);
      
      // Update the local state
      setPages(prev => [...prev, transformedPage]);
      setCurrentPage(transformedPage);
      
      return transformedPage;
    } catch (error) {
      console.error('Error creating page:', error);
      setError('Failed to create page');
      
      // Create a local page if backend creation fails
      const pageId = `page-${Date.now()}`;
      const timestamp = new Date().toISOString();
      
      // Create a more complete local page with all necessary fields
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
        canvas: { ...DEFAULT_CANVAS_CONFIG },
        route: pageName.toLowerCase().replace(/\s+/g, '-'),
        route_segment: pageName.toLowerCase().replace(/\s+/g, '-'),
        parent_route: '',
        parent_type: 'page',
        is_parent_page: false,
        is_published: false,
        is_local: true,
        content: {
          layouts: {
            desktop: [],
            tablet: [],
            mobile: []
          },
          modules: {},
          canvas: { ...DEFAULT_CANVAS_CONFIG }
        }
      };
      
      // Log the local page
      console.log('Created local page:', localPage);
      
      // Update the local state
      setPages(prev => [...prev, localPage]);
      setCurrentPage(localPage);
      
      return localPage;
    } finally {
      setIsLoading(false);
    }
  }, []);
  
  /**
   * Delete a page
   * @param pageId The ID of the page to delete
   */
  const deletePage = useCallback(async (pageId: string): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Delete the page from the backend
      await pageService.deletePage(pageId);
      
      // Update the local state
      setPages(prev => {
        const newPages = prev.filter(p => p.id !== pageId);
        
        // If we're deleting the current page, handle the state appropriately
        if (currentPage && pageId === currentPage.id) {
          if (newPages.length > 0) {
            // If there are other pages, switch to the first one
            setCurrentPage(newPages[0]);
          } else {
            // If there are no pages left, set currentPage to null
            setCurrentPage(null);
          }
        }
        
        return newPages;
      });
    } catch (error) {
      console.error('Error deleting page:', error);
      setError('Failed to delete page');
    } finally {
      setIsLoading(false);
    }
  }, [pages.length, currentPage]);
  
  /**
   * Rename a page
   * @param pageId The ID of the page to rename
   * @param newName The new name for the page
   */
  const renamePage = useCallback(async (pageId: string, newName: string): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
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
      setPages(prev => {
        const newPages = prev.map(p => 
          p.id === pageId ? { ...p, name: newName } : p
        );
        
        return newPages;
      });
      
      // If the current page was renamed, update it
      if (currentPage && pageId === currentPage.id) {
        setCurrentPage(transformedPage);
      }
    } catch (error) {
      console.error('Error renaming page:', error);
      setError('Failed to rename page');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  /**
   * Save a page
   * @param pageId The ID of the page to save
   * @param options Optional save options including layoutOverride
   * @returns The saved page or null if saving failed
   */
  const savePage = useCallback(async (
    pageId: string,
    options?: {
      layoutOverride?: any;
      awaitCommit?: boolean;
    }
  ): Promise<Page | null> => {
    try {
      setIsLoading(true);
      setError(null);
      
      if (!currentPage) {
        setError('No current page to save');
        return null;
      }
      
      // Check if this is a local page
      const isLocalPage = currentPage.is_local === true;
      
      if (isLocalPage) {
        // Create a new page with a proper UUID
        const newPageName = currentPage.name === 'Blank Page' ? 'New Page' : currentPage.name;
        const pageSlug = newPageName.toLowerCase().replace(/\s+/g, '-');
        
        // Create a unique route by adding a timestamp
        const uniquePageSlug = `${pageSlug}-${Date.now()}`;
        
        // Phase 5: Use layoutOverride if provided for local pages too
        const layoutsForNewPage = options?.layoutOverride || currentPage.layouts;
        
        // Create a new page with the current content
        const newPage = await pageService.createPage({
          name: newPageName,
          route: uniquePageSlug,
          description: currentPage.description || '',
          content: {
            layouts: layoutsForNewPage,
            modules: currentPage.modules,
            canvas: currentPage.canvas
          }
        });
        
        // Transform the page to match the frontend expected format
        // Phase 5: Ensure we use the exact layouts that were saved
        const transformedPage = {
          ...newPage,
          layouts: layoutsForNewPage,
          modules: currentPage.modules ? normalizeObjectKeys(currentPage.modules) : {}
        };
        
        // Phase 5: Ensure content.layouts also matches what we saved
        if (transformedPage.content) {
          transformedPage.content = {
            ...transformedPage.content,
            layouts: JSON.parse(JSON.stringify(layoutsForNewPage))
          };
        }
        
        // Update the local state
        setPages(prev => [...prev, transformedPage]);
        setCurrentPage(transformedPage);
        
        // No need to set pending changes flag
        
        // Clear the page cache to ensure fresh data is loaded next time
        console.log('Clearing page cache after saving local page');
        clearCache();
        
        return transformedPage;
      } else {
        // Normal case - update existing page
        // Phase 5: Use layoutOverride if provided (from committed snapshot), otherwise use currentPage.layouts
        const layoutsToSave = options?.layoutOverride || currentPage.layouts || {};
        
        // Create deep clones to avoid reference issues
        const content = {
          layouts: JSON.parse(JSON.stringify(layoutsToSave)),
          modules: JSON.parse(JSON.stringify(currentPage.modules || {})),
          canvas: JSON.parse(JSON.stringify(currentPage.canvas || {}))
        };
        
        // Phase 5: Enhanced logging for save serialization tracking
        if (isDebugMode) {
          const layoutStr = JSON.stringify(content.layouts);
          let hash = 0;
          for (let i = 0; i < layoutStr.length; i++) {
            const char = layoutStr.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
          }
          const layoutHash = Math.abs(hash).toString(16).padStart(8, '0');
          const source = options?.layoutOverride ? 'layoutOverride (committed snapshot)' : 'currentPage.layouts';
          console.log(`[savePage] Phase 5 - Serialize from ${source} hash:${layoutHash}`, {
            pageId,
            timestamp: Date.now(),
            awaitCommit: options?.awaitCommit !== false
          });
        }
        
        console.log('savePage - Saving content to backend:', content);
        
        // Ensure configOverrides in layout items are preserved
        if (content.layouts) {
          Object.keys(content.layouts).forEach(deviceType => {
            const layoutItems = content.layouts[deviceType];
            if (Array.isArray(layoutItems)) {
              console.log(`savePage - Processing ${layoutItems.length} layout items for ${deviceType}`);
              layoutItems.forEach(item => {
                if (item.configOverrides) {
                  console.log(`savePage - Layout item ${item.i} has configOverrides:`, item.configOverrides);
                }
              });
            }
          });
        }
        
        // Call the API to update the page with the current layouts and modules
        const updatedPage = await pageService.updatePage(pageId, {
          content
        });
        
        console.log('savePage - API response:', updatedPage);
        
        // Transform the page to match the frontend expected format
        // Phase 5: Ensure we use the exact layouts we serialized, creating a single source of truth
        const transformedPage = {
          ...updatedPage,
          // Use the exact layouts we just saved (from committed snapshot or override)
          layouts: JSON.parse(JSON.stringify(content.layouts)),
          modules: content.modules ? normalizeObjectKeys(content.modules) : {}
        };
        
        // Phase 5: Sync both currentPage.layouts and content.layouts to the same committed snapshot
        // This ensures consistency across all references to the page's layout state
        if (transformedPage.content) {
          transformedPage.content = {
            ...transformedPage.content,
            layouts: JSON.parse(JSON.stringify(content.layouts))
          };
        }
        
        if (isDebugMode) {
          console.log('[savePage] Phase 5 - Syncing page state to committed snapshot');
          console.log('  currentPage.layouts:', JSON.stringify(transformedPage.layouts));
          console.log('  content.layouts:', JSON.stringify(transformedPage.content?.layouts));
        }
        
        // Update the local state with the synchronized snapshot
        setPages(prev =>
          prev.map(p => p.id === pageId ? transformedPage : p)
        );
        
        // Update the current page to the synchronized state
        setCurrentPage(transformedPage);
        
        // No need to set pending changes flag
        
        // QUICK MITIGATION: Don't clear cache immediately after save
        // This was causing the page to reload and reset the layout state
        // clearCache() should only be called when explicitly needed
        console.log('Save complete - not clearing cache to preserve layout state');
        
        return transformedPage;
      }
    } catch (error) {
      console.error('Error saving page:', error);
      setError('Failed to save page');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [currentPage, isDebugMode]);

  // savePageImmediately function removed since we're always saving immediately now
  
  /**
   * Publish or unpublish a page
   * @param pageId The ID of the page to publish/unpublish
   * @param publish Whether to publish (true) or unpublish (false) the page
   */
  const publishPage = useCallback(async (pageId: string, publish: boolean): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Call the API to publish/unpublish the page
      const updatedPage = await pageService.publishPage(pageId, publish);
      
      // Update the local state
      setPages(prev => 
        prev.map(p => p.id === pageId ? { ...p, is_published: publish } : p)
      );
      
      // If the current page was published/unpublished, update it
      if (currentPage && pageId === currentPage.id) {
        setCurrentPage(updatedPage);
      }
    } catch (error) {
      console.error('Error publishing page:', error);
      setError(`Failed to ${publish ? 'publish' : 'unpublish'} page`);
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  /**
   * Create a backup of a page
   * @param pageId The ID of the page to backup
   */
  const backupPage = useCallback(async (pageId: string): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Call the API to create a backup
      const updatedPage = await pageService.backupPage(pageId);
      
      // Update the local state
      setPages(prev => 
        prev.map(p => p.id === pageId ? { ...p, backup_date: new Date().toISOString() } : p)
      );
      
      // If the current page was backed up, update it
      if (currentPage && pageId === currentPage.id) {
        setCurrentPage(updatedPage);
      }
    } catch (error) {
      console.error('Error creating backup:', error);
      setError('Failed to create backup');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  /**
   * Restore a page from backup
   * @param pageId The ID of the page to restore
   */
  const restorePage = useCallback(async (pageId: string): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Call the API to restore from backup
      const updatedPage = await pageService.restorePage(pageId);
      
      // Transform the updated page to match frontend format
      const transformedPage = {
        ...updatedPage,
        layouts: updatedPage.content?.layouts || {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: updatedPage.content?.modules ? normalizeObjectKeys(updatedPage.content.modules) : {}
      };
      
      // Update the local state
      setPages(prev => 
        prev.map(p => p.id === pageId ? transformedPage : p)
      );
      
      // If the current page was restored, update it
      if (currentPage && pageId === currentPage.id) {
        setCurrentPage(transformedPage);
      }
    } catch (error) {
      console.error('Error restoring page:', error);
      setError('Failed to restore page');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  /**
   * Update a page
   * @param pageId The ID of the page to update
   * @param updates The updates to apply to the page
   */
  const updatePage = useCallback(async (pageId: string, updates: Partial<Page>): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      console.log('usePages - updatePage called with:', { pageId, updates });
      console.log('usePages - currentPage before update:', currentPage);
      
      // Call the API to update the page
      const updatedPage = await pageService.updatePage(pageId, updates);
      console.log('usePages - API response updatedPage:', updatedPage);
      
      // Transform the updated page to match frontend format
      const transformedPage = {
        ...updatedPage,
        layouts: updatedPage.content?.layouts || {
          desktop: [],
          tablet: [],
          mobile: []
        },
        modules: updatedPage.content?.modules ? normalizeObjectKeys(updatedPage.content.modules) : {}
      };
      // Update the local state
      console.log('usePages - Updating pages state');
      console.log('usePages - Updates to be applied:', updates);
      
      if (updates.modules) {
        console.log('usePages - Module updates:', updates.modules);
        // Log specific module updates if available
        if (currentPage && currentPage.modules) {
          const moduleIds = Object.keys(currentPage.modules);
          for (const moduleId of moduleIds) {
            if (updates.modules[moduleId]) {
              console.log(`usePages - Updates for module ${moduleId}:`, updates.modules[moduleId]);
              console.log(`usePages - Current module ${moduleId} config:`, currentPage.modules[moduleId].config);
              console.log(`usePages - New module ${moduleId} config:`, updates.modules[moduleId].config);
              
              // Check if the module has a _lastUpdated timestamp
              if (updates.modules[moduleId]._lastUpdated) {
                console.log(`usePages - Module ${moduleId} has _lastUpdated timestamp:`, updates.modules[moduleId]._lastUpdated);
              }
            }
          }
        }
      }
      
      // Create a deep clone of the updates to avoid reference issues
      const deepClonedUpdates = JSON.parse(JSON.stringify(updates));
      console.log('usePages - Deep cloned updates:', deepClonedUpdates);
      
      // Ensure layouts are properly handled
      if (deepClonedUpdates.layouts) {
        console.log('usePages - Processing layout updates');
        // Make sure configOverrides in layout items are preserved
        Object.keys(deepClonedUpdates.layouts).forEach(deviceType => {
          const layoutItems = deepClonedUpdates.layouts[deviceType];
          if (Array.isArray(layoutItems)) {
            console.log(`usePages - Processing ${layoutItems.length} layout items for ${deviceType}`);
            layoutItems.forEach(item => {
              if (item.configOverrides) {
                console.log(`usePages - Layout item ${item.i} has configOverrides:`, item.configOverrides);
              }
            });
          }
        });
      }
      
      setPages(prev => {
        const newPages = prev.map(p => {
          if (p.id === pageId) {
            // Create a completely new object with deep cloning to ensure React detects the change
            const updatedPage = JSON.parse(JSON.stringify({
              ...p,
              ...deepClonedUpdates,
              _lastUpdated: Date.now() // Add timestamp to force reference change
            }));
            console.log('usePages - Page before update:', p);
            console.log('usePages - Page after update:', updatedPage);
            return updatedPage;
          }
          return p;
        });
        console.log('usePages - New pages state:', newPages);
        return newPages;
      });
      
      // If the current page was updated, update it
      if (currentPage && pageId === currentPage.id) {
        console.log('usePages - Updating currentPage state with transformedPage');
        console.log('usePages - transformedPage:', transformedPage);
        console.log('usePages - Current page reference before update:', Object.prototype.toString.call(currentPage));
        
        // Create a completely new reference to ensure React detects the change
        const newCurrentPage = {
          ...JSON.parse(JSON.stringify(transformedPage)), // Deep clone
          // Add a timestamp to force reference change
          _lastUpdated: Date.now()
        };
        
        console.log('usePages - New current page reference:', Object.prototype.toString.call(newCurrentPage));
        console.log('usePages - New current page modules:', newCurrentPage.modules);
        
        // Force a new reference for each module in the page
        if (newCurrentPage.modules) {
          Object.keys(newCurrentPage.modules).forEach(moduleId => {
            if (newCurrentPage.modules[moduleId]) {
              // Add a timestamp to each module to force reference change
              newCurrentPage.modules[moduleId]._moduleUpdated = Date.now();
            }
          });
        }
        
        setCurrentPage(newCurrentPage);
      }
    } catch (error) {
      console.error('Error updating page:', error);
      setError('Failed to update page');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  /**
   * Update page hierarchy
   * @param pageId The ID of the page to update
   * @param hierarchyParams The hierarchy parameters to update
   */
  const updatePageHierarchy = useCallback(async (pageId: string, hierarchyParams: PageHierarchyParams): Promise<void> => {
    try {
      setIsLoading(true);
      setError(null);
      
      // Call the API to update the page hierarchy
      await pageService.updatePageHierarchy(pageId, hierarchyParams);
      
      // Update the local state
      setPages(prev => 
        prev.map(p => p.id === pageId ? { ...p, ...hierarchyParams } : p)
      );
      
      // If the current page was updated, update it
      if (currentPage && pageId === currentPage.id) {
        setCurrentPage(prev => prev ? { ...prev, ...hierarchyParams } : null);
      }
    } catch (error) {
      console.error('Error updating page hierarchy:', error);
      setError('Failed to update page hierarchy');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);
  
  return {
    pages,
    currentPage,
    setCurrentPage,
    isLoading,
    error,
    refreshPages: fetchPages,
    createPage,
    deletePage,
    renamePage,
    savePage,
    publishPage,
    backupPage,
    restorePage,
    updatePage,
    updatePageHierarchy
  };
};
