import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Chip,
  Button,
  Divider,
  Paper,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemIcon,
  ListItemText
} from '@mui/material';
import PowerOffIcon from '@mui/icons-material/PowerOff';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import UpdateIcon from '@mui/icons-material/Update';
import DeleteIcon from '@mui/icons-material/Delete';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StorageIcon from '@mui/icons-material/Storage';
import WebIcon from '@mui/icons-material/Web';
import WarningIcon from '@mui/icons-material/Warning';
import LinkIcon from '@mui/icons-material/Link';
import { Module, Plugin, DependentPlugin } from '../types';
import ModuleStatusToggle from './ModuleStatusToggle';
import { pluginInstallerService } from '../../plugin-installer/services';
import moduleService from '../services/moduleService';
import { PluginTestState } from '../../plugin-installer/types';
import PluginTestResults from '../../plugin-installer/components/PluginTestResults';

interface ModuleDetailHeaderProps {
  module: Module;
  plugin: Plugin;
  onBack: () => void;
  onToggleStatus: (enabled: boolean) => Promise<void>;
  onUpdate?: () => Promise<void>;
  onDelete?: () => Promise<void>;
}

/**
 * Header component for the module detail page
 */
export const ModuleDetailHeader: React.FC<ModuleDetailHeaderProps> = ({
  module,
  plugin,
  onBack,
  onToggleStatus,
  onUpdate,
  onDelete
}) => {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false);
  const [cascadeDisableDialogOpen, setCascadeDisableDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dependentPlugins, setDependentPlugins] = useState<DependentPlugin[]>([]);
  const [cascadeDisableTargets, setCascadeDisableTargets] = useState<DependentPlugin[]>([]);

  // Plugin test state
  const [testState, setTestState] = useState<PluginTestState>({
    isLoading: false,
    result: null,
    hasRun: false
  });

  // Fetch dependent plugins for backend/fullstack plugins
  useEffect(() => {
    const fetchDependents = async () => {
      if (plugin.pluginType === 'backend' || plugin.pluginType === 'fullstack') {
        try {
          const dependents = await moduleService.getDependentPlugins(plugin.name);
          setDependentPlugins(dependents);
        } catch (err) {
          console.error('Failed to fetch dependent plugins:', err);
        }
      }
    };
    fetchDependents();
  }, [plugin.name, plugin.pluginType]);

  const handleUpdate = async () => {
    if (!onUpdate) return;

    setLoading(true);
    setError(null);
    try {
      await onUpdate();
      setUpdateDialogOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update plugin');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!onDelete) return;

    setLoading(true);
    setError(null);
    try {
      await onDelete();
      // Refresh sidebar navigation in case plugin removal changes available pages
      const win = window as typeof window & {
        refreshSidebar?: () => void;
        refreshPages?: () => void;
      };
      win.refreshSidebar?.();
      win.refreshPages?.();
      setDeleteDialogOpen(false);
      onBack(); // Navigate back after successful deletion
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete plugin');
    } finally {
      setLoading(false);
    }
  };

  // Handle plugin test
  const handleTestPlugin = async () => {
    setTestState(prev => ({ ...prev, isLoading: true }));

    try {
      // Use plugin.name instead of plugin.id since the manifest uses plugin_slug/name for identification
      const testResult = await pluginInstallerService.testPluginLoading(plugin.name);
      setTestState({
        isLoading: false,
        result: testResult,
        hasRun: true
      });
    } catch (error: any) {
      setTestState({
        isLoading: false,
        result: {
          status: 'error',
          message: 'Test failed to execute',
          details: {
            backend: {
              plugin_installed: false,
              files_exist: false,
              manifest_valid: false,
              bundle_accessible: false,
              modules_configured: [],
              errors: [error.message || 'Unknown error occurred'],
              warnings: []
            },
            frontend: {
              success: false,
              error: error.message || 'Test execution failed'
            },
            overall: {
              canLoad: false,
              canInstantiate: false,
              issues: ['Test execution failed'],
              recommendations: ['Check console for detailed error information', 'Ensure plugin is properly installed']
            }
          }
        },
        hasRun: true
      });
    }
  };

  const canUpdate = plugin.sourceUrl && plugin.updateAvailable;
  const canDelete = plugin.sourceUrl;
  const isBackendPlugin = plugin.pluginType === 'backend' || plugin.pluginType === 'fullstack';
  const hasEnabledDependents = dependentPlugins.some(dep => dep.enabled);

  // Intercept toggle status to show cascade disable warning for backend plugins
  const handleToggleStatusWithCascade = async (enabled: boolean) => {
    // If enabling, no cascade check needed
    if (enabled) {
      await onToggleStatus(enabled);
      return;
    }

    // If disabling a backend plugin with enabled dependents, show warning
    if (isBackendPlugin && hasEnabledDependents) {
      setCascadeDisableTargets(dependentPlugins.filter(dep => dep.enabled));
      setCascadeDisableDialogOpen(true);
      return;
    }

    // Otherwise, proceed normally
    await onToggleStatus(enabled);
  };

  // Handle confirmed cascade disable
  const handleConfirmCascadeDisable = async () => {
    setLoading(true);
    setError(null);
    try {
      // Use the cascade disable endpoint
      await moduleService.disablePluginWithCascade(plugin.id);
      setCascadeDisableDialogOpen(false);
      // Refresh the page to show updated states
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to disable plugin');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Button
        startIcon={<ArrowBackIcon />}
        onClick={onBack}
        sx={{ mb: 2 }}
      >
        Back to Plugin Manager
      </Button>
      
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
        <Typography variant="h4" component="h1" gutterBottom>
          {module.displayName || module.name}
        </Typography>
        
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Button
            variant="outlined"
            color="info"
            startIcon={testState.isLoading ? <CircularProgress size={16} /> : <PlayArrowIcon />}
            onClick={handleTestPlugin}
            disabled={testState.isLoading}
            size="small"
          >
            {testState.isLoading ? 'Testing...' : 'Test Plugin'}
          </Button>

          {canUpdate && (
            <Button
              variant="contained"
              color="primary"
              startIcon={<UpdateIcon />}
              onClick={() => setUpdateDialogOpen(true)}
              size="small"
            >
              Update Available
            </Button>
          )}

          {canDelete && (
            <Button
              variant="outlined"
              color="error"
              startIcon={<DeleteIcon />}
              onClick={() => setDeleteDialogOpen(true)}
              size="small"
            >
              Delete Plugin
            </Button>
          )}

          <ModuleStatusToggle
            moduleId={module.id}
            pluginId={module.pluginId}
            enabled={module.enabled}
            onChange={handleToggleStatusWithCascade}
          />
        </Box>
      </Box>
      
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="body1">
            <strong>Plugin:</strong> {plugin.name} (v{plugin.version})
          </Typography>
          {plugin.pluginType === 'backend' && (
            <Chip
              icon={<StorageIcon sx={{ fontSize: 16 }} />}
              label="Backend Plugin"
              size="small"
              color="secondary"
            />
          )}
          {plugin.pluginType === 'fullstack' && (
            <Chip
              icon={<WebIcon sx={{ fontSize: 16 }} />}
              label="Fullstack Plugin"
              size="small"
              color="info"
            />
          )}
          {dependentPlugins.length > 0 && (
            <Chip
              icon={<WarningIcon sx={{ fontSize: 16 }} />}
              label={`${dependentPlugins.length} dependent${dependentPlugins.length > 1 ? 's' : ''}`}
              size="small"
              color="warning"
            />
          )}
        </Box>

        {module.author && (
          <Typography variant="body1">
            <strong>Author:</strong> {module.author}
          </Typography>
        )}

        {module.category && (
          <Typography variant="body1">
            <strong>Category:</strong> {module.category}
          </Typography>
        )}

        {module.lastUpdated && (
          <Typography variant="body1">
            <strong>Updated:</strong> {new Date(module.lastUpdated).toLocaleDateString()}
          </Typography>
        )}

        {/* Backend Dependencies */}
        {plugin.backendDependencies && plugin.backendDependencies.length > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography variant="body1">
              <strong>Requires:</strong>
            </Typography>
            {plugin.backendDependencies.map((dep) => (
              <Chip
                key={dep}
                icon={<LinkIcon sx={{ fontSize: 14 }} />}
                label={dep}
                size="small"
                variant="outlined"
              />
            ))}
          </Box>
        )}
      </Box>

      {/* Dependent Plugins Section for Backend Plugins */}
      {(plugin.pluginType === 'backend' || plugin.pluginType === 'fullstack') && dependentPlugins.length > 0 && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <WarningIcon color="warning" />
            Required by {dependentPlugins.length} Plugin{dependentPlugins.length > 1 ? 's' : ''}
          </Typography>
          <Alert severity="warning" sx={{ mb: 2 }}>
            Disabling this backend plugin will also disable the following dependent plugins.
          </Alert>
          <List dense>
            {dependentPlugins.map((dep) => (
              <ListItem key={dep.id}>
                <ListItemIcon>
                  <WebIcon color={dep.enabled ? 'primary' : 'disabled'} />
                </ListItemIcon>
                <ListItemText
                  primary={dep.name}
                  secondary={dep.enabled ? 'Currently enabled' : 'Currently disabled'}
                />
                <Chip
                  label={dep.enabled ? 'Enabled' : 'Disabled'}
                  size="small"
                  color={dep.enabled ? 'success' : 'default'}
                />
              </ListItem>
            ))}
          </List>
        </>
      )}
      
      {module.description && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" gutterBottom>Description</Typography>
          <Typography variant="body1" paragraph>
            {module.description}
          </Typography>
        </>
      )}
      
      {module.tags && module.tags.length > 0 && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" gutterBottom>Tags</Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
            {module.tags.map((tag) => (
              <Chip key={tag} label={tag} />
            ))}
          </Box>
        </>
      )}

      {/* Plugin Test Results */}
      {testState.hasRun && testState.result && (
        <>
          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" gutterBottom>Plugin Loading Test Results</Typography>
          <PluginTestResults result={testState.result} />
        </>
      )}

      {/* Update Confirmation Dialog */}
      <Dialog open={updateDialogOpen} onClose={() => setUpdateDialogOpen(false)}>
        <DialogTitle>Update Plugin</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to update "{plugin.name}" from version {plugin.version} to {plugin.latestVersion}?
          </DialogContentText>
          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUpdateDialogOpen(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={handleUpdate}
            variant="contained"
            disabled={loading}
            startIcon={loading ? <CircularProgress size={20} /> : <UpdateIcon />}
          >
            {loading ? 'Updating...' : 'Update'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete Plugin</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to permanently delete the plugin "{plugin.name}"?
            This action cannot be undone and will remove all plugin data and modules.
          </DialogContentText>
          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={handleDelete}
            variant="contained"
            color="error"
            disabled={loading}
            startIcon={loading ? <CircularProgress size={20} /> : <DeleteIcon />}
          >
            {loading ? 'Deleting...' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Cascade Disable Confirmation Dialog */}
      <Dialog open={cascadeDisableDialogOpen} onClose={() => setCascadeDisableDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <WarningIcon color="warning" />
          Disable Backend Plugin
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            Disabling "{plugin.name}" will also disable the following dependent plugins:
          </DialogContentText>
          <List dense sx={{ mt: 2, bgcolor: 'background.paper', borderRadius: 1 }}>
            {cascadeDisableTargets.map((dep) => (
              <ListItem key={dep.id}>
                <ListItemIcon>
                  <WebIcon color="primary" />
                </ListItemIcon>
                <ListItemText primary={dep.name} />
              </ListItem>
            ))}
          </List>
          <Alert severity="warning" sx={{ mt: 2 }}>
            These plugins require "{plugin.name}" to function. They will be automatically disabled.
          </Alert>
          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCascadeDisableDialogOpen(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmCascadeDisable}
            variant="contained"
            color="warning"
            disabled={loading}
            startIcon={loading ? <CircularProgress size={20} /> : <WarningIcon />}
          >
            {loading ? 'Disabling...' : `Disable ${cascadeDisableTargets.length + 1} Plugins`}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default ModuleDetailHeader;
