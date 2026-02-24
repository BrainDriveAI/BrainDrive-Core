import { createBrowserRouter, Navigate, RouteObject } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import ErrorBoundary from '../components/ErrorBoundary';
import { useAuth } from '../contexts/AuthContext';

// Lazy load components for better performance
const Login = lazy(() => import('../pages/Login'));
const Dashboard = lazy(() => import('../pages/Dashboard'));
const PluginStudio = lazy(() => import('../pages/PluginStudio'));
const Settings = lazy(() => import('../pages/Settings'));
const DashboardLayout = lazy(() => import('../components/dashboard/DashboardLayout'));

// Loading component for suspense fallback
const LoadingFallback = () => (
  <div style={{ 
    display: 'flex', 
    justifyContent: 'center', 
    alignItems: 'center', 
    height: '100vh' 
  }}>
    Loading...
  </div>
);

// Auth guard HOC
const AuthGuard = ({ children }: { children: React.ReactNode }) => {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
};

// Public routes (no auth required)
const publicRoutes: RouteObject[] = [
  {
    path: 'login',
    element: (
      <Suspense fallback={<LoadingFallback />}>
        <Login />
      </Suspense>
    ),
    errorElement: <ErrorBoundary />
  },
];

// Protected routes (auth required)
const protectedRoutes: RouteObject[] = [
  {
    path: '/',
    element: (
      <AuthGuard>
        <Suspense fallback={<LoadingFallback />}>
          <DashboardLayout />
        </Suspense>
      </AuthGuard>
    ),
    errorElement: <ErrorBoundary />,
    children: [
      {
        path: '/',
        element: <Navigate to="/dashboard" replace />,
      },
      {
        path: 'dashboard',
        element: (
          <Suspense fallback={<LoadingFallback />}>
            <Dashboard />
          </Suspense>
        ),
      },
      {
        path: 'plugin-studio',
        element: (
          <Suspense fallback={<LoadingFallback />}>
            <PluginStudio />
          </Suspense>
        ),
      },
      {
        path: 'settings',
        element: (
          <Suspense fallback={<LoadingFallback />}>
            <Settings />
          </Suspense>
        ),
      },
    ],
  },
];

// Combine all routes
const router = createBrowserRouter([
  ...publicRoutes,
  ...protectedRoutes,
  {
    path: '*',
    element: <ErrorBoundary />,
  },
]);

export default router;
