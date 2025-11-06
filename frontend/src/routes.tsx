import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import Login from './pages/Login';
import { PluginStudioPage } from './features/plugin-studio';
import { PluginInstallerPage } from './features/plugin-installer';
import DashboardLayout from './components/dashboard/DashboardLayout';
import ModuleDetailPage from './pages/ModuleDetailPage';
import ProfilePage from './pages/ProfilePage';
import TasksPage from './pages/TasksPage';
import PersonasPage from './pages/PersonasPage';
import PersonaFormPage from './pages/PersonaFormPage';
import { DynamicRoutes } from './components/DynamicRoutes';
import { DynamicPageRenderer } from './components/DynamicPageRenderer';
import { RouteContentRenderer } from './components/RouteContentRenderer';
import DefaultPageRedirect from './components/DefaultPageRedirect';

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  
  if (!isAuthenticated) {
    return <Navigate to="/login" />;
  }
  
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <PrivateRoute>
            <DashboardLayout />
          </PrivateRoute>
        }
      >
        <Route index element={<DefaultPageRedirect />} />
        <Route path="dashboard" element={<RouteContentRenderer route="dashboard" />} />
        <Route path="plugin-studio" element={<RouteContentRenderer route="plugin-studio" />} />
        <Route path="settings" element={<RouteContentRenderer route="settings" />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="plugin-manager" element={<RouteContentRenderer route="plugin-manager" />} />
        <Route path="plugin-manager/:pluginId/:moduleId" element={<ModuleDetailPage />} />
        <Route path="plugin-installer" element={<PluginInstallerPage />} />
        <Route path="personas" element={<RouteContentRenderer route="personas" />} />
        <Route path="personas/new" element={<PersonaFormPage />} />
        <Route path="personas/:personaId" element={<PersonasPage />} />
        <Route path="personas/:personaId/edit" element={<PersonaFormPage />} />
        {/* Dynamic routes for published pages - wrapped in Route element */}
        <Route path="pages/*" element={<DynamicRoutes />} />
        {/* Custom navigation routes - handled by RouteContentRenderer */}
        <Route path=":route" element={<RouteContentRenderer />} />
      </Route>
      {/* Catch-all route for direct URLs */}
      <Route path="*" element={<DynamicPageRenderer />} />
    </Routes>
  );
}

export default AppRoutes;
