import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  Alert,
  Box,
  Typography,
  List,
  ListItem,
  ListItemIcon,
  ListItemText
} from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import StorageIcon from '@mui/icons-material/Storage';
import SecurityIcon from '@mui/icons-material/Security';
import CodeIcon from '@mui/icons-material/Code';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';

interface BackendPluginWarningDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  pluginName?: string;
  action?: 'install' | 'enable';
}

/**
 * Warning dialog shown when installing or enabling a backend plugin.
 * Backend plugins execute server-side code and require explicit user confirmation.
 */
export const BackendPluginWarningDialog: React.FC<BackendPluginWarningDialogProps> = ({
  open,
  onClose,
  onConfirm,
  pluginName = 'this plugin',
  action = 'install'
}) => {
  const actionVerb = action === 'install' ? 'Installing' : 'Enabling';
  const actionPast = action === 'install' ? 'install' : 'enable';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="backend-plugin-warning-title"
    >
      <DialogTitle
        id="backend-plugin-warning-title"
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          bgcolor: 'warning.light',
          color: 'warning.contrastText'
        }}
      >
        <WarningIcon />
        Backend Plugin Warning
      </DialogTitle>
      <DialogContent sx={{ mt: 2 }}>
        <Alert severity="warning" icon={<StorageIcon />} sx={{ mb: 2 }}>
          <Typography variant="body1" fontWeight="medium">
            {actionVerb} a backend plugin
          </Typography>
        </Alert>

        <DialogContentText sx={{ mb: 2 }}>
          <strong>{pluginName}</strong> is a backend plugin that will execute server-side code.
          Backend plugins have the ability to:
        </DialogContentText>

        <List dense>
          <ListItem>
            <ListItemIcon>
              <CodeIcon color="action" />
            </ListItemIcon>
            <ListItemText
              primary="Execute server-side code"
              secondary="Run Python/Node.js code on the server"
            />
          </ListItem>
          <ListItem>
            <ListItemIcon>
              <StorageIcon color="action" />
            </ListItemIcon>
            <ListItemText
              primary="Access server resources"
              secondary="Interact with databases, files, and APIs"
            />
          </ListItem>
          <ListItem>
            <ListItemIcon>
              <SecurityIcon color="action" />
            </ListItemIcon>
            <ListItemText
              primary="Handle sensitive operations"
              secondary="Process data and perform privileged actions"
            />
          </ListItem>
        </List>

        <Alert severity="info" icon={<VerifiedUserIcon />} sx={{ mt: 2 }}>
          <Typography variant="body2">
            <strong>Only {actionPast} backend plugins from trusted sources.</strong>
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Verify the plugin author and review the source code if possible before proceeding.
          </Typography>
        </Alert>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color="warning"
          startIcon={<WarningIcon />}
        >
          I Understand, {action === 'install' ? 'Install' : 'Enable'} Anyway
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default BackendPluginWarningDialog;
