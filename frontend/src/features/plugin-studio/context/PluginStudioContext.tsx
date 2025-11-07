import { createContext } from 'react';
import { Page, Layouts, ViewModeState, DynamicPluginConfig, GridItem, LayoutItem } from '../types';
import { CanvasConfig } from '../types/page.types';
import { ZoomMode } from '../hooks/ui/useCanvas';

/**
 * Interface for the PluginStudio context
 */
export interface PluginStudioContextType {
  // Page state
  pages: Page[];
  currentPage: Page | null;
  setCurrentPage: (page: Page) => void;
  createPage: (pageName: string) => Promise<Page | null>;
  deletePage: (pageId: string) => Promise<void>;
  renamePage: (pageId: string, newName: string) => Promise<void>;
  savePage: (pageId: string, options?: {
    layoutOverride?: any;
    awaitCommit?: boolean;
  }) => Promise<Page | null>;
  publishPage: (pageId: string, publish: boolean) => Promise<void>;
  backupPage: (pageId: string) => Promise<void>;
  restorePage: (pageId: string) => Promise<void>;
  updatePage: (pageId: string, updates: Partial<Page>) => Promise<void>;
  
  // Layout state
  layouts: Layouts | null;
  handleLayoutChange: (layout: any[], newLayouts: Layouts, metadata?: { origin?: { source?: string }; [key: string]: unknown }) => void;
  addItem: (item: GridItem | LayoutItem, activeDeviceType: keyof Layouts) => void;
  removeItem: (id: string) => void;
  handleResizeStart: () => void;
  handleResizeStop: () => void;
  flushLayoutChanges?: () => Promise<void>; // Phase 3: Add flush method
  
  // Plugin state
  availablePlugins: DynamicPluginConfig[];
  
  // UI state
  viewMode: ViewModeState;
  setViewMode: (mode: ViewModeState) => void;
  previewMode: boolean;
  togglePreviewMode: () => void;
  viewWidth: number;
  containerWidth: number;
  setContainerWidth: (width: number) => void;

  // Canvas state
  canvas: CanvasConfig;
  setCanvas: (config: CanvasConfig) => void;
  zoom: number;
  setZoom: (value: number | ((prev: number) => number)) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  zoomMode: ZoomMode;
  setZoomMode: (mode: ZoomMode) => void;
  applyAutoZoom: (value: number) => void;
  
  // Selection state
  selectedItem: { i: string } | null;
  setSelectedItem: (item: { i: string } | null) => void;
  
  // Dialog state
  configDialogOpen: boolean;
  setConfigDialogOpen: (open: boolean) => void;
  jsonViewOpen: boolean;
  setJsonViewOpen: (open: boolean) => void;
  pageManagementOpen: boolean;
  setPageManagementOpen: (open: boolean) => void;
  routeManagementOpen: boolean;
  setRouteManagementOpen: (open: boolean) => void;
  
  // Loading state
  isLoading: boolean;
  error: string | null;
}

/**
 * Create the PluginStudio context with null as the default value
 */
export const PluginStudioContext = createContext<PluginStudioContextType | null>(null);
