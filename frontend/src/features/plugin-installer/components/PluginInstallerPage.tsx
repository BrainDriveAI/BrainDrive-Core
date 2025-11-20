import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Container,
  Typography,
  Box,
  Button,
  Breadcrumbs,
  Link,
  Alert,
  Tabs,
  Tab,
  Chip,
  Grid,
  Paper,
  Divider,
  Stack,
  CircularProgress
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  Extension as ExtensionIcon,
  Add as AddIcon,
  HelpOutline as HelpOutlineIcon,
  Article as ArticleIcon
} from '@mui/icons-material';
import InstallMethodTabs from './install-methods/InstallMethodTabs';
import InstallationProgress from './InstallationProgress';
import InstallationResult from './InstallationResult';
import { usePluginInstaller } from '../hooks';
import { PluginInstallRequest } from '../types';

const TAB_ORDER = ['install', 'progress', 'result', 'help'] as const;
type InstallerTab = typeof TAB_ORDER[number];

const PluginInstallerPage: React.FC = () => {
  const navigate = useNavigate();
  const {
    installationState,
    installPlugin,
    resetInstallation,
    validateUrl
  } = usePluginInstaller();

  const [activeTab, setActiveTab] = useState<InstallerTab>('install');

  const handleGoBack = useCallback(() => {
    navigate('/plugin-manager');
  }, [navigate]);

  const handleInstallAnother = useCallback(() => {
    resetInstallation('github');
    setActiveTab('install');
  }, [resetInstallation]);

  const handleGoToPluginManager = useCallback(() => {
    navigate('/plugin-manager');
  }, [navigate]);

  const handleInstall = useCallback(async (request: PluginInstallRequest) => {
    await installPlugin(request);
  }, [installPlugin]);

  const showForm = !installationState.result;
  const showProgress = installationState.isInstalling || installationState.steps.some(step => step.status !== 'pending');
  const showResult = installationState.result && !installationState.isInstalling;
  const hasError = Boolean(installationState.error && !installationState.result);

  const totalSteps = installationState.steps.length || 1;
  const completedSteps = installationState.steps.filter(step => step.status === 'completed').length;
  const progressValue = Math.round((completedSteps / totalSteps) * 100);
  const statusChip = useMemo(() => {
    if (installationState.isInstalling) {
      return { label: 'Installing', color: 'primary' as const };
    }
    if (installationState.result?.status === 'success') {
      return { label: 'Installed', color: 'success' as const };
    }
    if (installationState.result?.status === 'error' || hasError) {
      return { label: 'Needs attention', color: 'warning' as const };
    }
    return { label: 'Ready to install', color: 'default' as const };
  }, [installationState.isInstalling, installationState.result, hasError]);

  useEffect(() => {
    if (installationState.isInstalling && activeTab !== 'progress') {
      setActiveTab('progress');
    } else if (!installationState.isInstalling && installationState.result && activeTab === 'progress') {
      setActiveTab('result');
    }
  }, [installationState.isInstalling, installationState.result, activeTab]);

  const handleTabChange = useCallback((_: React.SyntheticEvent, value: InstallerTab) => {
    setActiveTab(value);
  }, []);

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Paper
        elevation={0}
        sx={{
          mb: 3,
          p: 3,
          border: '1px solid',
          borderColor: 'divider',
          position: 'sticky',
          top: 12,
          zIndex: 2,
          bgcolor: 'background.paper'
        }}
      >
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={2} flexWrap="wrap">
          <Box>
            <Breadcrumbs sx={{ mb: 1 }}>
              <Link
                component="button"
                variant="body2"
                onClick={handleGoBack}
                sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}
              >
                <ExtensionIcon fontSize="small" />
                Plugin Manager
              </Link>
              <Typography variant="body2" color="text.primary">
                Install Plugin
              </Typography>
            </Breadcrumbs>
            <Typography variant="h4" component="h1" sx={{ mb: 0.5 }}>
              Install New Plugin
            </Typography>
            <Typography variant="body2" color="text.secondary">
              
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
            <Chip label={statusChip.label} color={statusChip.color} variant="outlined" />
            <Chip
              label={`Steps: ${completedSteps}/${totalSteps}`}
              variant="outlined"
              color={installationState.isInstalling ? 'primary' : 'default'}
            />
            {installationState.isInstalling && <CircularProgress size={18} />}
            <Button
              variant="outlined"
              startIcon={<ArrowBackIcon />}
              onClick={handleGoBack}
              disabled={installationState.isInstalling}
              sx={{ ml: 1 }}
            >
              Back to Plugin Manager
            </Button>
          </Stack>
        </Stack>
      </Paper>

      <Grid container spacing={3}>
        <Grid item xs={12} lg={8}>
          <Paper elevation={0} sx={{ p: 0, border: '1px solid', borderColor: 'divider' }}>
            <Tabs
              value={activeTab}
              onChange={handleTabChange}
              aria-label="Plugin installer navigation"
              variant="scrollable"
              scrollButtons="auto"
              sx={{
                px: 2,
                borderBottom: 1,
                borderColor: 'divider',
                '& .MuiTab-root': { textTransform: 'none', alignItems: 'center' }
              }}
            >
              <Tab label="Install" value="install" />
              <Tab
                label="Progress"
                value="progress"
                disabled={!showProgress && !installationState.isInstalling}
              />
              <Tab
                label="Result"
                value="result"
                disabled={!showResult}
              />
              <Tab
                label="Help"
                value="help"
                icon={<HelpOutlineIcon fontSize="small" />}
                iconPosition="end"
                sx={{ ml: 'auto' }}
              />
            </Tabs>

            {activeTab === 'install' && (
              <Box sx={{ p: 3 }}>
                {showForm ? (
                  <InstallMethodTabs
                    onInstall={handleInstall}
                    isInstalling={installationState.isInstalling}
                    onValidateUrl={validateUrl}
                  />
                ) : (
                  <Alert severity="info" sx={{ mb: 2 }}>
                    An installation just finished. Start another install or review the result tab.
                  </Alert>
                )}
              </Box>
            )}

            {activeTab === 'progress' && (
              <Box sx={{ p: 3 }}>
                {showProgress ? (
                  <InstallationProgress
                    steps={installationState.steps}
                    currentStep={installationState.currentStep}
                    isInstalling={installationState.isInstalling}
                    errorDetails={installationState.errorDetails}
                    suggestions={installationState.suggestions}
                  />
                ) : (
                  <Alert severity="info" sx={{ mb: 2 }}>
                    No installation in progress yet. Start one from the Install tab to see live steps here.
                  </Alert>
                )}

                {hasError && (
                  <Alert severity="error" sx={{ mt: 2 }}>
                    <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
                      Installation Error
                    </Typography>
                    <Typography variant="body2" sx={{ mb: 2, whiteSpace: 'pre-line' }}>
                      {installationState.error}
                    </Typography>
                    {installationState.suggestions && installationState.suggestions.length > 0 && (
                      <Box sx={{ mt: 2, mb: 2 }}>
                        <Typography variant="body2" sx={{ fontWeight: 'medium', mb: 1 }}>
                          Suggestions to fix this issue:
                        </Typography>
                        <Box component="ul" sx={{ m: 0, pl: 2 }}>
                          {installationState.suggestions.map((suggestion, index) => (
                            <li key={index}>
                              <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                                {suggestion}
                              </Typography>
                            </li>
                          ))}
                        </Box>
                      </Box>
                    )}
                    <Box sx={{ mt: 2 }}>
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<AddIcon />}
                        onClick={() => {
                          resetInstallation('github');
                          setActiveTab('install');
                        }}
                      >
                        Try Again
                      </Button>
                    </Box>
                  </Alert>
                )}
              </Box>
            )}

            {activeTab === 'result' && (
              <Box sx={{ p: 3 }}>
                {showResult && installationState.result ? (
                  <InstallationResult
                    result={installationState.result}
                    onInstallAnother={handleInstallAnother}
                    onGoToPluginManager={handleGoToPluginManager}
                  />
                ) : (
                  <Alert severity="info">
                    Complete an installation to view detailed results and post-install actions here.
                  </Alert>
                )}
              </Box>
            )}

            {activeTab === 'help' && (
              <Box sx={{ p: 3 }}>
                <Alert severity="info" icon={<ArticleIcon fontSize="small" />} sx={{ mb: 2 }}>
                  Install from GitHub or local archive. All existing backend flows stay the same—we’re only reorganizing the view.
                </Alert>
                <Typography variant="subtitle1" gutterBottom>
                  Quick checklist
                </Typography>
                <Box component="ul" sx={{ m: 0, pl: 2, color: 'text.secondary' }}>
                  <li>GitHub installs: repository URL is correct and accessible</li>
                  <li>Local files: archive contains plugin.json and supported format (ZIP, RAR, TAR.GZ)</li>
                  <li>Stable connection recommended during download/upload</li>
                </Box>
                <Divider sx={{ my: 2 }} />
                <Typography variant="subtitle1" gutterBottom>
                  Need more guidance?
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  For plugin development guidelines, check the BrainDrive documentation. You can restart installs anytime without losing previous results.
                </Typography>
              </Box>
            )}
          </Paper>
        </Grid>

        <Grid item xs={12} lg={4}>
          <Stack spacing={2}>
            <Paper elevation={0} sx={{ p: 3, border: '1px solid', borderColor: 'divider' }}>
              <Typography variant="subtitle2" gutterBottom>
                Install at a glance
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Watch status without scrolling. Progress snaps into the Progress tab automatically.
              </Typography>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Chip size="small" label={statusChip.label} color={statusChip.color} variant="outlined" />
                <Chip size="small" label={`Progress ${progressValue}%`} variant="outlined" />
              </Stack>
            </Paper>

            <Paper elevation={0} sx={{ p: 3, border: '1px solid', borderColor: 'divider' }}>
              <Typography variant="subtitle2" gutterBottom>
                How Plugin Installation Works
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2, color: 'text.secondary' }}>
                <li>Choose GitHub URL or upload an archive from your computer.</li>
                <li>We validate structure, then download/extract/install.</li>
                <li>Installs are for your account only; you can update/remove later.</li>
              </Box>
            </Paper>

            <Paper elevation={0} sx={{ p: 3, border: '1px solid', borderColor: 'divider' }}>
              <Typography variant="subtitle2" gutterBottom>
                Troubleshooting hints
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2, color: 'text.secondary' }}>
                <li>GitHub: ensure releases exist and auth is set for private repos.</li>
                <li>Local: verify archive format and size (max 100MB).</li>
                <li>Network: retry if connection drops during download/upload.</li>
              </Box>
            </Paper>
          </Stack>
        </Grid>
      </Grid>
    </Container>
  );
};

export default PluginInstallerPage;
