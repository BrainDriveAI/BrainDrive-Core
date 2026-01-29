import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';

// Components
import { PublishedModeController } from '../components/PublishedModeController';
import { usePublishedMode, useSEO, useAnalytics, usePerformanceMonitoring } from '../hooks/usePublishedMode';

// Services
import { SEOService } from '../services/SEOService';
import { AnalyticsService } from '../services/AnalyticsService';
import { CacheManager } from '../services/CacheManager';
import { PerformanceOptimizer } from '../services/PerformanceOptimizer';

// Types
import {
  PublishedModeConfig,
  PublishedModeEvents,
  PublishedPageData,
  InteractionEvent,
  PublishedModeError
} from '../types/published';
import { PageData } from '../types/core';

// Mock data
const mockPageData: PageData = {
  id: 'test-page-1',
  name: 'Test Page',
  route: '/test-page',
  layouts: {
    mobile: [],
    tablet: [],
    desktop: []
  },
  modules: [],
  metadata: {
    title: 'Test Page Title',
    description: 'This is a test page description for SEO testing purposes.',
    keywords: ['test', 'page'],
    ogImage: 'https://example.com/og-image.jpg',
    canonicalUrl: 'https://example.com/test-page',
    author: 'Test Author',
    publishedAt: new Date('2025-01-01'),
    lastModified: new Date('2025-01-15'),
  },
  isPublished: true
};

const mockPublishedPageData: PublishedPageData = {
  id: 'test-page-1',
  name: 'Test Page',
  route: '/test-page',
  publishedLayouts: {
    mobile: [],
    tablet: [],
    desktop: []
  },
  publishedModules: [],
  metadata: {
    title: 'Test Page Title',
    description: 'This is a test page description for SEO testing purposes. It should be long enough to meet SEO requirements.',
    keywords: ['test', 'page', 'seo'],
    ogTitle: 'Test Page OG Title',
    ogDescription: 'Test page Open Graph description',
    ogImage: 'https://example.com/og-image.jpg',
    ogType: 'website',
    canonicalUrl: 'https://example.com/test-page',
    robots: 'index,follow',
    viewport: 'width=device-width, initial-scale=1',
    charset: 'utf-8',
    structuredData: [{
      type: 'WebPage',
      data: {
        name: 'Test Page',
        description: 'Test page description'
      }
    }],
    author: 'Test Author',
    publishedAt: new Date('2025-01-01'),
    lastModified: new Date('2025-01-15'),
    version: '1.0.0'
  },
  publicationStatus: {
    isPublished: true,
    publishedAt: new Date('2025-01-01'),
    version: '1.0.0',
    status: 'published',
    validationErrors: [],
    seoScore: 85,
    seoIssues: []
  }
};

const mockConfig: PublishedModeConfig = {
  readOnly: true,
  hideUnpublished: true,
  validatePublication: true,
  enableCaching: true,
  cacheStrategy: 'aggressive',
  preloadCritical: true,
  enableSEO: true,
  generateSitemap: true,
  structuredData: true,
  enableAnalytics: true,
  trackPageViews: true,
  trackInteractions: true
};

const mockEvents: PublishedModeEvents = {
  onPageLoad: jest.fn(),
  onPageView: jest.fn(),
  onInteraction: jest.fn(),
  onError: jest.fn(),
  onPerformanceMetric: jest.fn()
};

// Mock implementations
jest.mock('../hooks/usePageLoader', () => ({
  usePageLoader: jest.fn(() => ({
    pageData: mockPageData,
    loading: false,
    error: null
  }))
}));

jest.mock('../hooks/useErrorHandler', () => ({
  useErrorHandler: jest.fn(() => ({
    handleError: jest.fn()
  }))
}));

// Test component that uses published mode hooks
const TestComponent: React.FC = () => {
  const publishedMode = usePublishedMode();
  const seo = useSEO();
  const analytics = useAnalytics();
  const performance = usePerformanceMonitoring();

  return (
    <div data-testid="test-component">
      <div data-testid="published-mode-status">
        {publishedMode.isPublishedMode ? 'Published' : 'Not Published'}
      </div>
      <div data-testid="page-title">{publishedMode.pageMetadata?.title}</div>
      <div data-testid="seo-score">{seo.seoScore}</div>
      <div data-testid="seo-grade">{seo.getSEOGrade()}</div>
      <div data-testid="analytics-enabled">
        {analytics.analyticsEnabled ? 'Enabled' : 'Disabled'}
      </div>
      <div data-testid="performance-grade">{performance.getPerformanceGrade()}</div>
      <button 
        data-testid="track-click-button"
        onClick={() => analytics.trackClick('test-button', 'test-module')}
      >
        Track Click
      </button>
    </div>
  );
};

describe('Phase 4: Published Mode & Performance Integration Tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Mock performance API
    Object.defineProperty(window, 'performance', {
      value: {
        now: jest.fn(() => 1000),
        getEntriesByType: jest.fn(() => []),
        mark: jest.fn(),
        measure: jest.fn()
      }
    });
  });

  describe('PublishedModeController', () => {
    it('should render published mode controller with correct props', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(screen.getByTestId('published-mode-status')).toHaveTextContent('Published');
        expect(screen.getByTestId('page-title')).toHaveTextContent('Test Page Title');
      });
    });

    it('should track page load event', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(mockEvents.onPageLoad).toHaveBeenCalledWith(
          expect.objectContaining({
            id: 'test-page-1',
            name: 'Test Page'
          })
        );
      });
    });

    it('should track page view event', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(mockEvents.onPageView).toHaveBeenCalledWith(
          'test-page-1',
          expect.objectContaining({
            title: 'Test Page Title'
          })
        );
      });
    });

    it('should handle interaction tracking', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(screen.getByTestId('track-click-button')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('track-click-button'));

      await waitFor(() => {
        expect(mockEvents.onInteraction).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'click',
            target: 'test-button',
            moduleId: 'test-module'
          })
        );
      });
    });
  });

  describe('SEO Service', () => {
    let seoService: SEOService;

    beforeEach(() => {
      seoService = new SEOService();
    });

    it('should generate meta tags correctly', () => {
      const metaTags = seoService.generateMetaTags(mockPublishedPageData.metadata);
      
      expect(metaTags).toContain('<title>Test Page Title</title>');
      expect(metaTags).toContain('name="description" content="This is a test page description');
      expect(metaTags).toContain('name="keywords" content="test, page, seo"');
      expect(metaTags).toContain('property="og:title" content="Test Page OG Title"');
      expect(metaTags).toContain('property="og:image" content="https://example.com/og-image.jpg"');
    });

    it('should generate structured data correctly', () => {
      const structuredData = seoService.generateStructuredData(mockPublishedPageData.metadata.structuredData || []);
      
      expect(structuredData).toContain('<script type="application/ld+json">');
      expect(structuredData).toContain('"@type": "WebPage"');
      expect(structuredData).toContain('"name": "Test Page"');
    });

    it('should validate SEO and return issues', () => {
      const issues = seoService.validateSEO(mockPublishedPageData);
      
      // Should have minimal issues for well-formed metadata
      expect(issues.length).toBeLessThan(3);
      
      // Test with incomplete metadata
      const incompletePageData = {
        ...mockPublishedPageData,
        metadata: {
          ...mockPublishedPageData.metadata,
          title: '', // Missing title should create critical issue
          description: ''
        }
      };
      
      const incompleteIssues = seoService.validateSEO(incompletePageData);
      expect(incompleteIssues.some(issue => issue.severity === 'critical')).toBe(true);
    });

    it('should generate sitemap correctly', () => {
      const sitemap = seoService.generateSitemap([mockPublishedPageData]);
      
      expect(sitemap).toContain('<?xml version="1.0" encoding="UTF-8"?>');
      expect(sitemap).toContain('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
      expect(sitemap).toContain('<loc>https://example.com/test-page</loc>');
      expect(sitemap).toContain('<changefreq>');
      expect(sitemap).toContain('<priority>');
    });
  });

  describe('Analytics Service', () => {
    let analyticsService: AnalyticsService;

    beforeEach(() => {
      analyticsService = new AnalyticsService({
        enabled: true,
        enableDebug: false
      });
    });

    it('should track page views', () => {
      analyticsService.trackPageView('test-page-1', mockPageData.metadata);
      
      const analytics = analyticsService.getAnalytics();
      expect(analytics.pageViews).toBe(1);
    });

    it('should track interactions', () => {
      const interaction: InteractionEvent = {
        type: 'click',
        target: 'button',
        moduleId: 'test-module',
        timestamp: new Date(),
        metadata: { test: 'data' }
      };
      
      analyticsService.trackInteraction(interaction);
      
      const analytics = analyticsService.getAnalytics();
      expect(analytics.interactions).toHaveLength(1);
      expect(analytics.interactions[0]).toMatchObject(interaction);
    });

    it('should track performance metrics', () => {
      const metrics = {
        loadTime: 1500,
        firstContentfulPaint: 800,
        largestContentfulPaint: 1200,
        firstInputDelay: 50,
        cumulativeLayoutShift: 0.05,
        timeToInteractive: 2000,
        bundleSize: 500000,
        imageSize: 200000,
        requestCount: 25,
        cacheHitRate: 0.8,
        cachedResources: 20,
        mobileScore: 85,
        mobileFriendly: true
      };
      
      analyticsService.trackPerformance(metrics);
      
      const analytics = analyticsService.getAnalytics();
      expect(analytics.performanceMetrics).toMatchObject(metrics);
    });

    it('should track errors', () => {
      const error: PublishedModeError = {
        type: 'load',
        code: 'PAGE_LOAD_FAILED',
        message: 'Failed to load page',
        pageId: 'test-page-1',
        timestamp: new Date()
      };
      
      analyticsService.trackError(error);
      
      const analytics = analyticsService.getAnalytics();
      expect(analytics.errors).toHaveLength(1);
      expect(analytics.errors[0]).toMatchObject(error);
    });
  });

  describe('Cache Manager', () => {
    let cacheManager: CacheManager;

    beforeEach(() => {
      cacheManager = new CacheManager({
        enabled: true,
        defaultTTL: 3600000,
        enableDebug: false
      });
    });

    it('should set and get values from cache', async () => {
      const testData = { test: 'data', number: 123 };
      
      await cacheManager.set('test-key', testData);
      const retrieved = await cacheManager.get('test-key');
      
      expect(retrieved).toEqual(testData);
    });

    it('should return null for non-existent keys', async () => {
      const result = await cacheManager.get('non-existent-key');
      expect(result).toBeNull();
    });

    it('should delete values from cache', async () => {
      await cacheManager.set('test-key', 'test-value');
      await cacheManager.delete('test-key');
      
      const result = await cacheManager.get('test-key');
      expect(result).toBeNull();
    });

    it('should clear all cache', async () => {
      await cacheManager.set('key1', 'value1');
      await cacheManager.set('key2', 'value2');
      
      await cacheManager.clear();
      
      const result1 = await cacheManager.get('key1');
      const result2 = await cacheManager.get('key2');
      
      expect(result1).toBeNull();
      expect(result2).toBeNull();
    });

    it('should provide cache info', async () => {
      await cacheManager.set('test-key', 'test-value', 5000);
      
      const info = await cacheManager.getInfo('test-key');
      
      expect(info).toMatchObject({
        cached: true,
        cacheKey: 'test-key',
        cacheLevel: 'memory'
      });
    });

    it('should invalidate cache by pattern', async () => {
      await cacheManager.set('user:1:profile', 'profile1');
      await cacheManager.set('user:2:profile', 'profile2');
      await cacheManager.set('page:home', 'homepage');
      
      await cacheManager.invalidate('user:.*:profile');
      
      const profile1 = await cacheManager.get('user:1:profile');
      const profile2 = await cacheManager.get('user:2:profile');
      const homepage = await cacheManager.get('page:home');
      
      expect(profile1).toBeNull();
      expect(profile2).toBeNull();
      expect(homepage).toBe('homepage');
    });
  });

  describe('Performance Optimizer', () => {
    let performanceOptimizer: PerformanceOptimizer;

    beforeEach(() => {
      performanceOptimizer = new PerformanceOptimizer({
        enabled: true,
        performanceMonitoringEnabled: true,
        webVitalsEnabled: true
      });
    });

    it('should start and end timing measurements', () => {
      performanceOptimizer.startTiming('test-operation');
      
      // Mock some time passing
      (window.performance.now as jest.Mock).mockReturnValue(2000);
      
      const duration = performanceOptimizer.endTiming('test-operation');
      expect(duration).toBe(1000); // 2000 - 1000
    });

    it('should get memory usage information', () => {
      // Mock performance.memory
      Object.defineProperty(window.performance, 'memory', {
        value: {
          usedJSHeapSize: 10000000,
          totalJSHeapSize: 20000000,
          jsHeapSizeLimit: 50000000
        }
      });

      const memoryInfo = performanceOptimizer.getMemoryUsage();
      
      expect(memoryInfo).toMatchObject({
        usedJSHeapSize: 10000000,
        totalJSHeapSize: 20000000,
        jsHeapSizeLimit: 50000000
      });
    });

    it('should track custom performance metrics', () => {
      performanceOptimizer.trackMetric('custom-metric', 500, 'ms', { context: 'test' });
      
      // Should not throw and should log if debug enabled
      expect(() => {
        performanceOptimizer.trackMetric('another-metric', 1000, 'ms');
      }).not.toThrow();
    });

    it('should preload critical resources', () => {
      const resources = [
        '/critical.js',
        '/important.css',
        '/hero-image.jpg'
      ];
      
      performanceOptimizer.preloadCriticalResources(resources);
      
      // Check if link elements were added to head
      const preloadLinks = document.querySelectorAll('link[rel="preload"]');
      expect(preloadLinks.length).toBe(resources.length);
    });
  });

  describe('Hooks Integration', () => {
    it('should provide correct SEO data through useSEO hook', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(screen.getByTestId('seo-score')).toHaveTextContent('60');
        expect(screen.getByTestId('seo-grade')).toHaveTextContent('D');
      });
    });

    it('should provide correct analytics status through useAnalytics hook', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(screen.getByTestId('analytics-enabled')).toHaveTextContent('Enabled');
      });
    });

    it('should provide performance monitoring through usePerformanceMonitoring hook', async () => {
      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(screen.getByTestId('performance-grade')).toBeInTheDocument();
      });
    });
  });

  describe('Error Handling', () => {
    it('should handle publication validation errors', async () => {
      const unpublishedPageData = {
        ...mockPageData,
        isPublished: false
      };

      // Mock usePageLoader to return unpublished page
      const mockUsePageLoader = require('../hooks/usePageLoader').usePageLoader;
      mockUsePageLoader.mockReturnValue({
        pageData: unpublishedPageData,
        loading: false,
        error: null
      });

      render(
        <PublishedModeController
          pageId="test-page-1"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(mockEvents.onError).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'validation',
            code: 'PUBLICATION_VALIDATION_FAILED'
          })
        );
      });
    });

    it('should handle page load errors', async () => {
      const mockUsePageLoader = require('../hooks/usePageLoader').usePageLoader;
      mockUsePageLoader.mockReturnValue({
        pageData: null,
        loading: false,
        error: new Error('Page not found')
      });

      render(
        <PublishedModeController
          pageId="non-existent-page"
          config={mockConfig}
          events={mockEvents}
        >
          <TestComponent />
        </PublishedModeController>
      );

      await waitFor(() => {
        expect(mockEvents.onError).toHaveBeenCalledWith(
          expect.objectContaining({
            type: 'load',
            code: 'PAGE_LOAD_FAILED'
          })
        );
      });
    });
  });
});
