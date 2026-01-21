import React from 'react';
import { Tabs, Tab, Box, Chip } from '@mui/material';
import { PluginType } from '../types';

interface PluginTypeTabsProps {
  selectedType: PluginType | 'all';
  onTypeChange: (type: PluginType | 'all') => void;
  counts?: {
    all?: number;
    frontend?: number;
    backend?: number;
    fullstack?: number;
  };
}

/**
 * Tab component for filtering plugins by type (Frontend, Backend, or All)
 */
export const PluginTypeTabs: React.FC<PluginTypeTabsProps> = ({
  selectedType,
  onTypeChange,
  counts
}) => {
  const handleChange = (_event: React.SyntheticEvent, newValue: PluginType | 'all') => {
    onTypeChange(newValue);
  };

  const renderLabel = (label: string, count?: number) => (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      {label}
      {count !== undefined && (
        <Chip
          label={count}
          size="small"
          sx={{
            height: 20,
            fontSize: '0.75rem',
            '& .MuiChip-label': { px: 1 }
          }}
        />
      )}
    </Box>
  );

  return (
    <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
      <Tabs
        value={selectedType}
        onChange={handleChange}
        aria-label="plugin type tabs"
      >
        <Tab
          label={renderLabel('All Plugins', counts?.all)}
          value="all"
        />
        <Tab
          label={renderLabel('Frontend', counts?.frontend)}
          value="frontend"
        />
        <Tab
          label={renderLabel('Backend', counts?.backend)}
          value="backend"
        />
        <Tab
          label={renderLabel('Fullstack', counts?.fullstack)}
          value="fullstack"
        />
      </Tabs>
    </Box>
  );
};

export default PluginTypeTabs;
