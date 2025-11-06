import { CanvasConfig } from '../types/page.types';

export const DEFAULT_CANVAS_CONFIG: CanvasConfig = {
  width: 1440,
  height: 2400,
  minWidth: 960,
  maxWidth: 1920,
  minHeight: 1200,
  maxHeight: 4000,
};

export const ZOOM_LIMITS = {
  min: 0.5,
  max: 1.5,
  step: 0.1,
};

