// Test setup file for Unified Dynamic Page Renderer
import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';
import { PerformanceObserver, performance } from 'perf_hooks';
import 'fake-indexeddb/auto';

const globalAny = global as typeof global & {
  TextEncoder?: typeof TextEncoder;
  TextDecoder?: typeof TextDecoder;
  fetch?: typeof fetch;
  PerformanceObserver?: typeof PerformanceObserver;
  performance?: typeof performance;
};

if (!globalAny.TextEncoder) {
  globalAny.TextEncoder = TextEncoder;
}

if (!globalAny.TextDecoder) {
  globalAny.TextDecoder = TextDecoder;
}

if (!globalAny.fetch) {
  globalAny.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: async () => ({}),
      text: async () => '',
      headers: new Map(),
    }) as unknown as Response
  );
}

if (!globalAny.performance) {
  globalAny.performance = performance;
}

if (!globalAny.PerformanceObserver) {
  globalAny.PerformanceObserver = PerformanceObserver;
}

// Provide Vite env globals for tests
(globalThis as any).__VITE_ENV__ = {
  MODE: 'test',
  DEV: true,
  PROD: false,
};
(globalThis as any).__IMPORT_META__ = {
  env: (globalThis as any).__VITE_ENV__,
};

// Mock implementations for browser APIs that might not be available in test environment
(global as any).ResizeObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

(global as any).IntersectionObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock CSS.supports for feature detection tests
Object.defineProperty(global, 'CSS', {
  value: {
    supports: jest.fn().mockReturnValue(true),
  },
  writable: true,
});

// Mock matchMedia for responsive tests
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock window dimensions
Object.defineProperty(window, 'innerWidth', {
  writable: true,
  configurable: true,
  value: 1024,
});

Object.defineProperty(window, 'innerHeight', {
  writable: true,
  configurable: true,
  value: 768,
});

// Ensure focusable checks treat elements as visible
Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
  configurable: true,
  get() {
    return this.parentElement;
  },
});

// Auto-resolve animation end events in jsdom
const originalAddEventListener = HTMLElement.prototype.addEventListener;
HTMLElement.prototype.addEventListener = function (
  type: string,
  listener: EventListenerOrEventListenerObject,
  options?: boolean | AddEventListenerOptions
) {
  originalAddEventListener.call(this, type, listener, options);

  if (type === 'animationend') {
    setTimeout(() => {
      try {
        this.dispatchEvent(new Event('animationend'));
      } catch {
        // noop
      }
    }, 50);
  }
};

// Suppress animation cancel errors in tests
try {
  const { animationService } = require('../services/AnimationService');
  const originalPlay = animationService.play.bind(animationService);

  animationService.play = async (...args: unknown[]) => {
    try {
      return await originalPlay(...args);
    } catch (error: any) {
      if (error?.message?.includes('Animation was cancelled')) {
        return;
      }
      throw error;
    }
  };
} catch {
  // Ignore if animation service cannot be loaded in this environment
}

process.on('unhandledRejection', (reason: any) => {
  if (reason?.message?.includes('Animation was cancelled')) {
    return;
  }
  throw reason;
});

export {};
