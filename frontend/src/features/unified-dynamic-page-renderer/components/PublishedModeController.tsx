import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { RenderMode, ModuleConfig } from '../types/core';
import {
  PublishedModeConfig,
  PublishedPageData,
  PublishedModeContext as PublishedModeContextType,
  PublishedModeEvents,
  InteractionEvent,
  PublishedModeError,
  PerformanceMetric,
  PublicationStatus
} from '../types/published';
import { usePageLoader } from '../hooks/usePageLoader';
import { useErrorHandler } from '../hooks/useErrorHandler';

interface PublishedModeControllerProps {
  pageId?: string;
  route?: string;
  config: PublishedModeConfig;
  events: PublishedModeEvents;
  children: React.ReactNode;
}

/**
 * PublishedModeController - Controls published mode rendering with security, SEO, and performance optimizations
 */
export const PublishedModeController: React.FC<PublishedModeControllerProps> = ({
  pageId,
  route,
  config,
  events,
  children
}) => {
  const [currentPage, setCurrentPage] = useState<PublishedPageData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [publicationStatus, setPublicationStatus] = useState<PublicationStatus | null>(null);
  const [performanceMetrics, setPerformanceMetrics] = useState<Record<string, number>>({});

  const { pageData, loading, error } = usePageLoader({
    pageId,
    route,
    mode: RenderMode.PUBLISHED,
    allowUnpublished: false
  });
  const { handleError } = useErrorHandler();

  // Validate publication status
  const validatePublication = useCallback(async (page: PublishedPageData): Promise<boolean> => {
    if (!config.validatePublication) {
      return true;
    }

    try {
      // Check if page is published
      if (!page.publicationStatus.isPublished) {
        throw new Error('Page is not published');
      }

      // Check publication status
      if (page.publicationStatus.status !== 'published') {
        throw new Error(`Page status is ${page.publicationStatus.status}, not published`);
      }

      // Validate publication date
      if (page.publicationStatus.publishedAt && page.publicationStatus.publishedAt > new Date()) {
        throw new Error('Page is scheduled for future publication');
      }

      // Check for validation errors
      const criticalErrors = page.publicationStatus.validationErrors.filter(
        error => error.type === 'error'
      );
      
      if (criticalErrors.length > 0) {
        throw new Error(`Page has validation errors: ${criticalErrors.map(e => e.message).join(', ')}`);
      }

      return true;
    } catch (error) {
      const publishedError: PublishedModeError = {
        type: 'validation',
        code: 'PUBLICATION_VALIDATION_FAILED',
        message: error instanceof Error ? error.message : 'Publication validation failed',
        pageId: page.id,
        timestamp: new Date()
      };

      events.onError(publishedError);
      return false;
    }
  }, [config.validatePublication, events]);

  // Process page data when loaded
  useEffect(() => {
    if (loading) {
      setIsLoading(true);
      return;
    }

    if (error) {
      const publishedError: PublishedModeError = {
        type: 'load',
        code: 'PAGE_LOAD_FAILED',
        message: error.message,
        pageId,
        timestamp: new Date(),
        stack: error.stack
      };
      events.onError(publishedError);
      handleError(error);
      setIsLoading(false);
      return;
    }

    if (!pageData) {
      setIsLoading(false);
      return;
    }

    const processPageData = async () => {
      try {
        // Start performance timing
        const loadStartTime = performance.now();

        // Convert to published page data
        const publishedPage: PublishedPageData = {
          id: pageData.id,
          name: pageData.name,
          route: pageData.route,
          publishedLayouts: pageData.layouts,
          publishedModules: pageData.modules.filter((module: ModuleConfig) => {
            const pluginId = (module as any).pluginId || module.pluginId;
            if (!pluginId || pluginId === 'unknown') {
              console.warn(`[PublishedModeController] Skipping module with missing pluginId:`, module);
              return false;
            }
            return true;
          }).map((module: ModuleConfig) => ({
            moduleId: (module as any).moduleId || `module-${Math.random()}`,
            pluginId: (module as any).pluginId || module.pluginId,
            config: module,
            lazy: module.lazy || false,
            priority: module.priority || 'normal',
            preload: module.preload || false,
            cacheable: true,
            cacheKey: `module-${(module as any).moduleId || Math.random()}`,
            cacheTTL: 3600000 // 1 hour
          })),
          metadata: {
            title: pageData.metadata.title || pageData.name,
            description: pageData.metadata.description || '',
            keywords: pageData.metadata.keywords || [],
            ogTitle: pageData.metadata.ogImage ? pageData.metadata.title : undefined,
            ogDescription: pageData.metadata.description,
            ogImage: pageData.metadata.ogImage,
            ogType: 'website',
            canonicalUrl: pageData.metadata.canonicalUrl,
            robots: pageData.metadata.robots || 'index,follow',
            viewport: 'width=device-width, initial-scale=1',
            charset: 'utf-8',
            structuredData: [],
            author: pageData.metadata.author,
            publishedAt: pageData.metadata.publishedAt || new Date(),
            lastModified: pageData.metadata.lastModified || new Date(),
            version: '1.0.0'
          },
          publicationStatus: {
            isPublished: pageData.isPublished,
            publishedAt: pageData.metadata.publishedAt,
            version: '1.0.0',
            status: pageData.isPublished ? 'published' : 'draft',
            validationErrors: [],
            seoScore: 85,
            seoIssues: []
          }
        };

        // Validate publication if required
        const isValid = await validatePublication(publishedPage);
        if (!isValid) {
          setIsLoading(false);
          return;
        }

        // Calculate load time
        const loadTime = performance.now() - loadStartTime;
        
        // Update performance metrics
        setPerformanceMetrics(prev => ({
          ...prev,
          loadTime
        }));

        // Track performance metric
        const performanceMetric: PerformanceMetric = {
          name: 'page_load_time',
          value: loadTime,
          unit: 'ms',
          timestamp: new Date(),
          context: {
            pageId: publishedPage.id,
            route: publishedPage.route
          }
        };
        events.onPerformanceMetric(performanceMetric);

        // Set current page
        setCurrentPage(publishedPage);
        setPublicationStatus(publishedPage.publicationStatus);

        // Trigger page load event
        events.onPageLoad(publishedPage);

        // Track page view
        events.onPageView(publishedPage.id, publishedPage.metadata);

      } catch (error) {
        const publishedError: PublishedModeError = {
          type: 'load',
          code: 'PAGE_PROCESSING_FAILED',
          message: error instanceof Error ? error.message : 'Failed to process page',
          pageId,
          timestamp: new Date(),
          stack: error instanceof Error ? error.stack : undefined
        };

        events.onError(publishedError);
        handleError(error instanceof Error ? error : new Error('Failed to process page'));
      } finally {
        setIsLoading(false);
      }
    };

    processPageData();
  }, [pageData, loading, error, pageId, validatePublication, events, handleError]);

  // Track user interactions
  const trackInteraction = useCallback((event: React.SyntheticEvent, type: InteractionEvent['type']) => {
    if (!config.trackInteractions || !currentPage) {
      return;
    }

    const target = (event.target || event.currentTarget) as HTMLElement | null;
    if (!target) {
      return;
    }

    const moduleId = target instanceof Element
      ? target.closest('[data-module-id]')?.getAttribute('data-module-id')
      : null;

    const interactionEvent: InteractionEvent = {
      type,
      target: target.tagName.toLowerCase(),
      moduleId: moduleId || undefined,
      timestamp: new Date(),
      metadata: {
        className: target.className,
        id: target.id,
        dataset: { ...target.dataset }
      }
    };

    events.onInteraction(interactionEvent);
  }, [config.trackInteractions, currentPage, events]);

  // Setup interaction tracking
  useEffect(() => {
    if (!config.trackInteractions) {
      return;
    }

    const handleClick = (event: Event) => {
      trackInteraction(event as any, 'click');
    };

    const handleScroll = (event: Event) => {
      trackInteraction(event as any, 'scroll');
    };

    document.addEventListener('click', handleClick);
    document.addEventListener('scroll', handleScroll);

    return () => {
      document.removeEventListener('click', handleClick);
      document.removeEventListener('scroll', handleScroll);
    };
  }, [config.trackInteractions, trackInteraction]);

  // Create context value
  const contextValue: PublishedModeContextType = useMemo(() => ({
    isPublishedMode: true,
    currentPage: currentPage || undefined,
    config,
    events
  }), [currentPage, config, events]);

  // Show loading state
  if (isLoading) {
    return (
      <div className="published-mode-loading" role="status" aria-label="Loading page">
        <div className="loading-spinner" />
        <span className="loading-text">Loading...</span>
      </div>
    );
  }

  // Show error state if page failed to load
  if (!currentPage) {
    return (
      <div className="published-mode-error" role="alert">
        <h1>Page Not Found</h1>
        <p>The requested page could not be found or is not published.</p>
      </div>
    );
  }

  // Show unpublished warning if configured
  if (config.hideUnpublished && !currentPage.publicationStatus.isPublished) {
    return (
      <div className="published-mode-unpublished" role="alert">
        <h1>Page Not Available</h1>
        <p>This page is not currently published.</p>
      </div>
    );
  }

  return (
    <PublishedModeContext.Provider value={contextValue}>
      <div 
        className="published-mode-container"
        data-page-id={currentPage.id}
        data-page-route={currentPage.route}
        data-publication-status={currentPage.publicationStatus.status}
      >
        {/* SEO Meta Tags - handled by SEOService */}
        <PublishedModeSEOHead metadata={currentPage.metadata} />
        
        {/* Performance Monitoring */}
        {config.enableAnalytics && (
          <PublishedModePerformanceMonitor 
            pageId={currentPage.id}
            onMetric={events.onPerformanceMetric}
          />
        )}
        
        {/* Main Content */}
        <main className="published-content" role="main">
          {children}
        </main>
        
        {/* Analytics Tracking */}
        {config.enableAnalytics && (
          <PublishedModeAnalytics 
            pageId={currentPage.id}
            metadata={currentPage.metadata}
            onInteraction={events.onInteraction}
          />
        )}
      </div>
    </PublishedModeContext.Provider>
  );
};

// Context for published mode
export const PublishedModeContext = React.createContext<PublishedModeContextType | null>(null);

// Hook to use published mode context
export const usePublishedMode = () => {
  const context = React.useContext(PublishedModeContext);
  if (!context) {
    throw new Error('usePublishedMode must be used within a PublishedModeController');
  }
  return context;
};

// SEO Head component for meta tags
const PublishedModeSEOHead: React.FC<{ metadata: PublishedPageData['metadata'] }> = ({ metadata }) => {
  useEffect(() => {
    // Update document title
    document.title = metadata.title;

    // Update meta tags
    const updateMetaTag = (name: string, content: string) => {
      let meta = document.querySelector(`meta[name="${name}"]`) as HTMLMetaElement;
      if (!meta) {
        meta = document.createElement('meta');
        meta.name = name;
        document.head.appendChild(meta);
      }
      meta.content = content;
    };

    const updatePropertyTag = (property: string, content: string) => {
      let meta = document.querySelector(`meta[property="${property}"]`) as HTMLMetaElement;
      if (!meta) {
        meta = document.createElement('meta');
        meta.setAttribute('property', property);
        document.head.appendChild(meta);
      }
      meta.content = content;
    };

    // Basic meta tags
    updateMetaTag('description', metadata.description);
    updateMetaTag('keywords', metadata.keywords.join(', '));
    updateMetaTag('author', metadata.author || '');
    updateMetaTag('robots', metadata.robots || 'index,follow');
    updateMetaTag('viewport', metadata.viewport || 'width=device-width, initial-scale=1');

    // Open Graph tags
    if (metadata.ogTitle) updatePropertyTag('og:title', metadata.ogTitle);
    if (metadata.ogDescription) updatePropertyTag('og:description', metadata.ogDescription);
    if (metadata.ogImage) updatePropertyTag('og:image', metadata.ogImage);
    if (metadata.ogType) updatePropertyTag('og:type', metadata.ogType);

    // Twitter Card tags
    if (metadata.twitterCard) updateMetaTag('twitter:card', metadata.twitterCard);
    if (metadata.twitterTitle) updateMetaTag('twitter:title', metadata.twitterTitle);
    if (metadata.twitterDescription) updateMetaTag('twitter:description', metadata.twitterDescription);
    if (metadata.twitterImage) updateMetaTag('twitter:image', metadata.twitterImage);

    // Canonical URL
    if (metadata.canonicalUrl) {
      let link = document.querySelector('link[rel="canonical"]') as HTMLLinkElement;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'canonical';
        document.head.appendChild(link);
      }
      link.href = metadata.canonicalUrl;
    }

  }, [metadata]);

  return null;
};

// Performance monitoring component
const PublishedModePerformanceMonitor: React.FC<{
  pageId: string;
  onMetric: (metric: PerformanceMetric) => void;
}> = ({ pageId, onMetric }) => {
  useEffect(() => {
    // Monitor Web Vitals
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const metric: PerformanceMetric = {
          name: entry.name,
          value: entry.duration || (entry as any).value || 0,
          unit: 'ms',
          timestamp: new Date(),
          context: { pageId, entryType: entry.entryType }
        };
        onMetric(metric);
      }
    });

    observer.observe({ entryTypes: ['navigation', 'paint', 'largest-contentful-paint'] });

    return () => observer.disconnect();
  }, [pageId, onMetric]);

  return null;
};

// Analytics tracking component
const PublishedModeAnalytics: React.FC<{
  pageId: string;
  metadata: PublishedPageData['metadata'];
  onInteraction: (event: InteractionEvent) => void;
}> = ({ pageId, metadata, onInteraction }) => {
  useEffect(() => {
    // Track page view on mount
    const pageViewEvent: InteractionEvent = {
      type: 'scroll', // Using scroll as a proxy for page view
      target: 'page',
      timestamp: new Date(),
      metadata: {
        pageId,
        title: metadata.title,
        route: window.location.pathname
      }
    };
    onInteraction(pageViewEvent);
  }, [pageId, metadata, onInteraction]);

  return null;
};

export default PublishedModeController;
