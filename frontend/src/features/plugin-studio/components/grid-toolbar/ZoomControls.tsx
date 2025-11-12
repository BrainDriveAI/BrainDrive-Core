import React from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import ZoomInIcon from '@mui/icons-material/ZoomIn';
import ZoomOutIcon from '@mui/icons-material/ZoomOut';
import { usePluginStudio } from '../../hooks';

export const ZoomControls: React.FC = () => {
  const { zoom, zoomIn, zoomOut } = usePluginStudio();

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Tooltip title="Zoom out">
        <IconButton size="small" onClick={zoomOut}>
          <ZoomOutIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={`Zoom (${Math.round(zoom * 100)}%)`}>
        <Typography variant="body2" sx={{ minWidth: 42, textAlign: 'center' }}>
          {Math.round(zoom * 100)}%
        </Typography>
      </Tooltip>
      <Tooltip title="Zoom in">
        <IconButton size="small" onClick={zoomIn}>
          <ZoomInIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
};
