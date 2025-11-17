import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { RenderMode, PageData, BreakpointConfig, LayoutItem } from '../types';
import { ResponsiveContainer } from './ResponsiveContainer';
import { StudioLayoutEngine } from './StudioLayoutEngine';
import { DisplayLayoutEngine } from './DisplayLayoutEngine';
import { ModeController } from './ModeController';
import { ErrorBoundary } from './ErrorBoundary';
import { PageProvider } from '../contexts/PageContext';
import { usePageLoader } from '../hooks/usePageLoader';
import { useErrorHandler } from '../hooks/useErrorHandler';
// Load renderer styles
import '../styles/index.css';

export interface UnifiedPageRendererProps {
  // Page identification
  pageId?: string;
  route?: string;
  
  // Pre-loaded page data (bypasses internal fetching)
  pageData?: PageData;
  
  // Rendering mode
  mode: RenderMode;
  allowUnpublished?: boolean;
  
  // Responsive configuration
  responsive?: boolean;
  breakpoints?: BreakpointConfig;
  containerQueries?: boolean;
  
  // Performance options
  lazyLoading?: boolean;
  preloadPlugins?: string[];
  studioScale?: number;
  studioCanvasWidth?: number;
  studioCanvasHeight?: number;
  
  // Event handlers
  onModeChange?: (mode: RenderMode) => void;
  onPageLoad?: (page: PageData) => void;
  onLayoutChange?: (layouts: any) => void;
  onItemAdd?: (item: LayoutItem) => void;
  onItemSelect?: (itemId: string | null) => void;
  onItemConfig?: (itemId: string) => void;
  onItemRemove?: (itemId: string) => void;
  onError?: (error: Error) => void;
}

const defaultBreakpoints: BreakpointConfig = {
  breakpoints: {
    mobile: 0,
    tablet: 768,
    desktop: 1024,
    wide: 1440,
    ultrawide: 1920,
  },
  containerQueries: true,
  containerTypes: ['inline-size'],
  fluidTypography: {
    enabled: true,
    minSize: 0.875,
    maxSize: 1.125,
    minViewport: 320,
    maxViewport: 1440,
  },
  adaptiveSpacing: {
    enabled: true,
    baseUnit: 4,
    scaleRatio: 1.25,
  },
};

export const UnifiedPageRenderer: React.FC<UnifiedPageRendererProps> = ({
  pageId,
  route,
  pageData: preloadedPageData,
  mode,
  allowUnpublished = false,
  responsive = true,
  breakpoints = defaultBreakpoints,
  containerQueries = true,
  lazyLoading = true,
  preloadPlugins = [],
  onModeChange,
  onPageLoad,
  onLayoutChange,
  onItemAdd,
  onItemSelect,
  onItemConfig,
  onItemRemove,
  onError,
  studioScale = 1,
  studioCanvasWidth,
  studioCanvasHeight,
}) => {
  // State management
  const [currentMode, setCurrentMode] = useState<RenderMode>(mode);
  const [isLoading, setIsLoading] = useState(!preloadedPageData);
  const [error, setError] = useState<Error | null>(null);

  // Custom hooks - only use pageLoader if no preloaded data is provided
  const { pageData: fetchedPageData, loading: pageLoading, error: pageError } = usePageLoader({
    pageId: preloadedPageData ? undefined : pageId,
    route: preloadedPageData ? undefined : route,
    mode: currentMode,
    allowUnpublished,
  });

  // Use preloaded data if available, otherwise use fetched data
  const pageData = preloadedPageData || fetchedPageData;
  const loading = preloadedPageData ? false : pageLoading;
  const dataError = preloadedPageData ? null : pageError;

  const { handleError, clearError } = useErrorHandler({
    onError,
  });

  // Mode change handler
  const handleModeChange = useCallback((newMode: RenderMode) => {
    setCurrentMode(newMode);
    onModeChange?.(newMode);
  }, [onModeChange]);

  // Context value - MUST be before any conditional returns
  const contextValue = useMemo(() => ({
    pageData: pageData || null,
    mode: currentMode,
    responsive,
    breakpoints,
    containerQueries,
    lazyLoading,
    preloadPlugins,
  }), [
    pageData,
    currentMode,
    responsive,
    breakpoints,
    containerQueries,
    lazyLoading,
    preloadPlugins,
  ]);

  // Page load effect
  useEffect(() => {
    if (pageData && !loading) {
      setIsLoading(false);
      onPageLoad?.(pageData);
    }
  }, [pageData, loading, onPageLoad]);

  // Error handling effect
  useEffect(() => {
    if (dataError) {
      setError(dataError);
      handleError(dataError);
    }
  }, [dataError, handleError]);

  // Loading state
  if (isLoading || loading) {
    return (
      <div className="unified-page-renderer unified-page-renderer--loading">
        <div className="unified-page-renderer__loading-indicator">
          <div className="unified-page-renderer__spinner" />
          <span className="unified-page-renderer__loading-text">
            Loading page...
          </span>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !pageData) {
    return (
      <div className="unified-page-renderer unified-page-renderer--error">
        <div className="unified-page-renderer__error-container">
          <h2 className="unified-page-renderer__error-title">
            Failed to load page
          </h2>
          <p className="unified-page-renderer__error-message">
            {error?.message || 'Page not found or failed to load'}
          </p>
          <button
            className="unified-page-renderer__retry-button"
            onClick={() => {
              setError(null);
              clearError();
              setIsLoading(true);
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const engineKey = pageData?.id || pageId || route;
  const engineCommonProps = {
    layouts: pageData.layouts,
    modules: pageData.modules,
    lazyLoading,
    preloadPlugins,
    pageId: pageData.id || pageId,
    onLayoutChange,
    onItemAdd,
    onItemSelect,
    onItemConfig,
    onItemRemove,
  } as const;


  return (
    <ErrorBoundary
      onError={handleError}
      fallback={
        <div className="unified-page-renderer unified-page-renderer--error">
          <div className="unified-page-renderer__error-container">
            <h2 className="unified-page-renderer__error-title">
              Something went wrong
            </h2>
            <p className="unified-page-renderer__error-message">
              An unexpected error occurred while rendering the page.
            </p>
          </div>
        </div>
      }
    >
      <PageProvider value={contextValue}>
        <div
          className={`unified-page-renderer unified-page-renderer--${currentMode}`}
          data-testid="unified-page-renderer"
          data-page-id={pageData?.id || 'unknown'}
          data-mode={currentMode}
        >
          {pageData && (
            <>
              <ModeController
                mode={currentMode}
                onModeChange={handleModeChange}
                pageData={pageData}
              />
              
              {responsive ? (
                <ResponsiveContainer
                  breakpoints={breakpoints}
                  containerQueries={containerQueries}
                >
                  {currentMode === RenderMode.STUDIO ? (
                    <StudioLayoutEngine
                      key={engineKey}
                      {...engineCommonProps}
                      canvasScale={studioScale}
                      canvasWidth={studioCanvasWidth}
                      canvasHeight={studioCanvasHeight}
                    />
                  ) : (
                    <DisplayLayoutEngine key={engineKey} {...engineCommonProps} mode={currentMode} />
                  )}
                </ResponsiveContainer>
              ) : (
                currentMode === RenderMode.STUDIO ? (
                  <StudioLayoutEngine
                    key={engineKey}
                    {...engineCommonProps}
                    canvasScale={studioScale}
                    canvasWidth={studioCanvasWidth}
                    canvasHeight={studioCanvasHeight}
                  />
                ) : (
                  <DisplayLayoutEngine key={engineKey} {...engineCommonProps} mode={currentMode} />
                )
              )}
            </>
          )}
        </div>
      </PageProvider>
    </ErrorBoundary>
  );
};

export default UnifiedPageRenderer;
