import { Layouts } from './layout.types';
import { ModuleDefinition } from './plugin.types';

// Canvas configuration for Plugin Studio logical canvas
export interface CanvasConfig {
  width: number;      // logical width in px
  height: number;     // logical height in px
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
}

/**
 * Page interface representing a page in the application
 */
export interface Page {
  id: string;
  name: string;
  description: string;
  
  // Layouts for different device types
  layouts: Layouts;
  
  // Module definitions
  modules?: Record<string, ModuleDefinition>;
  
  // Optional canvas configuration persisted with content
  canvas?: CanvasConfig;
  
  // Breakpoints for responsive design
  defaultBreakpoints?: {
    tablet?: number;
    mobile?: number;
  };
  
  // Routing information
  route?: string;
  route_segment?: string;         // Just this page's segment of the route
  parent_route?: string;
  parent_type?: string;           // Type of parent: 'page', 'dashboard', 'plugin-studio', 'settings'
  
  // Publishing information
  is_published?: boolean;
  publish_date?: string;
  backup_date?: string;
  
  // Content and backup
  content?: any;
  content_backup?: any;
  
  // Creator and navigation
  creator_id?: string;
  navigation_route_id?: string;
  
  // Local flag
  is_local?: boolean; // Flag to indicate if this is a local page that hasn't been saved to the backend
  
  // Enhanced routing fields for nested routes
  is_parent_page?: boolean;       // Flag indicating if this is a parent page that can have children
  children?: string[];            // Array of child page IDs
  display_in_navigation?: boolean; // Whether to show in navigation menus
  navigation_order?: number;      // Order in navigation menus
  icon?: string;                  // Icon for navigation display
}

/**
 * Navigation route interface
 */
export interface NavigationRoute {
  id: string;
  name: string;
  route: string;
  icon?: string;
  parent_id?: string;
  order?: number;
  is_visible?: boolean;
  children?: NavigationRoute[];
}

/**
 * Page creation parameters
 */
export interface CreatePageParams {
  name: string;
  route: string;
  description?: string;
  content?: {
    layouts?: Layouts;
    modules?: Record<string, ModuleDefinition>;
    canvas?: CanvasConfig;
  };
  parent_route?: string;
  parent_type?: string;
  is_parent_page?: boolean;
  navigation_route_id?: string;
}

/**
 * Page update parameters
 */
export interface UpdatePageParams {
  name?: string;
  description?: string;
  content?: {
    layouts?: Layouts;
    modules?: Record<string, ModuleDefinition>;
    canvas?: CanvasConfig;
  };
  route?: string;
  parent_route?: string;
  parent_type?: string;
  is_parent_page?: boolean;
  navigation_route_id?: string;
  is_published?: boolean;
}

/**
 * Page hierarchy update parameters
 */
export interface PageHierarchyParams {
  parent_route: string;
  parent_type: string;
  is_parent_page: boolean;
}
