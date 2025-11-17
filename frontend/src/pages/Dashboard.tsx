import React from 'react';
import {
  Box,
  Grid,
  Paper,
  Typography,
  Card,
  CardContent,
  CardActionArea,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  Stack,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import ExtensionIcon from '@mui/icons-material/Extension';
import SettingsIcon from '@mui/icons-material/Settings';
import BuildIcon from '@mui/icons-material/Build';
import HomeOutlinedIcon from '@mui/icons-material/HomeOutlined';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import GroupsOutlinedIcon from '@mui/icons-material/GroupsOutlined';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { alpha, useTheme } from '@mui/material/styles';
import { useAuth } from '../contexts/AuthContext';
import { PluginUpdatesPanel, usePluginUpdateFeed } from '../features/plugin-manager';

const ExternalLinksCard = () => {
  const theme = useTheme();
  const hoverBg = alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.16 : 0.08);
  const focusRing = alpha(theme.palette.primary.main, 0.35);

  const links = [
    {
      label: 'BrainDrive Home',
      href: 'https://www.braindrive.ai/',
      icon: <HomeOutlinedIcon fontSize="small" />,
    },
    {
      label: 'Documentation',
      href: 'https://docs.braindrive.ai/',
      icon: <DescriptionOutlinedIcon fontSize="small" />,
    },
    {
      label: 'Community',
      href: 'https://community.braindrive.ai/',
      icon: <GroupsOutlinedIcon fontSize="small" />,
    },
  ];

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack spacing={0.5} sx={{ mb: 1.5 }}>
          <Typography variant="h6">External Links</Typography>
          <Typography variant="body2" color="text.secondary">
            Opens in a new tab
          </Typography>
        </Stack>
        <List disablePadding>
          {links.map((link, index) => (
            <React.Fragment key={link.href}>
              {index > 0 && <Divider component="li" />}
              <ListItemButton
                component="a"
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open ${link.label} in a new tab`}
                sx={{
                  borderRadius: 1,
                  px: 1,
                  py: 1.25,
                  minHeight: 56,
                  gap: 1,
                  transition: theme.transitions.create(['background-color', 'color', 'box-shadow'], {
                    duration: theme.transitions.duration.shorter,
                  }),
                  '&:hover': {
                    backgroundColor: hoverBg,
                    color: 'primary.main',
                  },
                  '&:focus-visible': {
                    outline: 'none',
                    backgroundColor: hoverBg,
                    color: 'primary.main',
                    boxShadow: `0 0 0 2px ${focusRing}`,
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 36, color: 'inherit' }}>{link.icon}</ListItemIcon>
                <ListItemText
                  primary={link.label}
                  primaryTypographyProps={{ variant: 'body1', fontWeight: 600 }}
                />
                <OpenInNewIcon fontSize="small" sx={{ color: 'inherit' }} />
              </ListItemButton>
            </React.Fragment>
          ))}
        </List>
      </CardContent>
    </Card>
  );
};

const Dashboard = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const updateFeed = usePluginUpdateFeed();

  const cards = [
    {
      title: 'Page Builder',
      description: 'Create and manage your plugins',
      icon: <ExtensionIcon sx={{ fontSize: 40 }} />,
      path: '/plugin-studio',
      color: '#2196F3'
    },
    {
      title: 'Settings',
      description: 'Configure your preferences',
      icon: <SettingsIcon sx={{ fontSize: 40 }} />,
      path: '/settings',
      color: '#FF9800'
    },
    {
      title: 'Plugin Manager',
      description: 'Browse and manage installed plugins',
      icon: <BuildIcon sx={{ fontSize: 40 }} />,
      path: '/plugin-manager',
      color: '#4CAF50'
    }
  ];

  return (
    <Box sx={{ p: 3 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h4" gutterBottom>
          Welcome back, {user?.username || 'User'}!
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Get started by creating a new plugin or managing your existing ones.
        </Typography>
      </Paper>

      <Grid container spacing={3} sx={{ mb: 3 }}>
        {cards.map((card) => (
          <Grid item xs={12} sm={6} md={4} key={card.title}>
            <Card>
              <CardActionArea onClick={() => navigate(card.path)}>
                <CardContent sx={{ textAlign: 'center', py: 4 }}>
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mb: 2,
                      width: 80,
                      height: 80,
                      borderRadius: '50%',
                      backgroundColor: `${card.color}20`,
                      margin: '0 auto',
                    }}
                  >
                    {React.cloneElement(card.icon, {
                      sx: { fontSize: 40, color: card.color }
                    })}
                  </Box>
                  <Typography variant="h6" gutterBottom>
                    {card.title}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {card.description}
                  </Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <PluginUpdatesPanel
            updates={updateFeed.updates}
            status={updateFeed.status}
            error={updateFeed.error}
            lastChecked={updateFeed.lastChecked}
            isUpdatingAll={updateFeed.isUpdatingAll}
            batchProgress={updateFeed.batchProgress}
            onUpdate={(pluginId) => void updateFeed.triggerUpdate(pluginId)}
            onUpdateAll={() => void updateFeed.triggerUpdateAll()}
            onRefresh={() => void updateFeed.refresh()}
            onDismiss={updateFeed.dismiss}
            onRetry={() => void updateFeed.retry()}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <ExternalLinksCard />
        </Grid>
      </Grid>
    </Box>
  );
};

export default Dashboard;
