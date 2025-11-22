import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Grid,
  Alert,
  Divider,
  Card,
  CardContent,
  IconButton,
  InputAdornment,
  CircularProgress,
  Avatar,
  Drawer,
  Stack
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Visibility, VisibilityOff, Person, Email, Lock } from '@mui/icons-material';
import { useAuth } from '../contexts/AuthContext';
import { useApi } from '../contexts/ServiceContext';
import { diagnosticsLog, buildDiagnosticsSnapshot, buildIssueText, DiagnosticsSnapshot } from '../utils/diagnostics';

const ProfilePage = () => {
  const { user } = useAuth();
  const apiService = useApi();
  const theme = useTheme();
  
  // Username update state
  const [username, setUsername] = useState('');
  const [usernameError, setUsernameError] = useState('');
  const [isUpdatingUsername, setIsUpdatingUsername] = useState(false);
  const [usernameSuccess, setUsernameSuccess] = useState(false);
  
  // Password update state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [isUpdatingPassword, setIsUpdatingPassword] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState(false);
  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [diagnosticsData, setDiagnosticsData] = useState<DiagnosticsSnapshot | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState('');
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [copyStatus, setCopyStatus] = useState('');

  // Initialize form with user data
  useEffect(() => {
    if (user) {
      setUsername(user.username || '');
    }
  }, [user]);

  // Handle username update
  const handleUsernameUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    setUsernameError('');
    setUsernameSuccess(false);
    
    if (!username.trim()) {
      setUsernameError('Username cannot be empty');
      return;
    }
    
    setIsUpdatingUsername(true);
    
    try {
      const response = await apiService?.put('/api/v1/auth/profile/username', { username });
      setUsernameSuccess(true);
      
      // Refresh user info to update the UI
      try {
        const updatedUser = await apiService?.get('/api/v1/auth/me');
        // Update local state to reflect the changes
        if (updatedUser) {
          window.location.reload(); // Simple solution to refresh the page with updated user data
        }
      } catch (refreshErr) {
        console.error('Error refreshing user info:', refreshErr);
      }
    } catch (err: any) {
      console.error('Error updating username:', err);
      
      if (err.response && err.response.data && err.response.data.detail) {
        setUsernameError(err.response.data.detail);
      } else {
        setUsernameError('Failed to update username. Please try again.');
      }
    } finally {
      setIsUpdatingUsername(false);
    }
  };

  // Handle password update
  const handlePasswordUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    setPasswordSuccess(false);
    
    // Validate password fields
    if (!currentPassword) {
      setPasswordError('Current password is required');
      return;
    }
    
    if (!newPassword) {
      setPasswordError('New password is required');
      return;
    }
    
    if (newPassword.length < 8) {
      setPasswordError('New password must be at least 8 characters long');
      return;
    }
    
    if (newPassword !== confirmPassword) {
      setPasswordError('New password and confirmation do not match');
      return;
    }
    
    setIsUpdatingPassword(true);
    
    try {
      const response = await apiService?.put('/api/v1/auth/profile/password', {
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword
      });
      
      setPasswordSuccess(true);
      
      // Clear password fields
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: any) {
      console.error('Error updating password:', err);
      
      if (err.response && err.response.data && err.response.data.detail) {
        setPasswordError(err.response.data.detail);
      } else {
        setPasswordError('Failed to update password. Please try again.');
      }
    } finally {
      setIsUpdatingPassword(false);
    }
  };

  // Toggle password visibility
  const togglePasswordVisibility = (field: 'current' | 'new' | 'confirm') => {
    if (field === 'current') {
      setShowCurrentPassword(!showCurrentPassword);
    } else if (field === 'new') {
      setShowNewPassword(!showNewPassword);
    } else {
      setShowConfirmPassword(!showConfirmPassword);
    }
  };

  const loadDiagnostics = async () => {
    if (!apiService) return;
    setDiagnosticsLoading(true);
    setDiagnosticsError('');
    setCopyStatus('');

    try {
      const backendResponse = await apiService.get('/api/v1/diagnostics');
      const snapshot = buildDiagnosticsSnapshot(backendResponse);
      setDiagnosticsData(snapshot);
      diagnosticsLog.info('diagnostics-loaded', { hasBackend: !!backendResponse });
    } catch (err: any) {
      console.error('Error loading diagnostics:', err);
      setDiagnosticsError(
        err?.response?.data?.detail || 'Failed to load diagnostics. Please try again.'
      );
      diagnosticsLog.error('diagnostics-load-failed', { message: err?.message });
    } finally {
      setDiagnosticsLoading(false);
    }
  };

  const openDiagnostics = async () => {
    setDiagnosticsOpen(true);
    await loadDiagnostics();
  };

  const closeDiagnostics = () => {
    setDiagnosticsOpen(false);
  };

  const handleCopyDiagnostics = async () => {
    if (!diagnosticsData) return;
    try {
      const text = buildIssueText(diagnosticsData);
      await navigator?.clipboard?.writeText(text);
      setCopyStatus('Copied for GitHub');
      diagnosticsLog.info('diagnostics-copied');
      setTimeout(() => setCopyStatus(''), 2000);
    } catch (err) {
      console.error('Error copying diagnostics:', err);
      setCopyStatus('Copy failed');
    }
  };

  const handleDownloadDiagnostics = () => {
    if (!diagnosticsData) return;
    const blob = new Blob([JSON.stringify(diagnosticsData, null, 2)], {
      type: 'application/json'
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'braindrive-diagnostics.json';
    anchor.click();
    URL.revokeObjectURL(url);
    diagnosticsLog.info('diagnostics-downloaded');
  };

  if (!user) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%', p: 3 }}>
      <Typography variant="h4" gutterBottom>
        Profile
      </Typography>
      
      <Grid container spacing={3}>
        {/* User Info Card */}
        <Grid item xs={12} md={4}>
          <Card sx={{ height: '100%' }}>
            <CardContent sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 4, height: '100%', position: 'relative' }}>
              <Avatar 
                sx={{ 
                  width: 100, 
                  height: 100, 
                  mb: 2,
                  bgcolor: 'primary.main',
                  fontSize: '2.5rem'
                }}
              >
                {user.username ? user.username[0].toUpperCase() : 'U'}
              </Avatar>
              
              <Typography variant="h5" gutterBottom>
                {user.full_name || user.username}
              </Typography>
              
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                <Email fontSize="small" sx={{ mr: 1, color: 'text.secondary' }} />
                <Typography variant="body1" color="text.secondary">
                  {user.email}
                </Typography>
              </Box>
              
              <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <Person fontSize="small" sx={{ mr: 1, color: 'text.secondary' }} />
                <Typography variant="body1" color="text.secondary">
                  {user.username}
                </Typography>
              </Box>

              <Box sx={{ mt: 'auto', pb: 2, width: '100%', position: 'absolute', bottom: 0, left: 0, textAlign: 'center' }}>
                <Typography variant="caption" color="text.secondary" align="center">
                  {`BrainDrive v${user.version ?? '0.0.0'}`}
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Grid>
        
        {/* Settings Cards */}
        <Grid item xs={12} md={8}>
          <Grid container spacing={3}>
            {/* Username Update Card */}
            <Grid item xs={12}>
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  Update Username
                </Typography>
                <Divider sx={{ mb: 3 }} />
                
                {usernameSuccess && (
                  <Alert severity="success" sx={{ mb: 2 }}>
                    Username updated successfully!
                  </Alert>
                )}
                
                {usernameError && (
                  <Alert severity="error" sx={{ mb: 2 }}>
                    {usernameError}
                  </Alert>
                )}
                
                <form onSubmit={handleUsernameUpdate}>
                  <TextField
                    fullWidth
                    label="Username"
                    variant="outlined"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Person />
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 2 }}
                  />
                  
                  <Button
                    type="submit"
                    variant="contained"
                    disabled={isUpdatingUsername || username === user.username}
                    sx={{ mt: 1 }}
                  >
                    {isUpdatingUsername ? 'Updating...' : 'Update Username'}
                  </Button>
                </form>
              </Paper>
            </Grid>
            
            {/* Password Update Card */}
            <Grid item xs={12}>
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  Change Password
                </Typography>
                <Divider sx={{ mb: 3 }} />
                
                {passwordSuccess && (
                  <Alert severity="success" sx={{ mb: 2 }}>
                    Password updated successfully!
                  </Alert>
                )}
                
                {passwordError && (
                  <Alert severity="error" sx={{ mb: 2 }}>
                    {passwordError}
                  </Alert>
                )}
                
                <form onSubmit={handlePasswordUpdate}>
                  <TextField
                    fullWidth
                    label="Current Password"
                    variant="outlined"
                    type={showCurrentPassword ? 'text' : 'password'}
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Lock />
                        </InputAdornment>
                      ),
                      endAdornment: (
                        <InputAdornment position="end">
                          <IconButton
                            aria-label="toggle password visibility"
                            onClick={() => togglePasswordVisibility('current')}
                            edge="end"
                          >
                            {showCurrentPassword ? <VisibilityOff /> : <Visibility />}
                          </IconButton>
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 2 }}
                  />
                  
                  <TextField
                    fullWidth
                    label="New Password"
                    variant="outlined"
                    type={showNewPassword ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Lock />
                        </InputAdornment>
                      ),
                      endAdornment: (
                        <InputAdornment position="end">
                          <IconButton
                            aria-label="toggle password visibility"
                            onClick={() => togglePasswordVisibility('new')}
                            edge="end"
                          >
                            {showNewPassword ? <VisibilityOff /> : <Visibility />}
                          </IconButton>
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 2 }}
                  />
                  
                  <TextField
                    fullWidth
                    label="Confirm New Password"
                    variant="outlined"
                    type={showConfirmPassword ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <Lock />
                        </InputAdornment>
                      ),
                      endAdornment: (
                        <InputAdornment position="end">
                          <IconButton
                            aria-label="toggle password visibility"
                            onClick={() => togglePasswordVisibility('confirm')}
                            edge="end"
                          >
                            {showConfirmPassword ? <VisibilityOff /> : <Visibility />}
                          </IconButton>
                        </InputAdornment>
                      ),
                    }}
                    sx={{ mb: 2 }}
                  />
                  
                  <Button
                    type="submit"
                    variant="contained"
                    disabled={isUpdatingPassword || !currentPassword || !newPassword || !confirmPassword}
                    sx={{ mt: 1 }}
                  >
                    {isUpdatingPassword ? 'Updating...' : 'Change Password'}
                  </Button>
                </form>
              </Paper>
            </Grid>

            {/* System Info Card */}
            <Grid item xs={12}>
              <Paper sx={{ p: 3 }}>
                <Typography variant="h6" gutterBottom>
                  System Info for GitHub Issues
                </Typography>
                <Divider sx={{ mb: 3 }} />

                {diagnosticsError && (
                  <Alert severity="error" sx={{ mb: 2 }}>
                    {diagnosticsError}
                  </Alert>
                )}

                <Stack direction="row" spacing={2}>
                  <Button
                    variant="contained"
                    onClick={openDiagnostics}
                    disabled={diagnosticsLoading}
                  >
                    {diagnosticsLoading ? 'Loading...' : 'Open System Info'}
                  </Button>
                  <Button variant="outlined" onClick={loadDiagnostics} disabled={diagnosticsLoading}>
                    Refresh Data
                  </Button>
                </Stack>

                <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                  Opens a drawer with collected environment details, plugins/modules, browser/OS info, and
                  quick copy/download actions for GitHub issue reports.
                </Typography>
              </Paper>
            </Grid>
          </Grid>
        </Grid>
      </Grid>

      <Drawer anchor="right" open={diagnosticsOpen} onClose={closeDiagnostics}>
        <Box
          sx={{
            width: { xs: 360, sm: 440 },
            p: 3,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            bgcolor: theme.palette.mode === 'dark' ? theme.palette.background.default : theme.palette.background.paper
          }}
        >
          <Typography variant="h6" gutterBottom>
            BrainDrive System Info
          </Typography>

          {diagnosticsLoading && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          )}

          {!diagnosticsLoading && diagnosticsError && (
            <Alert severity="error">{diagnosticsError}</Alert>
          )}

          {!diagnosticsLoading && !diagnosticsError && diagnosticsData && (
            <>
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  bgcolor: theme.palette.mode === 'dark' ? theme.palette.background.paper : undefined
                }}
              >
                <Typography variant="subtitle1" gutterBottom>
                  GitHub-ready Summary
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[100],
                    p: 1.5,
                    borderRadius: 1,
                    maxHeight: 240,
                    overflow: 'auto',
                    fontSize: 12
                  }}
                >
                  {buildIssueText(diagnosticsData)}
                </Box>
              </Paper>

              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  bgcolor: theme.palette.mode === 'dark' ? theme.palette.background.paper : undefined
                }}
              >
                <Typography variant="subtitle1" gutterBottom>
                  Backend Snapshot
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[100],
                    p: 1.5,
                    borderRadius: 1,
                    fontSize: 12,
                    maxHeight: 160,
                    overflow: 'auto'
                  }}
                >
                  {JSON.stringify(diagnosticsData.backend, null, 2)}
                </Box>
              </Paper>

              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  bgcolor: theme.palette.mode === 'dark' ? theme.palette.background.paper : undefined
                }}
              >
                <Typography variant="subtitle1" gutterBottom>
                  Frontend Snapshot
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[100],
                    p: 1.5,
                    borderRadius: 1,
                    fontSize: 12,
                    maxHeight: 160,
                    overflow: 'auto'
                  }}
                >
                  {JSON.stringify(
                    {
                      client: diagnosticsData.client,
                      frontend: diagnosticsData.frontend
                    },
                    null,
                    2
                  )}
                </Box>
              </Paper>

              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  bgcolor: theme.palette.mode === 'dark' ? theme.palette.background.paper : undefined
                }}
              >
                <Typography variant="subtitle1" gutterBottom>
                  Recent Logs
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    bgcolor: theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[100],
                    p: 1.5,
                    borderRadius: 1,
                    fontSize: 12,
                    maxHeight: 160,
                    overflow: 'auto'
                  }}
                >
                  {diagnosticsData.logs.map((log, idx) => `${log.ts} [${log.level}] ${log.message}${log.context ? ` ${JSON.stringify(log.context)}` : ''}`).join('\n') || 'No logs captured'}
                </Box>
              </Paper>

              <Stack direction="row" spacing={2}>
                <Button variant="contained" onClick={handleCopyDiagnostics} disabled={!diagnosticsData}>
                  Copy for GitHub
                </Button>
                <Button variant="outlined" onClick={handleDownloadDiagnostics} disabled={!diagnosticsData}>
                  Download JSON
                </Button>
                <Button variant="text" onClick={loadDiagnostics} disabled={diagnosticsLoading}>
                  Refresh
                </Button>
              </Stack>

              {copyStatus && (
                <Typography variant="body2" color="success.main">
                  {copyStatus}
                </Typography>
              )}
            </>
          )}

          {!diagnosticsLoading && !diagnosticsError && !diagnosticsData && (
            <Typography variant="body2" color="text.secondary">
              Click "Refresh" to collect diagnostics.
            </Typography>
          )}
        </Box>
      </Drawer>
    </Box>
  );
};

export default ProfilePage;
