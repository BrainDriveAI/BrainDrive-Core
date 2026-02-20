import React, { useState, useEffect } from 'react';
import { useParams, useLocation, Navigate, useNavigate } from 'react-router-dom';
import { Box, CircularProgress, Typography } from '@mui/material';
import { navigationService } from '../services/navigationService';
import { componentService } from '../services/componentService';
import { pageService } from '../services/pageService';
import { defaultPageService } from '../services/defaultPageService';
import { NavigationRoute } from '../types/navigation';
import { Component } from '../types/component';
import { Page } from '../pages';
import { DynamicPageRenderer } from './DynamicPageRenderer';
import Dashboard from '../pages/Dashboard';
import { PluginStudioPage } from '../features/plugin-studio';
import Settings from '../pages/Settings';
import PluginManagerPage from '../pages/PluginManagerPage';
import PersonasPage from '../pages/PersonasPage';



interface RouteContentRendererProps {
  route?: string;
}

const FALLBACK_SYSTEM_ROUTES: Record<string, NavigationRoute> = {
  dashboard: { id: "fallback-dashboard", name: "Dashboard", route: "dashboard", creator_id: "system", is_system_route: true, is_visible: true },
  "plugin-studio": { id: "fallback-plugin-studio", name: "Plugin Studio", route: "plugin-studio", creator_id: "system", is_system_route: true, is_visible: true },
  settings: { id: "fallback-settings", name: "Settings", route: "settings", creator_id: "system", is_system_route: true, is_visible: true },
  "plugin-manager": { id: "fallback-plugin-manager", name: "Plugin Manager", route: "plugin-manager", creator_id: "system", is_system_route: true, is_visible: true },
  personas: { id: "fallback-personas", name: "Personas", route: "personas", creator_id: "system", is_system_route: true, is_visible: true }
};

const getFallbackSystemRoute = (routePath: string): NavigationRoute | null => {
  return FALLBACK_SYSTEM_ROUTES[routePath] || null;
};

export const RouteContentRenderer: React.FC<RouteContentRendererProps> = ({ route }) => {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [navigationRoute, setNavigationRoute] = useState<NavigationRoute | null>(null);
  const [component, setComponent] = useState<Component | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [redirectPath, setRedirectPath] = useState<string | null>(null);

  // Determine the route to load
  const routePath = route || params['*'] || location.pathname.substring(1);

  useEffect(() => {
    const loadRouteContent = async () => {
      try {
        setLoading(true);
        setError(null);
        setNavigationRoute(null);
        setComponent(null);
        setPage(null);
        setRedirectPath(null);

        // console.log('Loading route content for:', routePath);

        // Get the navigation route (fallback to built-in system route when backend rows are missing)
        const fetchedRoute = await navigationService.getNavigationRouteByRoute(routePath);
        const navRoute = fetchedRoute || getFallbackSystemRoute(routePath);
        if (!navRoute) {
          throw new Error(`Route not found: ${routePath}`);
        }
        if (!fetchedRoute) {
          console.warn(`Using fallback system route for path: ${routePath}`);
        }

        setNavigationRoute(navRoute);
        // console.log('Found navigation route:', navRoute);

        // Check if the route has a default component
        if (navRoute.default_component_id) {
          // console.log(`Route has default_component_id: ${navRoute.default_component_id}`);
          const comp = await componentService.getComponentByComponentId(navRoute.default_component_id);
          if (comp) {
            setComponent(comp);
            // console.log('Found default component:', comp);
          } else {
            console.error(`Component with ID ${navRoute.default_component_id} not found`);
          }
        } else {
          // console.log('Route has no default_component_id');
        }

        // Check if the route has a default page
        if (navRoute.default_page_id) {
          // console.log(`Route has default_page_id: ${navRoute.default_page_id} (type: ${typeof navRoute.default_page_id})`);
          
          // Ensure the default_page_id is in the correct format
          // Remove any hyphens if present to match the format expected by the API
          const formattedPageId = typeof navRoute.default_page_id === 'string' 
            ? navRoute.default_page_id.replace(/-/g, '')
            : navRoute.default_page_id;
          
          // console.log(`Formatted page ID: ${formattedPageId}`);
          
          try {
            // First try to fetch the page using the regular pageService
            // console.log(`Attempting to fetch page with ID: ${formattedPageId}`);
            try {
              const pg = await pageService.getPage(formattedPageId);
              if (pg) {
                setPage(pg);
                // console.log('Found default page:', pg);
                
                // Set redirect path to the page route if we're directly accessing the navigation route
                if (location.pathname === `/${navRoute.route}`) {
                  // console.log(`Setting redirect to page route: /pages/${pg.route}`);
                  setRedirectPath(`/pages/${pg.route}`);
                }
                return;
              }
            } catch (pageErr) {
              // console.log('Regular page fetch failed, trying defaultPageService:', pageErr);
              
              // If the regular pageService fails (e.g., due to publication check),
              // try using our custom defaultPageService that can handle unpublished pages
              const defaultPage = await defaultPageService.getDefaultPage(formattedPageId);
              if (defaultPage) {
                setPage(defaultPage);
                // console.log('Found default page using defaultPageService:', defaultPage);
                
                // Don't set redirect path for unpublished pages
                // This will render the page directly in the route context
                return;
              }
              
              // If both methods fail, try with the original ID
              if (formattedPageId !== navRoute.default_page_id) {
                // console.log(`Trying with original ID: ${navRoute.default_page_id}`);
                const pgWithOriginalId = await defaultPageService.getDefaultPage(navRoute.default_page_id);
                if (pgWithOriginalId) {
                  setPage(pgWithOriginalId);
                  // console.log('Found default page with original ID:', pgWithOriginalId);
                  return;
                }
              }
              
              // If all else fails, try to fetch by route
              // console.log(`Attempting to fetch page by route: ${routePath}`);
              try {
                const pgByRoute = await pageService.getPageByRoute(routePath);
                if (pgByRoute) {
                  setPage(pgByRoute);
                  // console.log('Found page by route:', pgByRoute);
                } else {
                  console.error(`Page with route ${routePath} not found`);
                }
              } catch (routeErr) {
                console.error(`Error fetching page by route: ${routeErr}`);
              }
            }
          } catch (err) {
            console.error('Error in default page fetching process:', err);
            setError(`Failed to fetch default page: ${err instanceof Error ? err.message : String(err)}`);
          }
        } else {
          // console.log('Route has no default_page_id');
        }
      } catch (err) {
        console.error('Error loading route content:', err);
        setError(err instanceof Error ? err.message : 'Failed to load route content');
      } finally {
        setLoading(false);
      }
    };

    if (routePath) {
      loadRouteContent();
    }
  }, [routePath]);

  // If we have a redirect path, navigate to it
  if (redirectPath) {
    // console.log(`Redirecting to: ${redirectPath}`);
    return <Navigate to={redirectPath} replace />;
  }

  // Loading state
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  // Error state
  if (error) {
    return (
      <Box sx={{ p: 4, textAlign: 'center' }}>
        <Typography variant="h5" color="error" gutterBottom>
          Error Loading Route
        </Typography>
        <Typography variant="body1">
          {error}
        </Typography>
      </Box>
    );
  }

  // If no navigation route was found, show an error
  if (!navigationRoute) {
    return (
      <Box sx={{ p: 4, textAlign: 'center' }}>
        <Typography variant="h5" color="error" gutterBottom>
          Route Not Found
        </Typography>
        <Typography variant="body1">
          The requested route does not exist.
        </Typography>
      </Box>
    );
  }

  // If the route has a default page, render it
  if (page) {
    // console.log(`Rendering default page with ID: ${page.id}, route: ${page.route}`);
    // Use the page ID directly instead of relying on the route
    // Pass allowUnpublished=true to bypass the publication check for default pages
    return <DynamicPageRenderer pageId={page.id} allowUnpublished={true} />;
  }

  // If the route has a default component, render the appropriate component
  if (component) {
    // console.log(`Rendering component: ${component.component_id}`);
    
    switch (component.component_id) {
      case 'dashboard':
        // console.log('Rendering Dashboard component');
        return <Dashboard />;
      case 'plugin-studio':
        // console.log('Rendering new PluginStudio component');
        return <PluginStudioPage />; // Use the new implementation
      case 'settings':
        // console.log('Rendering Settings component');
        return <Settings />;
      case 'plugin-manager':
        // console.log('Rendering PluginManagerPage component');
        return <PluginManagerPage />;
      default:
        console.warn(`Unknown component: ${component.component_id}`);
        break;
    }
  }

  // Special case for system routes if no default component or page is set
  if (navigationRoute.is_system_route) {
    if (routePath === 'dashboard') {
      // console.log('Rendering Dashboard component as fallback for dashboard route');
      return <Dashboard />;
    } else if (routePath === 'plugin-studio') {
      // console.log('Rendering PluginStudio component as fallback for plugin-studio route');
      return <PluginStudioPage />; // Use the new implementation
    } else if (routePath === 'settings') {
      // console.log('Rendering Settings component as fallback for settings route');
      return <Settings />;
    } else if (routePath === 'plugin-manager') {
      // console.log('Rendering PluginManagerPage component as fallback for plugin-manager route');
      return <PluginManagerPage />;
    } else if (routePath === 'personas') {
      // console.log('Rendering PersonasPage component as fallback for personas route');
      return <PersonasPage />;
    }
  }

  // If the route has no default page or component, show the BannerPage
  return (
    <BannerPage 
      routeName={navigationRoute.name}
      routeDescription={navigationRoute.description}
      showHelp={true}
    />
  );
};
