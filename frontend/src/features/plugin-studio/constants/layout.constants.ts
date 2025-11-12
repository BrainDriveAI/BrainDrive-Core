import { DeviceType, ViewModeConfigs } from '../types';

/**
 * Breakpoints for different device types (in pixels)
 */
export const DEVICE_BREAKPOINTS = {
  mobile: 480,   // 0-480px is mobile
  tablet: 768,   // 481-768px is tablet
  desktop: 1200  // >768px is desktop
};

/**
 * Number of columns for each device type
 */
export const VIEW_MODE_COLS: Record<DeviceType | 'custom', number> = {
  mobile: 4,    // 4 columns for mobile
  tablet: 8,    // 8 columns for tablet
  desktop: 12,  // 12 columns for desktop
  custom: 12    // Use desktop columns for custom mode
};

/**
 * Layout configurations for each device type
 */
export const VIEW_MODE_LAYOUTS: ViewModeConfigs = {
  mobile: {
    cols: 4,
    rowHeight: 50,
    margin: [0, 0],
    padding: [0, 0],
    defaultItemSize: {
      w: 4,
      h: 4
    }
  },
  tablet: {
    cols: 8,
    rowHeight: 60,
    margin: [0, 0],
    padding: [0, 0],
    defaultItemSize: {
      w: 4,
      h: 4
    }
  },
  desktop: {
    cols: 12,
    rowHeight: 60,
    margin: [0, 0],
    padding: [0, 0],
    defaultItemSize: {
      w: 3,
      h: 4
    }
  },
  custom: {
    cols: 12,
    rowHeight: 60,
    margin: [0, 0],
    padding: [0, 0],
    defaultItemSize: {
      w: 3,
      h: 4
    }
  }
};

/**
 * Minimum width for the canvas (in pixels)
 */
export const MIN_CANVAS_WIDTH = 320;

/**
 * Default width for the plugin toolbar (in pixels)
 */
export const PLUGIN_TOOLBAR_WIDTH = 280;
