import React, { useState } from 'react';
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
  Alert
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import UpdateIcon from '@mui/icons-material/Update';
import DeleteIcon from '@mui/icons-material/Delete';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { Module, Plugin } from '../types';
import ModuleStatusToggle from './ModuleStatusToggle';
import { pluginInstallerService } from '../../plugin-installer/services';
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Plugin test state
  const [testState, setTestState] = useState<PluginTestState>({
    isLoading: false,
    result: null,
    hasRun: false
  });

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
            onChange={onToggleStatus}
          />
        </Box>
      </Box>
      
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mb: 3 }}>
        <Typography variant="body1">
          <strong>Plugin:</strong> {plugin.name} (v{plugin.version})
        </Typography>
        
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
      </Box>
      
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
    </Paper>
  );
};

export default ModuleDetailHeader;
