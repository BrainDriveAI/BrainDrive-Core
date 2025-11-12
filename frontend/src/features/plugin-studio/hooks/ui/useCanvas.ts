import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CanvasConfig } from '../../types/page.types';
import { DEFAULT_CANVAS_CONFIG, ZOOM_LIMITS } from '../../constants/canvas.constants';

export type ZoomMode = 'auto' | 'manual';

export interface UseCanvasState {
  canvas: CanvasConfig;
  setCanvas: (config: CanvasConfig) => void;
  zoom: number;
  setZoom: (value: number | ((prev: number) => number)) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  zoomMode: ZoomMode;
  setZoomMode: (mode: ZoomMode) => void;
  applyAutoZoom: (value: number) => void;
}

/**
 * Manage logical canvas configuration and zoom state.
 */
export const useCanvas = (initial?: Partial<CanvasConfig>): UseCanvasState => {
  const normalize = useCallback((config?: Partial<CanvasConfig>): CanvasConfig => ({
    ...DEFAULT_CANVAS_CONFIG,
    ...(config || {}),
  }), []);

  const normalizedInitial = useMemo(() => normalize(initial), [normalize, initial]);
  const [canvas, setCanvasState] = useState<CanvasConfig>(normalizedInitial);
  const [zoom, setZoomState] = useState<number>(1);
  const [zoomMode, setZoomModeState] = useState<ZoomMode>('auto');
  const lastInitialRef = useRef<string>(JSON.stringify(normalizedInitial));
  const autoZoomRef = useRef<number>(1);

  // Clamp helper
  const clamp = useCallback((val: number, min: number, max: number) => Math.max(min, Math.min(max, val)), []);

  const setCanvas = useCallback((config: CanvasConfig) => {
    const clamped: CanvasConfig = {
      width: clamp(config.width, config.minWidth ?? DEFAULT_CANVAS_CONFIG.minWidth!, config.maxWidth ?? DEFAULT_CANVAS_CONFIG.maxWidth!),
      height: clamp(config.height, config.minHeight ?? DEFAULT_CANVAS_CONFIG.minHeight!, config.maxHeight ?? DEFAULT_CANVAS_CONFIG.maxHeight!),
      minWidth: config.minWidth ?? DEFAULT_CANVAS_CONFIG.minWidth,
      maxWidth: config.maxWidth ?? DEFAULT_CANVAS_CONFIG.maxWidth,
      minHeight: config.minHeight ?? DEFAULT_CANVAS_CONFIG.minHeight,
      maxHeight: config.maxHeight ?? DEFAULT_CANVAS_CONFIG.maxHeight,
    };
    setCanvasState(clamped);
  }, [clamp]);

  const setZoom = useCallback((value: number | ((prev: number) => number)) => {
    setZoomState(prev => {
      const next = typeof value === 'function' ? value(prev) : value;
      const candidate = Number.isFinite(next) ? (next as number) : 1;
      return clamp(candidate, ZOOM_LIMITS.min, ZOOM_LIMITS.max);
    });
  }, [clamp]);

  const zoomIn = useCallback(() => {
    setZoomModeState('manual');
    setZoom(prev => prev + ZOOM_LIMITS.step);
  }, [setZoom]);

  const zoomOut = useCallback(() => {
    setZoomModeState('manual');
    setZoom(prev => prev - ZOOM_LIMITS.step);
  }, [setZoom]);

  const setZoomMode = useCallback((mode: ZoomMode) => {
    setZoomModeState(mode);
  }, []);

  const applyAutoZoom = useCallback((value: number) => {
    const next = clamp(value, ZOOM_LIMITS.min, ZOOM_LIMITS.max);
    autoZoomRef.current = next;
    if (zoomMode === 'auto') {
      setZoomState(next);
    }
  }, [clamp, zoomMode]);

  useEffect(() => {
    const serialized = JSON.stringify(normalizedInitial);
    if (lastInitialRef.current !== serialized) {
      lastInitialRef.current = serialized;
      setCanvasState(JSON.parse(serialized));
      setZoomState(1);
      autoZoomRef.current = 1;
      setZoomModeState('auto');
    }
  }, [normalizedInitial]);

  useEffect(() => {
    if (zoomMode === 'auto') {
      setZoomState(autoZoomRef.current);
    }
  }, [zoomMode]);

  return useMemo(() => ({
    canvas,
    setCanvas,
    zoom,
    setZoom,
    zoomIn,
    zoomOut,
    zoomMode,
    setZoomMode,
    applyAutoZoom,
  }), [canvas, setCanvas, zoom, setZoom, zoomIn, zoomOut, zoomMode, setZoomMode, applyAutoZoom]);
};
