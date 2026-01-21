import React from 'react';
import { Box, Card, CardContent, Typography, Chip, Switch, CardActionArea } from '@mui/material';
import StorageIcon from '@mui/icons-material/Storage';
import WebIcon from '@mui/icons-material/Web';
import { Module } from '../types';
import { IconResolver } from '../../../components/IconResolver';

interface ModuleCardProps {
  module: Module;
  onClick?: (module: Module) => void;
  onToggleStatus?: (module: Module, enabled: boolean) => void;
  compact?: boolean;
}

/**
 * A reusable card component that displays key information about a module
 */
export const ModuleCard: React.FC<ModuleCardProps> = ({
  module,
  onClick,
  onToggleStatus,
  compact = false
}) => {
  const handleClick = () => {
    if (onClick) {
      onClick(module);
    }
  };

  const handleToggleStatus = (event: React.ChangeEvent<HTMLInputElement>) => {
    event.stopPropagation();
    if (onToggleStatus) {
      onToggleStatus(module, event.target.checked);
    }
  };

  return (
    <Card 
      sx={{ 
        height: compact ? '100%' : 220,
        display: 'flex',
        flexDirection: 'column',
        transition: 'transform 0.2s, box-shadow 0.2s',
        '&:hover': {
          transform: 'translateY(-4px)',
          boxShadow: 3
        }
      }}
    >
      <CardActionArea 
        onClick={handleClick}
        sx={{ 
          flexGrow: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'stretch',
          height: '100%'
        }}
      >
        <CardContent sx={{ 
          flexGrow: 1, 
          p: compact ? 1.5 : 2,
          display: 'flex',
          flexDirection: 'column',
          height: '100%'
        }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0, flex: 1 }}>
              {module.icon && (
                <Box sx={{ flexShrink: 0 }}>
                  <IconResolver icon={module.icon} fontSize="small" />
                </Box>
              )}
              <Typography variant={compact ? "subtitle1" : "h6"} component="h2" noWrap sx={{ fontWeight: 'bold', flex: 1, minWidth: 0 }}>
                {module.displayName || module.name}
              </Typography>
              {module.pluginType === 'backend' && (
                <Chip
                  icon={<StorageIcon sx={{ fontSize: 14 }} />}
                  label="Backend"
                  size="small"
                  color="secondary"
                  sx={{
                    height: compact ? 18 : 22,
                    fontSize: compact ? '0.6rem' : '0.7rem',
                    flexShrink: 0,
                    '& .MuiChip-icon': { ml: 0.5 },
                    '& .MuiChip-label': { px: 0.5 }
                  }}
                />
              )}
              {module.pluginType === 'fullstack' && (
                <Chip
                  icon={<WebIcon sx={{ fontSize: 14 }} />}
                  label="Fullstack"
                  size="small"
                  color="info"
                  sx={{
                    height: compact ? 18 : 22,
                    fontSize: compact ? '0.6rem' : '0.7rem',
                    flexShrink: 0,
                    '& .MuiChip-icon': { ml: 0.5 },
                    '& .MuiChip-label': { px: 0.5 }
                  }}
                />
              )}
            </Box>
            <Switch
              size="small"
              checked={module.enabled}
              onChange={handleToggleStatus}
              onClick={(e) => e.stopPropagation()}
              color="primary"
              sx={{ flexShrink: 0 }}
            />
          </Box>
          
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, mb: compact ? 0.5 : 1 }}>
            {module.author && (
              <Typography variant="body2" color="text.secondary" noWrap>
                <strong>Author:</strong> {module.author}
              </Typography>
            )}
            
            {module.category && (
              <Typography variant="body2" color="text.secondary" noWrap>
                <strong>Category:</strong> {module.category}
              </Typography>
            )}
            
            {module.lastUpdated && (
              <Typography variant="body2" color="text.secondary" noWrap>
                <strong>Updated:</strong> {new Date(module.lastUpdated).toLocaleDateString()}
              </Typography>
            )}
          </Box>
          
          <Box sx={{ flexGrow: 1 }}>
            {module.description && !compact && (
              <Typography 
                variant="body2" 
                color="text.secondary" 
                sx={{ 
                  mb: 1.5,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical'
                }}
              >
                {module.description}
              </Typography>
            )}
          </Box>
          
          {module.tags && module.tags.length > 0 && (
            <Box sx={{ 
              display: 'flex', 
              flexWrap: 'wrap', 
              gap: 0.5,
              mt: 'auto',
              pt: compact ? 0.5 : 1
            }}>
              {module.tags.slice(0, compact ? 2 : 4).map((tag) => (
                <Chip 
                  key={tag} 
                  label={tag} 
                  size="small" 
                  sx={{ 
                    height: compact ? 20 : 24,
                    fontSize: compact ? '0.625rem' : '0.75rem'
                  }} 
                />
              ))}
              {module.tags.length > (compact ? 2 : 4) && (
                <Chip 
                  label={`+${module.tags.length - (compact ? 2 : 4)}`} 
                  size="small" 
                  variant="outlined"
                  sx={{ 
                    height: compact ? 20 : 24,
                    fontSize: compact ? '0.625rem' : '0.75rem'
                  }} 
                />
              )}
            </Box>
          )}
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

export default ModuleCard;
