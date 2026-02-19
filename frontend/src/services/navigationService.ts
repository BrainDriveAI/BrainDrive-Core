import ApiService from './ApiService';
import { NavigationRoute, NavigationRouteTree, NavigationRouteMove, NavigationRouteBatchUpdate } from '../types/navigation';

const API_PATH = '/api/v1/navigation-routes';
const FALLBACK_SYSTEM_ROUTES: Record<string, NavigationRoute> = {
  dashboard: { id: "fallback-dashboard", name: "Dashboard", route: "dashboard", creator_id: "system", is_system_route: true, is_visible: true },
  "plugin-studio": { id: "fallback-plugin-studio", name: "Plugin Studio", route: "plugin-studio", creator_id: "system", is_system_route: true, is_visible: true },
  settings: { id: "fallback-settings", name: "Settings", route: "settings", creator_id: "system", is_system_route: true, is_visible: true },
  "plugin-manager": { id: "fallback-plugin-manager", name: "Plugin Manager", route: "plugin-manager", creator_id: "system", is_system_route: true, is_visible: true },
  personas: { id: "fallback-personas", name: "Personas", route: "personas", creator_id: "system", is_system_route: true, is_visible: true }
};


export const navigationService = {
  // Get all navigation routes
  async getNavigationRoutes(): Promise<NavigationRoute[]> {
    try {
      const apiService = ApiService.getInstance();
      
      // Check if we have an access token
      const token = localStorage.getItem('accessToken');
      
      // Check if we have a valid token before making the API call
      if (!token) {
        console.warn('No access token available, skipping navigation routes API call');
        return [];
      }
      
      const response = await apiService.get(API_PATH);
      
      // If response is empty or not an array, return an empty array
      if (!response) {
        console.warn('Navigation routes response is empty');
        return [];
      }
      
      if (!Array.isArray(response)) {
        console.warn('Navigation routes response is not an array:', response);
        // Try to convert to array if possible
        if (response && typeof response === 'object') {
          // console.log('Attempting to convert response to array');
          const converted = Object.values(response) as NavigationRoute[];
          // console.log('Converted response:', converted);
          return converted;
        }
        return [];
      }
      
      // Log each route for debugging
      response.forEach((route, index) => {
        // console.log(`Route ${index}:`, route);
      });
      
      return response;
    } catch (error) {
      console.error('Failed to fetch navigation routes:', error);
      // Return empty array instead of throwing to prevent UI errors
      return [];
    }
  },

  // Get a navigation route by ID
  async getNavigationRoute(routeId: string): Promise<NavigationRoute | null> {
    try {
      // console.log(`Fetching navigation route with ID: ${routeId}`);
      const apiService = ApiService.getInstance();
      const response = await apiService.get(`${API_PATH}/${routeId}`);
      // console.log(`Navigation route ${routeId} response:`, response);
      
      if (!response) {
        console.warn(`Navigation route ${routeId} response is empty`);
        return null;
      }
      
      return response;
    } catch (error) {
      console.error(`Failed to fetch navigation route ${routeId}:`, error);
      return null;
    }
  },

  // Create a new navigation route
  async createNavigationRoute(routeData: Partial<NavigationRoute>): Promise<NavigationRoute> {
    try {
      const apiService = ApiService.getInstance();
      
      // Create a new object with all properties
      const dataToSend = { ...routeData };
      
      // Ensure default_page_id is explicitly null if not provided
      if (dataToSend.default_page_id === null) {
        // console.log('default_page_id is explicitly null for new route');
      } else if (dataToSend.default_page_id) {
        // console.log(`default_page_id has a value for new route: ${dataToSend.default_page_id}`);
      } else {
        // console.log('default_page_id is undefined or empty for new route - setting to explicit null');
        // Use type assertion to allow null assignment for API compatibility
        (dataToSend as any).default_page_id = null;
      }
      
      // console.log('Final data being sent to backend for new route:', JSON.stringify(dataToSend, null, 2));
      
      return await apiService.post(API_PATH, dataToSend);
    } catch (error) {
      console.error('Failed to create navigation route:', error);
      throw new Error(`Failed to create navigation route: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Update a navigation route
  async updateNavigationRoute(routeId: string, routeData: Partial<NavigationRoute>): Promise<NavigationRoute> {
    try {
      // console.log(`Updating navigation route with ID: ${routeId}`);
      // console.log('Route data being sent:', JSON.stringify(routeData, null, 2));
      // console.log('default_page_id type:', routeData.default_page_id === null ? 'null' : typeof routeData.default_page_id);
      
      // Format the route ID properly with hyphens if needed
      const formattedRouteId = routeId.replace(/([0-9a-f]{8})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{12})/i, '$1-$2-$3-$4-$5');
      // console.log(`Formatted route ID: ${formattedRouteId}`);
      
      // Ensure default_page_id is properly formatted if present
      if (routeData.default_page_id) {
        // Format UUID with hyphens if it doesn't have them
        if (!routeData.default_page_id.includes('-')) {
          routeData.default_page_id = routeData.default_page_id.replace(
            /([0-9a-f]{8})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{12})/i,
            '$1-$2-$3-$4-$5'
          );
          // console.log(`Formatted default_page_id: ${routeData.default_page_id}`);
        }
      } else if (routeData.default_page_id === null) {
        // Explicitly log when we're clearing the default_page_id
        // console.log('Explicitly setting default_page_id to null');
      }
      
      const apiService = ApiService.getInstance();
      
      // Create a new object with all properties
      const dataToSend = { ...routeData };
      
      // Log the default_page_id value
      if (dataToSend.default_page_id === null) {
        // console.log('default_page_id is explicitly null - this should clear it in the database');
      } else if (dataToSend.default_page_id) {
        // console.log(`default_page_id has a value: ${dataToSend.default_page_id}`);
      } else {
        // console.log('default_page_id is undefined or empty - setting to explicit null');
        // Use type assertion to allow null assignment for API compatibility
        (dataToSend as any).default_page_id = null;
      }
      
      // console.log('Final data being sent to backend:', JSON.stringify(dataToSend, null, 2));
      
      return await apiService.put(`${API_PATH}/${formattedRouteId}`, dataToSend);
    } catch (error) {
      console.error(`Failed to update navigation route ${routeId}:`, error);
      throw new Error(`Failed to update navigation route: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Delete a navigation route
  async deleteNavigationRoute(routeId: string): Promise<void> {
    try {
      const apiService = ApiService.getInstance();
      await apiService.delete(`${API_PATH}/${routeId}`);
    } catch (error) {
      console.error(`Failed to delete navigation route ${routeId}:`, error);
      throw new Error(`Failed to delete navigation route: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Get visible navigation routes
  async getVisibleNavigationRoutes(): Promise<NavigationRoute[]> {
    try {
      // console.log('Fetching visible navigation routes');
      const apiService = ApiService.getInstance();
      const response = await apiService.get(`${API_PATH}`, {
        params: { visible_only: 'true' }
      });
      
      // console.log('Visible navigation routes response:', response);
      
      // If response is empty or not an array, return an empty array
      if (!response || !Array.isArray(response)) {
        console.warn('Visible navigation routes response is not an array:', response);
        return [];
      }
      
      return response;
    } catch (error) {
      console.error('Failed to fetch visible navigation routes:', error);
      // Return empty array instead of throwing to prevent UI errors
      return [];
    }
  },

  // Get a navigation route by route path
  async getNavigationRouteByRoute(route: string): Promise<NavigationRoute | null> {
    try {
      // First get all routes
      const routes = await this.getNavigationRoutes();

      // Find the route with the matching route path
      const matchingRoute = routes.find(r => r.route === route);

      if (!matchingRoute) {
        const fallbackRoute = FALLBACK_SYSTEM_ROUTES[route];
        if (fallbackRoute) {
          console.info(`Using fallback system navigation route for path: ${route}`);
          return fallbackRoute;
        }

        console.warn(`No navigation route found with route path: ${route}`);
        return null;
      }

      // console.log(`Found navigation route for path ${route}:`, matchingRoute);
      return matchingRoute;
    } catch (error) {
      console.error(`Failed to fetch navigation route by path ${route}:`, error);
      return null;
    }
  },

  // HIERARCHICAL NAVIGATION METHODS

  // Get navigation routes as tree structure
  async getNavigationTree(): Promise<NavigationRouteTree[]> {
    try {
      console.log('🌳 [NavigationService] Starting getNavigationTree...');
      const apiService = ApiService.getInstance();
      
      const token = localStorage.getItem('accessToken');
      if (!token) {
        console.warn('🌳 [NavigationService] No access token available, skipping navigation tree API call');
        return [];
      }
      
      console.log('🌳 [NavigationService] Making API call to /tree endpoint...');
      const response = await apiService.get(`${API_PATH}/tree`);
      console.log('🌳 [NavigationService] Tree API response:', response);
      
      if (!response || !Array.isArray(response)) {
        console.warn('🌳 [NavigationService] Navigation tree response is not an array:', response);
        return [];
      }
      
      console.log('🌳 [NavigationService] Successfully fetched tree with', response.length, 'root routes');
      return response;
    } catch (error) {
      console.error('🌳 [NavigationService] Failed to fetch navigation tree:', error);
      return [];
    }
  },

  // Move a navigation route
  async moveNavigationRoute(routeId: string, moveData: NavigationRouteMove): Promise<NavigationRoute> {
    try {
      const apiService = ApiService.getInstance();
      return await apiService.put(`${API_PATH}/${routeId}/move`, moveData);
    } catch (error) {
      console.error(`Failed to move navigation route ${routeId}:`, error);
      throw new Error(`Failed to move navigation route: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Batch update navigation routes
  async batchUpdateNavigationRoutes(updates: NavigationRouteBatchUpdate[]): Promise<NavigationRoute[]> {
    try {
      const apiService = ApiService.getInstance();
      return await apiService.post(`${API_PATH}/batch-update`, updates);
    } catch (error) {
      console.error('Failed to batch update navigation routes:', error);
      throw new Error(`Failed to batch update navigation routes: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Toggle expanded state of a navigation route
  async toggleNavigationRouteExpanded(routeId: string, isExpanded: boolean): Promise<NavigationRoute> {
    try {
      const updates: NavigationRouteBatchUpdate[] = [{
        id: routeId,
        is_expanded: isExpanded
      }];
      
      const results = await this.batchUpdateNavigationRoutes(updates);
      if (results.length === 0) {
        throw new Error('No route was updated');
      }
      
      return results[0];
    } catch (error) {
      console.error(`Failed to toggle expanded state for route ${routeId}:`, error);
      throw new Error(`Failed to toggle expanded state: ${error instanceof Error ? error.message : String(error)}`);
    }
  },

  // Get navigation route with children
  async getNavigationRouteWithChildren(routeId: string): Promise<NavigationRouteTree | null> {
    try {
      const tree = await this.getNavigationTree();
      
      // Recursively search for the route in the tree
      const findRouteInTree = (routes: NavigationRouteTree[], targetId: string): NavigationRouteTree | null => {
        for (const route of routes) {
          if (route.id === targetId) {
            return route;
          }
          if (route.children && route.children.length > 0) {
            const found = findRouteInTree(route.children, targetId);
            if (found) return found;
          }
        }
        return null;
      };
      
      return findRouteInTree(tree, routeId);
    } catch (error) {
      console.error(`Failed to get navigation route with children ${routeId}:`, error);
      return null;
    }
  }
};
