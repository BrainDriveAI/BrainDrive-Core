import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Collapse,
  Box,
  Typography,
  ListItemButton
} from '@mui/material';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import FolderIcon from '@mui/icons-material/Folder';
import PageIcon from '@mui/icons-material/Description';
import DashboardIcon from '@mui/icons-material/Dashboard';
import ExtensionIcon from '@mui/icons-material/Extension';
import SettingsIcon from '@mui/icons-material/Settings';
import CollectionsBookmarkIcon from '@mui/icons-material/CollectionsBookmark';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import { usePages, PageHierarchy } from '../hooks/usePages';
import { IconResolver } from './IconResolver';
import { navigationService } from '../services/navigationService';
import { defaultPageService } from '../services/defaultPageService';
import { NavigationRoute, NavigationRouteTree } from '../types/navigation';

// Names of pages that should be excluded from the top level
const excludedPageNames: string[] = [];

const FALLBACK_SYSTEM_ROUTES: NavigationRoute[] = [
  {
    id: "fallback-dashboard",
    name: "Dashboard",
    route: "dashboard",
    creator_id: "system",
    is_system_route: true,
    is_visible: true,
    order: 1
  },
  {
    id: "fallback-plugin-studio",
    name: "Plugin Studio",
    route: "plugin-studio",
    creator_id: "system",
    is_system_route: true,
    is_visible: true,
    order: 2
  },
  {
    id: "fallback-settings",
    name: "Settings",
    route: "settings",
    creator_id: "system",
    is_system_route: true,
    is_visible: true,
    order: 3
  },
  {
    id: "fallback-plugin-manager",
    name: "Plugin Manager",
    route: "plugin-manager",
    creator_id: "system",
    is_system_route: true,
    is_visible: true,
    order: 4
  },
  {
    id: "fallback-personas",
    name: "Personas",
    route: "personas",
    creator_id: "system",
    is_system_route: true,
    is_visible: true,
    order: 5
  }
];

interface PageNavigationProps {
  basePath?: string; // Optional base path for all page links
}

export const PageNavigation: React.FC<PageNavigationProps> = ({ basePath = '/pages' }) => {
  const { pageHierarchy, isLoading, error } = usePages();
  const location = useLocation();
  const navigate = useNavigate();

  // State for hierarchical navigation
  const [navigationTree, setNavigationTree] = useState<NavigationRouteTree[]>([]);
  const [expandedRoutes, setExpandedRoutes] = useState<Set<string>>(new Set());
  const [isLoadingTree, setIsLoadingTree] = useState(true);

  // Legacy state for backward compatibility
  const [navigationRoutes, setNavigationRoutes] = useState<NavigationRoute[]>([]);
  const [systemRoutes, setSystemRoutes] = useState<NavigationRoute[]>([]);
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    dashboard: true,
    'plugin-studio': true,
    settings: true,
    'your-pages': true,
    'your-pages-builtin': true
  });

  const toggleSection = (sectionId: string) => {
    setExpandedSections(prev => ({
      ...prev,
      [sectionId]: !prev[sectionId]
    }));
  };

  // Fetch hierarchical navigation tree
  useEffect(() => {
    const fetchNavigationTree = async () => {
      try {
        setIsLoadingTree(true);
        console.log('🎯 [PageNavigation] Starting fetchNavigationTree...');
        
        const tree = await navigationService.getNavigationTree();
        console.log('🎯 [PageNavigation] Fetched navigation tree:', tree);
        console.log('🎯 [PageNavigation] Tree length:', tree.length);
        
        // Debug: Log each route in the tree with full details
        tree.forEach((route, index) => {
          console.log(`🎯 [PageNavigation] Route ${index}:`, {
            id: route.id,
            name: route.name,
            route: route.route,
            parent_id: route.parent_id,
            children: route.children?.length || 0,
            is_collapsible: route.is_collapsible,
            is_expanded: route.is_expanded,
            display_order: route.display_order
          });
          
          // Log children if they exist
          if (route.children && route.children.length > 0) {
            route.children.forEach((child, childIndex) => {
              console.log(`🎯 [PageNavigation] Route ${index} -> Child ${childIndex}:`, {
                id: child.id,
                name: child.name,
                route: child.route,
                parent_id: child.parent_id
              });
            });
          }
        });
        
        setNavigationTree(tree);
        
        // Initialize expanded state based on is_expanded property
        const initialExpanded = new Set<string>();
        const processTreeForExpanded = (routes: NavigationRouteTree[]) => {
          routes.forEach(route => {
            if (route.is_expanded !== false) { // Default to expanded if not explicitly false
              initialExpanded.add(route.id);
            }
            if (route.children && route.children.length > 0) {
              processTreeForExpanded(route.children);
            }
          });
        };
        
        processTreeForExpanded(tree);
        
        // Also ensure the built-in "Your Pages" section is expanded by default
        initialExpanded.add('your-pages-builtin');
        
        setExpandedRoutes(initialExpanded);
        
      } catch (err: any) {
        console.error('Error fetching navigation tree:', err.message);
        
        // Fallback to legacy flat navigation
        console.log('Falling back to legacy navigation');
        console.log('Error that caused fallback:', err);
        const routes = await navigationService.getNavigationRoutes();
        console.log('Legacy routes fetched:', routes);
        const systemRoutes = routes.filter(route => route.is_system_route);
        const customRoutes = routes.filter(route => !route.is_system_route);
        
        console.log('System routes:', systemRoutes);
        console.log('Custom routes:', customRoutes);
        
        setSystemRoutes(systemRoutes);
        setNavigationRoutes(customRoutes);
      } finally {
        setIsLoadingTree(false);
      }
    };

    fetchNavigationTree();
  }, []);

  // Toggle expanded state for a route
  const toggleRouteExpanded = async (routeId: string) => {
    const newExpandedRoutes = new Set(expandedRoutes);
    const isCurrentlyExpanded = expandedRoutes.has(routeId);
    
    if (isCurrentlyExpanded) {
      newExpandedRoutes.delete(routeId);
    } else {
      newExpandedRoutes.add(routeId);
    }
    
    setExpandedRoutes(newExpandedRoutes);
    
    // Persist the expanded state to the backend
    try {
      await navigationService.toggleNavigationRouteExpanded(routeId, !isCurrentlyExpanded);
    } catch (error) {
      console.error('Failed to persist expanded state:', error);
      // Revert the local state if the backend update fails
      setExpandedRoutes(expandedRoutes);
    }
  };

  // Utility function to normalize UUIDs by removing hyphens
  const normalizeUuid = (id: string): string => {
    if (!id) return id;
    return id.replace(/-/g, '');
  };

  // Handle navigation to a route
  const handleRouteNavigation = async (route: NavigationRoute) => {
    console.log(`Navigating to route: ${route.route}`, {
      default_page_id: route.default_page_id,
      default_component_id: route.default_component_id,
      is_system_route: route.is_system_route
    });

    // If the route has a default page, navigate to that page
    if (route.default_page_id) {
      console.log(`Route has default page ID: ${route.default_page_id}, navigating to page`);
      
      // Find the page using normalized UUID comparison
      const normalizedDefaultPageId = normalizeUuid(route.default_page_id);
      console.log(`Normalized default page ID: ${normalizedDefaultPageId}`);
      
      let defaultPage = pageHierarchy.root.find(page => 
        normalizeUuid(page.id) === normalizedDefaultPageId
      );
                
      if (defaultPage) {
        console.log(`Found default page in hierarchy: ${defaultPage.name}, route: ${defaultPage.route}, published: ${defaultPage.is_published}`);
        // Navigate to the page route
        navigate(`/pages/${defaultPage.route}`);
        return;
      } else {
        console.log(`Default page not found in hierarchy, fetching from API`);
        
        // If we can't find the page in the hierarchy, fetch it directly from the API
        try {
          console.log(`Fetching page with ID: ${route.default_page_id}`);
          const normalizedId = normalizeUuid(String(route.default_page_id));
          console.log(`Using normalized ID for API fetch: ${normalizedId}`);
          
          // Use defaultPageService to handle unpublished pages
          const page = await defaultPageService.getDefaultPage(normalizedId);
          
          if (page) {
            console.log(`Found page via defaultPageService: ${page.name}, route: ${page.route}, published: ${page.is_published}`);
            
            // Always navigate to the page route, even if the page is not published
            console.log(`Navigating to page route: /pages/${page.route}, published: ${page.is_published}`);
            navigate(`/pages/${page.route}`);
            return;
          }
          
          // If all else fails, navigate to the route path
          console.log(`Could not find page via API, navigating to route path: /${route.route}`);
          navigate(`/${route.route}`);
        } catch (error) {
          console.error('Error fetching page:', error);
          // If there's an error, fall back to the route path
          navigate(`/${route.route}`);
        }
      }
    } else {
      // Navigate to the route path if it has a default component or is a system route
      navigate(`/${route.route}`);
    }
  };

  // Render hierarchical navigation tree
  const renderNavigationTree = (routes: NavigationRouteTree[], depth: number = 0): React.ReactNode => {
    return routes.map(route => {
      const isExpanded = expandedRoutes.has(route.id);
      const isActive = location.pathname === `/${route.route}`;
      const hasChildren = route.children && route.children.length > 0;
      
      // Get pages associated with this route
      const routePages = pageHierarchy.root.filter(page => 
        page.navigation_route_id === route.id
      );
      
      // For system routes, also include pages with matching parent_type
      const coreRoutePages = route.is_system_route && pageHierarchy[route.route] 
        ? pageHierarchy[route.route] 
        : [];
      
      // Combine both types of pages
      const allRoutePages = [...routePages, ...coreRoutePages];
      
      // Check if there are any non-default pages to display
      const hasNonDefaultPages = allRoutePages.filter(page => {
        if (!route.default_page_id) return true;
        
        // Direct comparison
        if (page.id === route.default_page_id) return false;
        
        // Compare without hyphens
        const pageIdNoHyphens = page.id.replace(/-/g, '');
        const defaultPageIdNoHyphens = typeof route.default_page_id === 'string'
          ? route.default_page_id.replace(/-/g, '')
          : route.default_page_id;
        
        if (pageIdNoHyphens === defaultPageIdNoHyphens) return false;
        
        return true;
      }).length > 0;

      return (
        <React.Fragment key={route.id}>
          <ListItemButton
            selected={isActive}
            onClick={() => {
              // For parent routes with children, prioritize toggle over navigation
              if (hasChildren || hasNonDefaultPages) {
                toggleRouteExpanded(route.id);
                
                // Only navigate if the route is already expanded (second click)
                // or if it doesn't have a default component (pure parent)
                if (isExpanded || !route.default_component_id) {
                  handleRouteNavigation(route);
                }
              } else {
                // No children, just navigate
                handleRouteNavigation(route);
              }
            }}
            sx={{
              borderRadius: 1,
              mx: 1,
              mb: 0.5,
              pl: 1 + depth * 2, // Indent based on depth
              '&.Mui-selected': {
                bgcolor: 'action.selected',
                color: 'primary.main',
                '&:hover': {
                  bgcolor: 'action.hover',
                }
              }
            }}
          >
            <ListItemIcon
              sx={{
                color: isActive ? 'primary.main' : 'inherit',
                minWidth: '32px'
              }}
            >
              {route.icon ? (
                <IconResolver icon={route.icon} />
              ) : route.route === 'dashboard' ? (
                <DashboardIcon />
              ) : route.route === 'plugin-studio' ? (
                <ExtensionIcon />
              ) : route.route === 'settings' ? (
                <SettingsIcon />
              ) : route.route === 'your-braindrive' ? (
                <AccountTreeIcon />
              ) : route.route === 'your-pages' ? (
                <CollectionsBookmarkIcon />
              ) : (
                <FolderIcon />
              )}
            </ListItemIcon>
            <ListItemText
              primary={route.name}
              sx={{
                '& .MuiListItemText-primary': {
                  color: isActive ? 'primary.main' : 'inherit',
                  fontWeight: isActive ? 600 : 400
                }
              }}
            />
            {/* Show expand/collapse arrows if there are children or non-default pages */}
            {(hasChildren || hasNonDefaultPages) && (isExpanded ? <ExpandLess /> : <ExpandMore />)}
          </ListItemButton>

          {/* Render child routes */}
          {hasChildren && (
            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
              {renderNavigationTree(route.children!, depth + 1)}
            </Collapse>
          )}

          {/* Render pages for this route - excluding the default page */}
          {allRoutePages.length > 0 && (
            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
              <List component="div" disablePadding sx={{ pl: 2 + depth * 2 }}>
                {allRoutePages
                  .filter(page => {
                    if (!route.default_page_id) return true;
                    
                    // Direct comparison
                    if (page.id === route.default_page_id) return false;
                    
                    // Compare without hyphens
                    const pageIdNoHyphens = page.id.replace(/-/g, '');
                    const defaultPageIdNoHyphens = typeof route.default_page_id === 'string' 
                      ? route.default_page_id.replace(/-/g, '')
                      : route.default_page_id;
                    
                    if (pageIdNoHyphens === defaultPageIdNoHyphens) return false;
                    
                    return true;
                  })
                  .sort((a, b) => (a.navigation_order || 0) - (b.navigation_order || 0))
                  .map(page => {
                    const pagePath = `${basePath}/${page.route}`;
                    const isChildActive = location.pathname === pagePath;

                    return (
                      <ListItem
                        key={page.id}
                        component={Link}
                        to={pagePath}
                        selected={isChildActive}
                        sx={{
                          color: 'text.primary',
                          '&.Mui-selected': {
                            bgcolor: 'action.selected',
                            color: 'primary.main'
                          },
                          '&:hover': {
                            bgcolor: 'action.hover'
                          }
                        }}
                      >
                        <ListItemIcon sx={{ minWidth: '32px' }}>
                          {page.icon ? (
                            <IconResolver icon={page.icon} />
                          ) : (
                            <PageIcon />
                          )}
                        </ListItemIcon>
                        <ListItemText primary={page.name} />
                      </ListItem>
                    );
                  })}
              </List>
            </Collapse>
          )}
        </React.Fragment>
      );
    });
  };

  // Render legacy flat navigation (fallback)
  const renderLegacyNavigation = () => {
    // Your Pages navigation item
    const yourPagesNavItem = {
      id: 'your-pages',
      name: 'Your Pages',
      icon: <CollectionsBookmarkIcon />,
      path: '/pages'
    };

    // Get pages without a specific route (for "Your Pages" section)
    const pagesWithoutRoute = pageHierarchy.root.filter(page => 
      page.is_published && // Only include published pages
      !page.navigation_route_id && 
      !page.parent_route && 
      (!page.parent_type || page.parent_type === 'page') &&
      !excludedPageNames.includes(page.name)
    );

    // Create a combined list of all routes (system and custom)
    const allRoutesFromApi = [...systemRoutes, ...navigationRoutes]
      .filter(route => route.is_visible !== false)
      .sort((a, b) => (a.order || 0) - (b.order || 0));

    const allRoutes = allRoutesFromApi.length > 0
      ? allRoutesFromApi
      : FALLBACK_SYSTEM_ROUTES;

    return (
      <>
        {/* Combined list of system and custom routes - sorted by order */}
        {allRoutes.map(route => {
          // Get pages associated with this route by navigation_route_id
          const routePages = pageHierarchy.root.filter(page => 
            page.navigation_route_id === route.id
          );
          
          // For system routes, also include pages with matching parent_type
          const coreRoutePages = route.is_system_route && pageHierarchy[route.route] 
            ? pageHierarchy[route.route] 
            : [];
          
          // Combine both types of pages
          const allRoutePages = [...routePages, ...coreRoutePages];

          const hasChildren = allRoutePages.length > 0;
          
          // Check if there are any non-default pages to display
          const hasNonDefaultPages = allRoutePages.filter(page => {
            if (!route.default_page_id) return true;
            
            // Direct comparison
            if (page.id === route.default_page_id) return false;
            
            // Compare without hyphens
            const pageIdNoHyphens = page.id.replace(/-/g, '');
            const defaultPageIdNoHyphens = typeof route.default_page_id === 'string'
              ? route.default_page_id.replace(/-/g, '')
              : route.default_page_id;
            
            if (pageIdNoHyphens === defaultPageIdNoHyphens) return false;
            
            return true;
          }).length > 0;
          
          const isExpanded = expandedSections[route.route] || false;
          const isActive = location.pathname === `/${route.route}`;

          return (
            <React.Fragment key={route.id}>
              <ListItemButton
                selected={isActive}
                onClick={() => {
                  // Toggle the section if it has children
                  if (hasChildren) {
                    toggleSection(route.route);
                  }
                  
                  // Navigate to the route
                  handleRouteNavigation(route);
                }}
                sx={{
                  borderRadius: 1,
                  mx: 1,
                  mb: 0.5,
                  '&.Mui-selected': {
                    bgcolor: 'action.selected',
                    color: 'primary.main',
                    '&:hover': {
                      bgcolor: 'action.hover',
                    }
                  }
                }}
              >
                <ListItemIcon
                  sx={{
                    color: isActive ? 'primary.main' : 'inherit',
                    minWidth: '32px'
                  }}
                >
                  {route.icon ? (
                    <IconResolver icon={route.icon} />
                  ) : route.route === 'dashboard' ? (
                    <DashboardIcon />
                  ) : route.route === 'plugin-studio' ? (
                    <ExtensionIcon />
                  ) : route.route === 'settings' ? (
                    <SettingsIcon />
                  ) : (
                    <FolderIcon />
                  )}
                </ListItemIcon>
                <ListItemText
                  primary={route.name}
                  sx={{
                    '& .MuiListItemText-primary': {
                      color: isActive ? 'primary.main' : 'inherit',
                      fontWeight: isActive ? 600 : 400
                    }
                  }}
                />
                {/* Only show expand/collapse arrows if there are non-default pages to display */}
                {hasNonDefaultPages && (isExpanded ? <ExpandLess /> : <ExpandMore />)}
              </ListItemButton>

              {/* Pages for this route - excluding the default page */}
              {hasChildren && (
                <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                  <List component="div" disablePadding sx={{ pl: 2 }}>
                    {allRoutePages
                      .filter(page => {
                        if (!route.default_page_id) return true;
                        
                        // Direct comparison
                        if (page.id === route.default_page_id) return false;
                        
                        // Compare without hyphens
                        const pageIdNoHyphens = page.id.replace(/-/g, '');
                        const defaultPageIdNoHyphens = typeof route.default_page_id === 'string' 
                          ? route.default_page_id.replace(/-/g, '')
                          : route.default_page_id;
                        
                        if (pageIdNoHyphens === defaultPageIdNoHyphens) return false;
                        
                        return true;
                      })
                      .sort((a, b) => (a.navigation_order || 0) - (b.navigation_order || 0))
                      .map(page => {
                        const pagePath = `${basePath}/${page.route}`;
                        const isChildActive = location.pathname === pagePath;

                        return (
                          <ListItem
                            key={page.id}
                            component={Link}
                            to={pagePath}
                            selected={isChildActive}
                            sx={{
                              color: 'text.primary',
                              '&.Mui-selected': {
                                bgcolor: 'action.selected',
                                color: 'primary.main'
                              },
                              '&:hover': {
                                bgcolor: 'action.hover'
                              }
                            }}
                          >
                            <ListItemIcon sx={{ minWidth: '32px' }}>
                              {page.icon ? (
                                <IconResolver icon={page.icon} />
                              ) : (
                                <PageIcon />
                              )}
                            </ListItemIcon>
                            <ListItemText primary={page.name} />
                          </ListItem>
                        );
                      })}
                  </List>
                </Collapse>
              )}
            </React.Fragment>
          );
        })}

        {/* Your Pages section - always at the bottom */}
        <React.Fragment key={yourPagesNavItem.id}>
          <ListItemButton
            selected={location.pathname === yourPagesNavItem.path}
            onClick={() => {
              // Always toggle the section if it has children
              const hasChildren = pagesWithoutRoute.length > 0;
              if (hasChildren) {
                toggleSection(yourPagesNavItem.id);
              }
              // Always navigate to the path regardless of whether it has children
              navigate(yourPagesNavItem.path);
            }}
            sx={{
              borderRadius: 1,
              mx: 1,
              mb: 0.5,
              mt: 2, // Add margin top to separate from other routes
              '&.Mui-selected': {
                bgcolor: 'action.selected',
                color: 'primary.main',
                '&:hover': {
                  bgcolor: 'action.hover',
                }
              }
            }}
          >
            <ListItemIcon
              sx={{
                color: location.pathname === yourPagesNavItem.path ? 'primary.main' : 'inherit',
                minWidth: '32px'
              }}
            >
              {yourPagesNavItem.icon}
            </ListItemIcon>
            <ListItemText
              primary={yourPagesNavItem.name}
              sx={{
                '& .MuiListItemText-primary': {
                  color: location.pathname === yourPagesNavItem.path ? 'primary.main' : 'inherit',
                  fontWeight: location.pathname === yourPagesNavItem.path ? 600 : 400
                }
              }}
            />
            {pagesWithoutRoute.length > 0 && (expandedSections[yourPagesNavItem.id] ? <ExpandLess /> : <ExpandMore />)}
          </ListItemButton>

          {/* Pages without specific routes */}
          {pagesWithoutRoute.length > 0 && (
            <Collapse in={expandedSections[yourPagesNavItem.id]} timeout="auto" unmountOnExit>
              <List component="div" disablePadding sx={{ pl: 2 }}>
                {pagesWithoutRoute
                  .sort((a, b) => (a.navigation_order || 0) - (b.navigation_order || 0))
                  .map(page => {
                    const pagePath = `${basePath}/${page.route}`;
                    const isChildActive = location.pathname === pagePath;

                    return (
                      <ListItem
                        key={page.id}
                        component={Link}
                        to={pagePath}
                        selected={isChildActive}
                        sx={{
                          color: 'text.primary',
                          '&.Mui-selected': {
                            bgcolor: 'action.selected',
                            color: 'primary.main'
                          },
                          '&:hover': {
                            bgcolor: 'action.hover'
                          }
                        }}
                      >
                        <ListItemIcon sx={{ minWidth: '32px' }}>
                          {page.icon ? (
                            <IconResolver icon={page.icon} />
                          ) : (
                            <PageIcon />
                          )}
                        </ListItemIcon>
                        <ListItemText primary={page.name} />
                      </ListItem>
                    );
                  })}
              </List>
            </Collapse>
          )}
        </React.Fragment>
      </>
    );
  };

  if (isLoading || isLoadingTree) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Loading navigation...
        </Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="error">
          Error loading navigation
        </Typography>
      </Box>
    );
  }

  // Get pages without a specific route (for "Your Pages" section)
  const pagesWithoutRoute = pageHierarchy.root.filter(page =>
    page.is_published && // Only include published pages
    !page.navigation_route_id &&
    !page.parent_route &&
    (!page.parent_type || page.parent_type === 'page') &&
    !excludedPageNames.includes(page.name)
  );

  return (
    <List component="nav" sx={{ width: '100%' }}>
      {/* Render hierarchical navigation if available, otherwise fall back to legacy */}
      {navigationTree.length > 0 ? (
        <>
          {console.log('Rendering hierarchical navigation tree with', navigationTree.length, 'routes')}
          {renderNavigationTree(navigationTree)}
          
          {/* Built-in "Your Pages" section - always at the bottom */}
          <React.Fragment key="your-pages-builtin">
            <ListItemButton
              selected={location.pathname === '/pages'}
              onClick={() => {
                // Always toggle the section if it has children
                const hasChildren = pagesWithoutRoute.length > 0;
                if (hasChildren) {
                  const newExpandedRoutes = new Set(expandedRoutes);
                  if (expandedRoutes.has('your-pages-builtin')) {
                    newExpandedRoutes.delete('your-pages-builtin');
                  } else {
                    newExpandedRoutes.add('your-pages-builtin');
                  }
                  setExpandedRoutes(newExpandedRoutes);
                }
                // Always navigate to the path regardless of whether it has children
                navigate('/pages');
              }}
              sx={{
                borderRadius: 1,
                mx: 1,
                mb: 0.5,
                mt: 2, // Add margin top to separate from other routes
                '&.Mui-selected': {
                  bgcolor: 'action.selected',
                  color: 'primary.main',
                  '&:hover': {
                    bgcolor: 'action.hover',
                  }
                }
              }}
            >
              <ListItemIcon
                sx={{
                  color: location.pathname === '/pages' ? 'primary.main' : 'inherit',
                  minWidth: '32px'
                }}
              >
                <CollectionsBookmarkIcon />
              </ListItemIcon>
              <ListItemText
                primary="Your Pages"
                sx={{
                  '& .MuiListItemText-primary': {
                    color: location.pathname === '/pages' ? 'primary.main' : 'inherit',
                    fontWeight: location.pathname === '/pages' ? 600 : 400
                  }
                }}
              />
              {pagesWithoutRoute.length > 0 && (expandedRoutes.has('your-pages-builtin') ? <ExpandLess /> : <ExpandMore />)}
            </ListItemButton>

            {/* Pages without specific routes */}
            {pagesWithoutRoute.length > 0 && (
              <Collapse in={expandedRoutes.has('your-pages-builtin')} timeout="auto" unmountOnExit>
                <List component="div" disablePadding sx={{ pl: 2 }}>
                  {pagesWithoutRoute
                    .sort((a, b) => (a.navigation_order || 0) - (b.navigation_order || 0))
                    .map(page => {
                      const pagePath = `${basePath}/${page.route}`;
                      const isChildActive = location.pathname === pagePath;

                      return (
                        <ListItem
                          key={page.id}
                          component={Link}
                          to={pagePath}
                          selected={isChildActive}
                          sx={{
                            color: 'text.primary',
                            '&.Mui-selected': {
                              bgcolor: 'action.selected',
                              color: 'primary.main'
                            },
                            '&:hover': {
                              bgcolor: 'action.hover'
                            }
                          }}
                        >
                          <ListItemIcon sx={{ minWidth: '32px' }}>
                            {page.icon ? (
                              <IconResolver icon={page.icon} />
                            ) : (
                              <PageIcon />
                            )}
                          </ListItemIcon>
                          <ListItemText primary={page.name} />
                        </ListItem>
                      );
                    })}
                </List>
              </Collapse>
            )}
          </React.Fragment>
        </>
      ) : (
        <>
          {console.log('Rendering legacy navigation - tree length is', navigationTree.length)}
          {renderLegacyNavigation()}
        </>
      )}
    </List>
  );
};
