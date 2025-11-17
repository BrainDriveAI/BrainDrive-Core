import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { usePages, PageHierarchy } from '../hooks/usePages';
import { DynamicPageRenderer } from './DynamicPageRenderer';
import { RouteContentRenderer } from './RouteContentRenderer';
import { CircularProgress, Box } from '@mui/material';
import { navigationService } from '../services/navigationService';
import { NavigationRoute } from '../types/navigation';
import BannerPage from './BannerPage';

interface DynamicRoutesProps {
  basePath?: string; // Optional base path for all dynamic routes
}

export const DynamicRoutes: React.FC<DynamicRoutesProps> = ({ basePath = '' }) => {
  const { pageHierarchy, isLoading: loadingPages, error: pagesError } = usePages();
  const navigate = useNavigate();
  const [navigationRoutes, setNavigationRoutes] = useState<NavigationRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch navigation routes
  useEffect(() => {
    const fetchNavigationRoutes = async () => {
      try {
        const routes = await navigationService.getNavigationRoutes();
        setNavigationRoutes(routes);
      } catch (err) {
        console.error('Error fetching navigation routes:', err);
        setError('Failed to load navigation routes');
      } finally {
        setLoading(false);
      }
    };

    fetchNavigationRoutes();
  }, []);

  // Determine overall loading state
  const isLoading = loadingPages || loading;
  // Determine overall error state
  const hasError = pagesError || error;
  
  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }
  
  if (error) {
    console.error('Error loading pages:', error);
    navigate('/dashboard');
    return null;
  }
  
  return (
    <Routes>
      {/* Index route for the pages section */}
      <Route index element={
        <BannerPage 
          routeName=""
          routeDescription="Goto BrainDrive Page Builder to create and manage unique pages for each of your AI use cases."
          showHelp={false}
        />
      } />
      
      {/* Dynamic routes based on page hierarchy and navigation routes */}
      {renderDynamicRoutes(pageHierarchy, navigationRoutes)}
      
      {/* Catch-all route for the pages section */}
      <Route path="*" element={
        <BannerPage 
          routeName="Page Not Found"
          routeDescription="The requested page does not exist or is not published."
          showHelp={false}
        />
      } />
    </Routes>
  );
};

// Helper function to render dynamic routes based on the page hierarchy and navigation routes
function renderDynamicRoutes(hierarchy: PageHierarchy, navigationRoutes: NavigationRoute[]) {
  const routes: JSX.Element[] = [];
  
  // Process all sections of the hierarchy
  Object.keys(hierarchy).forEach(section => {
    // Skip empty sections
    if (!hierarchy[section] || hierarchy[section].length === 0) {
      return;
    }
    
    // Add routes for pages in this section
    hierarchy[section].forEach(page => {
      if (page.is_parent_page) {
        // Parent page with potential children
        routes.push(
          <Route key={page.id} path={page.route} element={<DynamicPageRenderer pageId={page.id} />}>
            {/* Child page routes */}
            {hierarchy[page.route || '']?.map(childPage => {
              // Extract the last segment of the child route
              const childSegment = childPage.route?.split('/').pop() || '';
              
              return (
                <Route 
                  key={childPage.id} 
                  path={childSegment} 
                  element={<DynamicPageRenderer pageId={childPage.id} />} 
                />
              );
            })}
          </Route>
        );
      } else {
        // Regular page without children
        routes.push(
          <Route 
            key={page.id} 
            path={page.route} 
            element={<DynamicPageRenderer pageId={page.id} />} 
          />
        );
      }
    });
  });
  
  // Add routes for custom navigation routes
  navigationRoutes.forEach(navRoute => {
    if (hierarchy[navRoute.route] && hierarchy[navRoute.route].length > 0) {
      hierarchy[navRoute.route].forEach(page => {
        routes.push(
          <Route 
            key={`${navRoute.route}-${page.id}`} 
            path={page.route} 
            element={<DynamicPageRenderer pageId={page.id} />} 
          />
        );
      });
    }
    
    // Add a route for the navigation route itself
    routes.push(
      <Route
        key={`route-${navRoute.route}`}
        path={navRoute.route}
        element={<RouteContentRenderer route={navRoute.route} />}
      />
    );
  });
  
  return routes;
}
