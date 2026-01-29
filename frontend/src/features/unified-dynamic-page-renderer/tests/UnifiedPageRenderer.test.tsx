import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { UnifiedPageRenderer } from '../components/UnifiedPageRenderer';
import { RenderMode } from '../types';

// Mock the hooks to avoid dependency issues in tests
jest.mock('../hooks/usePageLoader', () => ({
  usePageLoader: () => ({
    pageData: {
      id: 'test-page',
      name: 'Test Page',
      route: '/test',
      layouts: {
        mobile: [],
        tablet: [],
        desktop: [],
      },
      modules: [],
      metadata: {
        title: 'Test Page',
      },
      isPublished: true,
    },
    loading: false,
    error: null,
  }),
}));

jest.mock('../hooks/useErrorHandler', () => ({
  useErrorHandler: () => ({
    error: null,
    handleError: jest.fn(),
    clearError: jest.fn(),
  }),
}));

jest.mock('../../../hooks/usePluginStudioDevMode', () => ({
  usePluginStudioDevMode: () => ({ isPluginStudioDevMode: false }),
}));

jest.mock('../hooks/useFeatureDetection', () => ({
  useFeatureDetection: () => ({
    supportsContainerQueries: true,
    supportsViewportUnits: true,
    supportsClamp: true,
    supportsGrid: true,
    supportsFlexbox: true,
    supportsCustomProperties: true,
    supportsResizeObserver: true,
    supportsIntersectionObserver: true,
    supportsWebP: true,
    supportsAvif: true,
    touchDevice: false,
    reducedMotion: false,
  }),
}));

jest.mock('react-router-dom', () => {
  const actual = jest.requireActual('react-router-dom');
  return {
    ...actual,
    MemoryRouter: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    useLocation: () => ({ pathname: '/test' }),
  };
});

describe('UnifiedPageRenderer', () => {
  const defaultProps = {
    mode: RenderMode.PUBLISHED,
    pageId: 'test-page',
  };

  const renderWithRouter = (ui: React.ReactElement) =>
    render(<MemoryRouter initialEntries={['/test']}>{ui}</MemoryRouter>);

  it('renders without crashing', () => {
    renderWithRouter(<UnifiedPageRenderer {...defaultProps} />);
    expect(screen.getByTestId('unified-page-renderer')).toBeInTheDocument();
  });

  it('displays loading state initially', () => {
    renderWithRouter(<UnifiedPageRenderer {...defaultProps} />);
    // This test would need to be adjusted based on actual loading behavior
  });

  it('handles different render modes', () => {
    const { unmount } = renderWithRouter(
      <UnifiedPageRenderer {...defaultProps} mode={RenderMode.STUDIO} />
    );
    
    expect(screen.getByTestId('unified-page-renderer')).toHaveClass('unified-page-renderer--studio');
    
    unmount();

    renderWithRouter(
      <UnifiedPageRenderer {...defaultProps} mode={RenderMode.PUBLISHED} />
    );
    
    expect(screen.getByTestId('unified-page-renderer')).toHaveClass('unified-page-renderer--published');
  });

  it('handles responsive configuration', () => {
    renderWithRouter(
      <UnifiedPageRenderer 
        {...defaultProps} 
        responsive={true}
        containerQueries={true}
      />
    );
    
    // Test responsive container is rendered
    expect(screen.getByTestId('responsive-container')).toBeInTheDocument();
  });
});
