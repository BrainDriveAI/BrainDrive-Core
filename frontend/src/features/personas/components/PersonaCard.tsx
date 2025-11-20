import React, { useEffect, useRef, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Switch,
  CardActionArea,
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText
} from '@mui/material';
import {
  MoreVert as MoreVertIcon,
  Edit as EditIcon,
  Delete as DeleteIcon
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { Persona } from '../types';

interface PersonaCardProps {
  persona: Persona;
  onClick?: (persona: Persona) => void;
  onToggleStatus?: (persona: Persona, enabled: boolean) => void;
  onDelete?: (persona: Persona) => void;
  compact?: boolean;
}

/**
 * A reusable card component that displays key information about a persona
 */
export const PersonaCard: React.FC<PersonaCardProps> = ({
  persona,
  onClick,
  onToggleStatus,
  onDelete,
  compact = false
}) => {
  const navigate = useNavigate();
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const menuOpen = Boolean(anchorEl);
  const clickTimeoutRef = useRef<number | null>(null);
  const CLICK_DELAY_MS = 350;

  const clearClickTimeout = () => {
    if (clickTimeoutRef.current) {
      window.clearTimeout(clickTimeoutRef.current);
      clickTimeoutRef.current = null;
    }
  };

  useEffect(() => {
    return () => clearClickTimeout();
  }, []);

  const handleCardClick = () => {
    clearClickTimeout();

    if (onClick) {
      clickTimeoutRef.current = window.setTimeout(() => {
        onClick(persona);
        clickTimeoutRef.current = null;
      }, CLICK_DELAY_MS);
    }
  };

  const handleToggleStatus = (event: React.ChangeEvent<HTMLInputElement>) => {
    event.stopPropagation();
    if (onToggleStatus) {
      onToggleStatus(persona, event.target.checked);
    }
  };

  const handleMenuClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setAnchorEl(event.currentTarget);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
  };

  const handleEdit = () => {
    handleMenuClose();
    navigate(`/personas/${persona.id}/edit`);
  };

  const handleDelete = () => {
    handleMenuClose();
    if (onDelete) {
      onDelete(persona);
    }
  };

  const handleCardDoubleClick = () => {
    clearClickTimeout();
    handleEdit();
  };

  // Truncate system prompt for preview
  const truncatedPrompt = persona.system_prompt.length > 100 
    ? persona.system_prompt.substring(0, 100) + '...'
    : persona.system_prompt;

  return (
    <Card 
      sx={{ 
        height: compact ? '100%' : 280,
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
        onClick={handleCardClick}
        onDoubleClick={handleCardDoubleClick}
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
            <Box sx={{ display: 'flex', alignItems: 'center', flexGrow: 1 }}>
              {persona.avatar && (
                <Box sx={{ mr: 1, fontSize: '1.5rem' }}>
                  {persona.avatar}
                </Box>
              )}
              <Typography variant={compact ? "subtitle1" : "h6"} component="h2" noWrap sx={{ fontWeight: 'bold' }}>
                {persona.name}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Switch
                size="small"
                checked={persona.is_active}
                onChange={handleToggleStatus}
                onClick={(e) => e.stopPropagation()}
                color="primary"
              />
              <IconButton
                size="small"
                onClick={handleMenuClick}
                sx={{ opacity: 0.7, '&:hover': { opacity: 1 } }}
              >
                <MoreVertIcon fontSize="small" />
              </IconButton>
            </Box>
          </Box>
          
          {persona.description && (
            <Typography 
              variant="body2" 
              color="text.secondary" 
              sx={{ 
                mb: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: '-webkit-box',
                WebkitLineClamp: compact ? 1 : 2,
                WebkitBoxOrient: 'vertical'
              }}
            >
              {persona.description}
            </Typography>
          )}
          
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, mb: compact ? 0.5 : 1 }}>
            <Typography variant="body2" color="text.secondary" noWrap>
              <strong>Created:</strong> {new Date(persona.created_at).toLocaleDateString()}
            </Typography>
            
            {persona.model_settings && Object.keys(persona.model_settings).length > 0 && (
              <Typography variant="body2" color="text.secondary" noWrap>
                <strong>Model Settings:</strong> {Object.keys(persona.model_settings).length} configured
              </Typography>
            )}
          </Box>
          
          {!compact && (
            <Box sx={{ flexGrow: 1, mb: 1 }}>
              <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 'bold', mb: 0.5 }}>
                System Prompt:
              </Typography>
              <Typography 
                variant="body2" 
                color="text.secondary" 
                sx={{ 
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  fontStyle: 'italic',
                  backgroundColor: 'rgba(0, 0, 0, 0.04)',
                  p: 1,
                  borderRadius: 1,
                  fontSize: '0.75rem'
                }}
              >
                {truncatedPrompt}
              </Typography>
            </Box>
          )}
          
          {persona.tags && persona.tags.length > 0 && (
            <Box sx={{ 
              display: 'flex', 
              flexWrap: 'wrap', 
              gap: 0.5,
              mt: 'auto',
              pt: compact ? 0.5 : 1
            }}>
              {persona.tags.slice(0, compact ? 2 : 4).map((tag) => (
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
              {persona.tags.length > (compact ? 2 : 4) && (
                <Chip 
                  label={`+${persona.tags.length - (compact ? 2 : 4)}`} 
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
      
      {/* Action Menu */}
      <Menu
        anchorEl={anchorEl}
        open={menuOpen}
        onClose={handleMenuClose}
        onClick={(e) => e.stopPropagation()}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
      >
        <MenuItem onClick={handleEdit}>
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Edit</ListItemText>
        </MenuItem>
        <MenuItem onClick={handleDelete} sx={{ color: 'error.main' }}>
          <ListItemIcon>
            <DeleteIcon fontSize="small" color="error" />
          </ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>
    </Card>
  );
};

export default PersonaCard;
