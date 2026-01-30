import React, { useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Alert,
  Button,
  Chip,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Divider,
  CircularProgress
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Extension as ExtensionIcon,
  GitHub as GitHubIcon,
  Refresh as RefreshIcon,
  PlayArrow as PlayArrowIcon,
  Storage as StorageIcon,
  Warning as WarningIcon
} from '@mui/icons-material';
import { PluginInstallResponse, PluginTestState } from '../types';
import PluginTestResults from './PluginTestResults';
import { pluginInstallerService } from '../services';

interface InstallationResultProps {
  result: PluginInstallResponse;
  onInstallAnother: () => void;
  onGoToPluginManager: () => void;
}

const InstallationResult: React.FC<InstallationResultProps> = ({
  result,
  onInstallAnother,
  onGoToPluginManager
}) => {
  const isSuccess = result.status === 'success';

  // Test state management
  const [testState, setTestState] = useState<PluginTestState>({
    isLoading: false,
    result: null,
    hasRun: false
  });

  // Handle plugin test
  const handleTestPlugin = async () => {
    if (!result.data?.plugin_slug) return;

    setTestState(prev => ({ ...prev, isLoading: true }));

    try {
      const testResult = await pluginInstallerService.testPluginLoading(result.data.plugin_slug);
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

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {isSuccess ? (
            <CheckCircleIcon color="success" />
          ) : (
            <ErrorIcon color="error" />
          )}
          Installation {isSuccess ? 'Successful' : 'Failed'}
        </Typography>
      </Box>

      <Alert severity={isSuccess ? 'success' : 'error'} sx={{ mb: 3 }}>
        <Typography variant="body1" sx={{ fontWeight: 'medium' }}>
          {result.message}
        </Typography>
        {result.error && (
          <Typography variant="body2" sx={{ mt: 1 }}>
            Error: {result.error}
          </Typography>
        )}
      </Alert>

      {isSuccess && result.data && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'medium' }}>
            Plugin Details
          </Typography>

          <List dense>
            <ListItem>
              <ListItemIcon>
                <ExtensionIcon />
              </ListItemIcon>
              <ListItemText
                primary="Plugin ID"
                secondary={result.data.plugin_slug}
              />
            </ListItem>

            <ListItem>
              <ListItemIcon>
                <GitHubIcon />
              </ListItemIcon>
              <ListItemText
                primary="Repository"
                secondary={result.data.repo_url}
              />
            </ListItem>

            <ListItem>
              <ListItemIcon>
                <Chip label="v" size="small" />
              </ListItemIcon>
              <ListItemText
                primary="Version"
                secondary={result.data.version}
              />
            </ListItem>

            {result.data.modules_created && result.data.modules_created.length > 0 && (
              <ListItem>
                <ListItemIcon>
                  <ExtensionIcon />
                </ListItemIcon>
                <ListItemText
                  primary="Modules Created"
                  secondary={`${result.data.modules_created.length} module(s): ${result.data.modules_created.join(', ')}`}
                />
              </ListItem>
            )}
          </List>

          <Divider sx={{ my: 2 }} />

          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
            <Chip
              label={`Source: ${result.data.source}`}
              variant="outlined"
              size="small"
            />
            <Chip
              label="User-scoped installation"
              variant="outlined"
              size="small"
              color="primary"
            />
            {result.data?.plugin_type === 'backend' && (
              <Chip
                icon={<StorageIcon sx={{ fontSize: 16 }} />}
                label="Backend Plugin"
                size="small"
                color="secondary"
              />
            )}
            {result.data?.plugin_type === 'fullstack' && (
              <Chip
                icon={<StorageIcon sx={{ fontSize: 16 }} />}
                label="Fullstack Plugin"
                size="small"
                color="info"
              />
            )}
          </Box>

          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              This plugin has been installed for your account only. Other users will not see or have access to this plugin unless they install it themselves.
            </Typography>
          </Alert>

          {/* Backend Plugin Warning - shown when plugin_type is backend or fullstack */}
          {(result.data?.plugin_type === 'backend' || result.data?.plugin_type === 'fullstack') && (
            <Alert
              severity="warning"
              icon={<StorageIcon />}
              sx={{ mb: 2 }}
            >
              <Typography variant="body2" fontWeight="medium" sx={{ mb: 1 }}>
                <WarningIcon sx={{ fontSize: 16, verticalAlign: 'middle', mr: 0.5 }} />
                Backend Plugin Installed
              </Typography>
              <Typography variant="body2">
                This is a {result.data.plugin_type} plugin that executes server-side code.
                Backend plugins can access server resources, databases, and APIs.
                Only enable backend plugins from trusted sources.
              </Typography>
            </Alert>
          )}

          {/* Plugin Loading Test Section */}
          <Divider sx={{ my: 2 }} />

          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'medium' }}>
              Plugin Loading Test
            </Typography>

            {!testState.hasRun ? (
              <Box>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Test if the plugin modules can be successfully loaded by the frontend.
                </Typography>
                <Button
                  variant="outlined"
                  startIcon={testState.isLoading ? <CircularProgress size={16} /> : <PlayArrowIcon />}
                  onClick={handleTestPlugin}
                  disabled={testState.isLoading}
                  size="small"
                >
                  {testState.isLoading ? 'Testing Plugin...' : 'Test Plugin Loading'}
                </Button>
              </Box>
            ) : (
              <PluginTestResults result={testState.result} />
            )}
          </Box>
        </Box>
      )}

      <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={onInstallAnother}
        >
          Install Another Plugin
        </Button>

        <Button
          variant="contained"
          startIcon={<ExtensionIcon />}
          onClick={onGoToPluginManager}
        >
          Go to Plugin Manager
        </Button>
      </Box>
    </Paper>
  );
};

export default InstallationResult;