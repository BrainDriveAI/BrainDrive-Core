import React, { useState, useCallback } from 'react';
import {
  Box,
  Button,
  Typography,
  Paper,
  Alert,
  Chip
} from '@mui/material';
import {
  InsertDriveFile as FileIcon,
  CloudUpload as CloudUploadIcon
} from '@mui/icons-material';
import FileUploadZone from '../common/FileUploadZone';
import { LocalFileInstallRequest, FileUploadState } from '../../types';
import { formatFileSize } from '../../utils/fileValidation';

interface LocalFileInstallFormProps {
  onInstall: (request: LocalFileInstallRequest) => void;
  isInstalling: boolean;
}

const LocalFileInstallForm: React.FC<LocalFileInstallFormProps> = ({
  onInstall,
  isInstalling
}) => {
  const [uploadState, setUploadState] = useState<FileUploadState>({
    file: null,
    uploading: false,
    progress: 0,
    error: null
  });

  const handleFileSelect = useCallback((file: File) => {
    setUploadState(prev => ({
      ...prev,
      file,
      error: null
    }));
  }, []);

  const handleFileRemove = useCallback(() => {
    setUploadState({
      file: null,
      uploading: false,
      progress: 0,
      error: null
    });
  }, []);

  const handleSubmit = useCallback((event: React.FormEvent) => {
    event.preventDefault();

    if (!uploadState.file) {
      setUploadState(prev => ({
        ...prev,
        error: 'Please select a plugin archive file'
      }));
      return;
    }

    onInstall({
      method: 'local-file',
      file: uploadState.file,
      filename: uploadState.file.name
    });
  }, [uploadState.file, onInstall]);

  const canInstall = uploadState.file && !uploadState.error && !isInstalling;

  return (
    <Paper sx={{ p: 3 }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h6" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <FileIcon />
          Install from Local File
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Upload a plugin archive file (ZIP, RAR, or TAR.GZ) from your computer. The plugin will be extracted and installed for your account only.
        </Typography>
      </Box>

      <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        <FileUploadZone
          onFileSelect={handleFileSelect}
          onFileRemove={handleFileRemove}
          uploadState={uploadState}
          disabled={isInstalling}
        />

        {/* File Information */}
        {uploadState.file && (
          <Box sx={{ p: 2, bgcolor: 'background.default', borderRadius: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 'medium', mb: 1 }}>
              Selected File:
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <FileIcon fontSize="small" color="action" />
              <Typography variant="body2">{uploadState.file.name}</Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Chip
                label={formatFileSize(uploadState.file.size)}
                size="small"
                variant="outlined"
              />
              <Chip
                label={uploadState.file.type || 'Unknown type'}
                size="small"
                variant="outlined"
              />
            </Box>
          </Box>
        )}

        <Alert severity="info">
          <Typography variant="body2" sx={{ fontWeight: 'medium', mb: 1 }}>
            Supported Archive Formats:
          </Typography>
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            <li><strong>ZIP</strong> - Most common format, widely supported</li>
            <li><strong>RAR</strong> - High compression ratio</li>
            <li><strong>TAR.GZ</strong> - Unix/Linux standard format</li>
          </Box>
          <Typography variant="body2" sx={{ mt: 1, fontSize: '0.75rem', color: 'text.secondary' }}>
            • Maximum file size: 100MB
            <br />
            • Archive must contain a valid plugin structure with plugin.json
            <br />
            • Files will be validated before installation
          </Typography>
        </Alert>

        <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
          <Button
            type="submit"
            variant="contained"
            disabled={!canInstall}
            startIcon={<CloudUploadIcon />}
            sx={{ minWidth: 120 }}
          >
            {isInstalling ? 'Installing...' : 'Install Plugin'}
          </Button>
        </Box>
      </Box>
    </Paper>
  );
};

export default LocalFileInstallForm;
