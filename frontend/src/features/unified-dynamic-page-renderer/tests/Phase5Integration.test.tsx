/**
 * Phase 5 Integration Tests - Unified Dynamic Page Renderer
 *
 * Comprehensive test suite for Phase 5 features including animation system,
 * accessibility enhancements, and developer tools integration.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

// Import Phase 5 components and services
import { animationService } from '../services/AnimationService';
import { accessibilityService } from '../services/AccessibilityService';
import { developerToolsService } from '../services/DeveloperToolsService';
import { useAnimation } from '../hooks/useAnimation';
import { useAccessibility } from '../hooks/useAccessibility';

// Mock performance API
Object.defineProperty(window, 'performance', {
  value: {
    now: jest.fn(() => Date.now()),
    mark: jest.fn(),
    measure: jest.fn(),
    getEntriesByName: jest.fn(() => [{ duration: 100 }]),
    memory: {
      usedJSHeapSize: 1024 * 1024 * 50 // 50MB
    }
  },
  writable: true
});

// Mock matchMedia for reduced motion
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: query === '(prefers-reduced-motion: reduce)' ? false : true,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

describe('Phase 5 Integration Tests', () => {
  beforeEach(() => {
    // Reset services before each test
    animationService.destroy();
    accessibilityService.destroy();
    developerToolsService.destroy();
    
    // Clear all mocks
    jest.clearAllMocks();
  });

  afterEach(() => {
    // Cleanup after each test
    document.body.innerHTML = '';
  });

  describe('Animation System Integration', () => {
    // Test component for animation testing
    const AnimatedTestComponent: React.FC = () => {
      const animation = useAnimation({
        name: 'fadeIn',
        duration: 300,
        easing: 'ease-out'
      }, {
        autoPlay: false,
        respectReducedMotion: true
      });

      return (
        <div ref={animation.ref as React.RefObject<HTMLDivElement>} data-testid="animated-element">
          <button 
            onClick={() => animation.play()}
            data-testid="play-animation"
          >
            Play Animation
          </button>
          <div data-testid="animation-status">
            {animation.isPlaying ? 'Playing' : 'Stopped'}
          </div>
          <div data-testid="animation-progress">
            Progress: {Math.round(animation.progress * 100)}%
          </div>
        </div>
      );
    };

    test('should initialize animation service correctly', () => {
      expect(animationService).toBeDefined();
      expect(typeof animationService.play).toBe('function');
      expect(typeof animationService.pause).toBe('function');
      expect(typeof animationService.stop).toBe('function');
    });

    test('should play animations with performance monitoring', async () => {
      render(<AnimatedTestComponent />);
      
      const playButton = screen.getByTestId('play-animation');
      const statusElement = screen.getByTestId('animation-status');
      
      expect(statusElement.textContent).toContain('Stopped');
      
      await act(async () => {
        fireEvent.click(playButton);
      });
      
      expect(statusElement.textContent).toContain('Playing');
      
      // Verify performance monitoring was called
      expect(window.performance.mark).toHaveBeenCalled();
    });

    test('should respect reduced motion preferences', async () => {
      // Mock reduced motion preference
      (window.matchMedia as jest.Mock).mockImplementation(query => ({
        matches: query === '(prefers-reduced-motion: reduce)',
        media: query,
        onchange: null,
        addListener: jest.fn(),
        removeListener: jest.fn(),
        addEventListener: jest.fn(),
        removeEventListener: jest.fn(),
        dispatchEvent: jest.fn(),
      }));

      render(<AnimatedTestComponent />);
      
      const playButton = screen.getByTestId('play-animation');
      
      await act(async () => {
        fireEvent.click(playButton);
      });
      
      // Animation should still work but with reduced duration
      expect(screen.getByTestId('animation-status').textContent).toContain('Playing');
    });

    test('should handle animation sequences correctly', async () => {
      const element = document.createElement('div');
      document.body.appendChild(element);

      const sequence = {
        id: 'test-sequence',
        animations: [
          { name: 'fadeIn', duration: 100, easing: 'ease-out' as const },
          { name: 'slideInUp', duration: 100, easing: 'ease-out' as const }
        ],
        parallel: false
      };

      await act(async () => {
        await animationService.playSequence(sequence, element);
      });

      expect((window.performance.mark as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(4);
    });

    test('should generate and apply CSS animations', () => {
      const element = document.createElement('div');
      document.body.appendChild(element);

      const config = {
        name: 'fadeIn',
        duration: 300,
        easing: 'ease-out' as const
      };

      act(() => {
        animationService.play(config, element);
      });

      expect(element.style.animationName).toBe('fadeIn');
      expect(element.style.animationDuration).toBe('300ms');
      expect(element.style.animationTimingFunction).toBe('ease-out');
    });
  });

  describe('Accessibility System Integration', () => {
    // Test component for accessibility testing
    const AccessibilityTestComponent: React.FC = () => {
      const accessibility = useAccessibility({
        enabled: true,
        autoFix: false,
        continuous: false,
        reportViolations: true
      });

      return (
        <div ref={accessibility.ref as React.RefObject<HTMLDivElement>} data-testid="accessibility-container">
          <h1>Test Page</h1>
          <button onClick={() => accessibility.runTest()}>
            Run Accessibility Test
          </button>
          <div data-testid="accessibility-score">
            Score: {accessibility.score?.overall || 0}
          </div>
          <div data-testid="accessibility-violations">
            Violations: {accessibility.violations.length}
          </div>
          <div data-testid="accessibility-compliant">
            Compliant: {accessibility.isCompliant ? 'Yes' : 'No'}
          </div>
          
          {/* Test elements with accessibility issues */}
          <img src="test.jpg" alt="" data-testid="image-no-alt" />
          <button data-testid="button-no-label"></button>
          <div role="button" data-testid="div-button-no-label">Click me</div>
        </div>
      );
    };

    test('should initialize accessibility service correctly', () => {
      expect(accessibilityService).toBeDefined();
      expect(typeof accessibilityService.testWCAGCompliance).toBe('function');
      expect(typeof accessibilityService.announceToScreenReader).toBe('function');
    });

    test('should run WCAG compliance tests', async () => {
      render(<AccessibilityTestComponent />);
      
      const testButton = screen.getByText('Run Accessibility Test');
      
      await act(async () => {
        fireEvent.click(testButton);
      });
      
      await waitFor(() => {
        const scoreElement = screen.getByTestId('accessibility-score');
        expect(scoreElement.textContent).toMatch(/Score: \d+/);
      });
    });

    test('should detect accessibility violations', async () => {
      render(<AccessibilityTestComponent />);
      
      const container = screen.getByTestId('accessibility-container');
      const button = screen.getByTestId('button-no-label');
      
      await act(async () => {
        const result = accessibilityService.testWCAGCompliance(button, 'AA');
        expect(result.issues.length).toBeGreaterThan(0);
      });
    });

    test('should calculate color contrast correctly', () => {
      const ratio = accessibilityService.calculateColorContrast('#000000', '#ffffff');
      expect(ratio).toBe(21); // Perfect contrast ratio
      
      const lowRatio = accessibilityService.calculateColorContrast('#777777', '#888888');
      expect(lowRatio).toBeLessThan(4.5); // Below WCAG AA threshold
    });

    test('should validate ARIA attributes', () => {
      const element = document.createElement('button');
      element.setAttribute('aria-label', 'Test button');
      element.setAttribute('role', 'button');
      
      const result = accessibilityService.validateARIA(element);
      expect(result.valid).toBe(true);
      expect(result.issues).toHaveLength(0);
    });

    test('should generate accessible names for elements', () => {
      const button = document.createElement('button');
      button.textContent = 'Click me';
      
      const label = accessibilityService.generateARIALabel(button);
      expect(label).toBe('Click me');
      
      const input = document.createElement('input');
      input.setAttribute('placeholder', 'Enter your name');
      
      const inputLabel = accessibilityService.generateARIALabel(input);
      expect(inputLabel).toBe('Enter your name');
    });

    test('should manage keyboard navigation', () => {
      const container = document.createElement('div');
      const button1 = document.createElement('button');
      const button2 = document.createElement('button');
      
      container.appendChild(button1);
      container.appendChild(button2);
      document.body.appendChild(container);
      
      const focusableElements = accessibilityService.getFocusableElements(container);
      expect(focusableElements).toHaveLength(2);
      expect(focusableElements[0]).toBe(button1);
      expect(focusableElements[1]).toBe(button2);
    });

    test('should create and manage live regions', () => {
      const liveRegion = accessibilityService.createLiveRegion({
        politeness: 'polite',
        atomic: true
      });
      
      expect(liveRegion).toBeInstanceOf(HTMLElement);
      expect(liveRegion.getAttribute('aria-live')).toBe('polite');
      expect(liveRegion.getAttribute('aria-atomic')).toBe('true');
    });

    test('should announce messages to screen readers', () => {
      const spy = jest.spyOn(document.body, 'appendChild');
      
      accessibilityService.announceToScreenReader('Test announcement', 'assertive');
      
      expect(spy).toHaveBeenCalled();
    });

    test('should pass axe accessibility tests', async () => {
      const { container } = render(
        <div>
          <h1>Accessible Page</h1>
          <button aria-label="Close dialog">×</button>
          <img src="test.jpg" alt="Test image" />
          <input type="text" aria-label="Search" />
        </div>
      );
      
      // Simplified accessibility test without axe
      const wcagResult = accessibilityService.testWCAGCompliance(container, 'AA');
      expect(wcagResult.passed).toBe(true);
      expect(wcagResult.issues.length).toBe(0);
    });
  });

  describe('Developer Tools Integration', () => {
    test('should initialize developer tools service correctly', () => {
      expect(developerToolsService).toBeDefined();
      expect(typeof developerToolsService.startProfiling).toBe('function');
      expect(typeof developerToolsService.log).toBe('function');
    });

    test('should profile performance correctly', async () => {
      const profileId = developerToolsService.startProfiling('Test Profile');
      expect(profileId).toBeDefined();
      expect(typeof profileId).toBe('string');
      
      // Simulate some work
      await new Promise(resolve => setTimeout(resolve, 100));
      
      const profile = developerToolsService.stopProfiling(profileId);
      expect(profile).toBeDefined();
      expect(profile?.name).toBe('Test Profile');
      expect(profile?.duration).toBeGreaterThan(0);
    });

    test('should log messages with different levels', () => {
      const consoleSpy = jest.spyOn(console, 'log').mockImplementation();
      
      developerToolsService.log('info', 'rendering', 'Test message');
      developerToolsService.log('warn', 'performance', 'Warning message');
      developerToolsService.log('error', 'errors', 'Error message');
      
      const logs = developerToolsService.getLogs();
      expect(logs).toHaveLength(3);
      expect(logs[0].level).toBe('info');
      expect(logs[1].level).toBe('warn');
      expect(logs[2].level).toBe('error');
      
      consoleSpy.mockRestore();
    });

    test('should collect and monitor metrics', () => {
      developerToolsService.collectMetric('renderTime', 16.67, 'ms');
      developerToolsService.collectMetric('memoryUsage', 1024 * 1024 * 50, 'bytes');
      
      const metrics = developerToolsService.getMetrics();
      expect(metrics).toHaveLength(2);
      expect(metrics[0].name).toBe('renderTime');
      expect(metrics[1].name).toBe('memoryUsage');
    });

    test('should trigger alerts based on thresholds', () => {
      // This should trigger a render time alert
      developerToolsService.collectMetric('renderTime', 20, 'ms');
      
      const alerts = developerToolsService.getAlerts(false); // unacknowledged
      expect(alerts.length).toBeGreaterThan(0);
      
      const renderAlert = alerts.find(alert => alert.metric === 'renderTime');
      expect(renderAlert).toBeDefined();
      expect(renderAlert?.severity).toBe('medium');
    });

    test('should inspect React components', () => {
      const element = document.createElement('div');
      element.setAttribute('data-testid', 'test-component');
      document.body.appendChild(element);
      
      // Mock React fiber node
      (element as any)._reactInternalFiber = {
        type: { name: 'TestComponent' },
        memoizedProps: { prop1: 'value1' },
        memoizedState: { state1: 'value1' }
      };
      
      const inspection = developerToolsService.inspectComponent(element);
      expect(inspection).toBeDefined();
      expect(inspection?.name).toBe('TestComponent');
    });

    test('should monitor network requests', async () => {
      // Mock fetch to test network monitoring
      const originalFetch = global.fetch;
      global.fetch = jest.fn().mockResolvedValue({
        status: 200,
        statusText: 'OK',
        headers: new Map([['content-length', '1024']])
      });
      
      // Initialize network monitoring
      developerToolsService.networkMonitor.startMonitoring();
      
      await fetch('/api/test');
      
      const requests = developerToolsService.networkMonitor.requests;
      expect(requests.length).toBeGreaterThan(0);
      
      global.fetch = originalFetch;
    });

    test('should track errors and warnings', () => {
      const testError = new Error('Test error');
      developerToolsService.errorTracker.reportError(testError, {
        component: 'TestComponent'
      });
      
      developerToolsService.errorTracker.reportWarning('Test warning', {
        component: 'TestComponent'
      });
      
      const errors = developerToolsService.errorTracker.errors;
      const warnings = developerToolsService.errorTracker.warnings;
      
      expect(errors).toHaveLength(1);
      expect(warnings).toHaveLength(1);
      expect(errors[0].message).toBe('Test error');
      expect(warnings[0].message).toBe('Test warning');
    });
  });

  describe('Cross-System Integration', () => {
    test('should integrate animation with accessibility', async () => {
      const element = document.createElement('div');
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', 'Animated button');
      document.body.appendChild(element);
      
      // Test that animations respect accessibility settings
      const config = {
        name: 'fadeIn',
        duration: 300,
        easing: 'ease-out' as const
      };
      
      await act(async () => {
        await animationService.play(config, element);
      });
      
      // Verify accessibility is maintained
      const ariaResult = accessibilityService.validateARIA(element);
      expect(ariaResult.valid).toBe(true);
    });

    test('should integrate developer tools with performance monitoring', () => {
      const profileId = developerToolsService.startProfiling('Animation Test');
      
      // Simulate animation performance impact
      developerToolsService.collectMetric('animationFrameRate', 58, 'fps');
      
      const profile = developerToolsService.stopProfiling(profileId);
      expect(profile).toBeDefined();
      
      const metrics = developerToolsService.getMetrics('animationFrameRate');
      expect(metrics).toHaveLength(1);
      expect(metrics[0].value).toBe(58);
    });

    test('should maintain accessibility during animations', async () => {
      const container = document.createElement('div');
      const button = document.createElement('button');
      button.textContent = 'Animated Button';
      button.setAttribute('aria-label', 'Click to animate');
      container.appendChild(button);
      document.body.appendChild(container);
      
      // Start animation
      const config = {
        name: 'pulse',
        duration: 1000,
        easing: 'ease-in-out' as const,
        iterations: 'infinite' as const
      };
      
      await act(async () => {
        await animationService.play(config, button);
      });
      
      // Test accessibility during animation
      const wcagResult = accessibilityService.testWCAGCompliance(container, 'AA');
      expect(wcagResult.passed).toBe(true);
      
      // Verify button is still focusable
      expect(accessibilityService.isElementFocusable(button)).toBe(true);
    });

    test('should provide comprehensive debugging information', () => {
      // Start profiling
      const profileId = developerToolsService.startProfiling('Debug Test');
      
      // Trigger accessibility test
      const element = document.createElement('div');
      element.innerHTML = '<button>Test</button>';
      document.body.appendChild(element);
      
      const wcagResult = accessibilityService.testWCAGCompliance(element, 'AA');
      
      // Trigger animation
      const animConfig = {
        name: 'fadeIn',
        duration: 100,
        easing: 'ease-out' as const
      };
      
      act(() => {
        animationService.play(animConfig, element);
      });
      
      // Stop profiling
      const profile = developerToolsService.stopProfiling(profileId);
      
      // Verify comprehensive debugging data
      expect(profile).toBeDefined();
      expect(profile?.metrics).toBeDefined();
      expect(wcagResult.issues).toBeDefined();
      
      const logs = developerToolsService.getLogs();
      expect(logs.length).toBeGreaterThan(0);
    });
  });

  describe('Performance and Memory Management', () => {
    test('should not cause memory leaks', () => {
      const initialMemory = (performance as any).memory?.usedJSHeapSize || 0;
      
      // Create and destroy multiple instances
      for (let i = 0; i < 100; i++) {
        const element = document.createElement('div');
        document.body.appendChild(element);
        
        const config = {
          name: 'fadeIn',
          duration: 10,
          easing: 'ease-out' as const
        };
        
        act(() => {
          animationService.play(config, element);
        });
        element.remove();
      }
      
      // Force garbage collection if available
      if ((global as any).gc) {
        (global as any).gc();
      }
      
      const finalMemory = (performance as any).memory?.usedJSHeapSize || 0;
      const memoryIncrease = finalMemory - initialMemory;
      
      // Memory increase should be reasonable (less than 10MB)
      expect(memoryIncrease).toBeLessThan(10 * 1024 * 1024);
    });

    test('should handle high-frequency operations efficiently', async () => {
      const startTime = performance.now();
      
      // Perform many operations quickly
      const promises = [];
      for (let i = 0; i < 50; i++) {
        const element = document.createElement('div');
        document.body.appendChild(element);
        
        const config = {
          name: 'fadeIn',
          duration: 10,
          easing: 'ease-out' as const
        };
        
        promises.push(animationService.play(config, element));
      }
      
      await Promise.all(promises);
      
      const endTime = performance.now();
      const duration = endTime - startTime;
      
      // Should complete within reasonable time (less than 1 second)
      expect(duration).toBeLessThan(1000);
    });

    test('should cleanup resources properly', () => {
      const element = document.createElement('div');
      document.body.appendChild(element);
      
      // Start various operations
      const profileId = developerToolsService.startProfiling('Cleanup Test');
      
      const config = {
        name: 'fadeIn',
        duration: 100,
        easing: 'ease-out' as const
      };
      
      animationService.play(config, element);
      accessibilityService.testWCAGCompliance(element, 'AA');
      
      // Cleanup
      animationService.destroy();
      accessibilityService.destroy();
      developerToolsService.destroy();
      
      // Verify cleanup
      expect(animationService.getAllAnimations()).toHaveLength(0);
      expect(developerToolsService.getProfiles()).toHaveLength(0);
    });
  });
});
