import { useEffect, useState } from 'react';
import { Box, Toolbar, useMediaQuery, useTheme } from '@mui/material';
import { Outlet, useLocation } from 'react-router-dom';
import Header from './Header';
import Sidebar from './Sidebar';
import { ThemeSelector } from '../ThemeSelector';
import { useSettings } from '../../contexts/ServiceContext';

const DRAWER_WIDTH = 240;

const DashboardLayout = () => {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'));
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);
  const settingsService = useSettings();
  const location = useLocation();
  const defaultCopyright = { text: 'AIs can make mistakes. Check important info.' };
  const defaultWhiteLabel = {
    COMMUNITY: { label: 'BrainDrive Community', url: 'https://tinyurl.com/yc2u5v2a' },
    DOCUMENTATION: { label: 'BrainDrive Docs', url: 'https://tinyurl.com/ewajc7k3' },
  };
  const [copyright, setCopyright] = useState(defaultCopyright);
  const [whiteLabel, setWhiteLabel] = useState(defaultWhiteLabel);

  // Update sidebar state when screen size changes
  useEffect(() => {
    setSidebarOpen(!isMobile);
  }, [isMobile]);

  // Load copyright setting
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const value = await settingsService.getSetting<any>('copyright_settings');
        if (!active) return;
        if (value) {
          if (typeof value === 'string') {
            try {
              const parsed = JSON.parse(value);
              if (parsed && parsed.text) {
                // Only update if we have text; else keep default
                // eslint-disable-next-line @typescript-eslint/no-unsafe-argument
                setCopyright({ text: parsed.text });
              }
            } catch {
              // Ignore parse errors, keep default
            }
          } else if (typeof value === 'object' && value.text) {
            // eslint-disable-next-line @typescript-eslint/no-unsafe-argument
            setCopyright({ text: value.text });
          }
        }
      } catch {
        // Keep default on error
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load white-label settings for footer links
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const value = await settingsService.getSetting<any>('white_label_settings');
        if (!active) return;

        const parsed =
          typeof value === 'string'
            ? (() => {
                try {
                  return JSON.parse(value);
                } catch {
                  return undefined;
                }
              })()
            : value;

        if (parsed && typeof parsed === 'object') {
          const next = {
            COMMUNITY: parsed.COMMUNITY ?? defaultWhiteLabel.COMMUNITY,
            DOCUMENTATION: parsed.DOCUMENTATION ?? defaultWhiteLabel.DOCUMENTATION,
          };
          setWhiteLabel(next);
        }
      } catch {
        // Keep defaults on error
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleToggleSidebar = () => {
    setSidebarOpen(!sidebarOpen);
  };

  const isDynamicPage = location.pathname.startsWith('/pages/');

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <Header 
        onToggleSidebar={handleToggleSidebar} 
        rightContent={<ThemeSelector />}
        sidebarOpen={sidebarOpen}
      />
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        drawerWidth={DRAWER_WIDTH}
      />
      <Box
        component="main"
        sx={{
          // CSS vars for downstream layout sizing
          '--app-header-h': { xs: '56px', sm: '64px' },
          '--app-footer-h': '32px',
          flexGrow: 1,
          // Reduce padding for dynamic pages to maximize real estate
          p: isDynamicPage ? { xs: 0, sm: 0 } : { xs: 1, sm: 2 },
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          marginLeft: {
            xs: 0,
            sm: sidebarOpen ? 0 : `-${DRAWER_WIDTH}px`
          },
          transition: theme.transitions.create(['margin'], {
            easing: theme.transitions.easing.easeOut,
            duration: theme.transitions.duration.enteringScreen,
          }),
        }}
      >
        <Toolbar /> {/* Spacer for header */}
        <Box
          sx={{
            maxWidth: '100%',
            overflow: 'hidden',
            flexGrow: 1,
          }}
        >
          <Outlet />
        </Box>
        <Box
          component="footer"
          sx={{
            borderTop: `1px solid ${theme.palette.divider}`,
            color: 'text.secondary',
            typography: 'caption',
            pt: 1,
            mt: 1,
            px: 1,
          }}
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexWrap: 'wrap',
              gap: 1.5,
            }}
          >
            <Box sx={{ textAlign: 'center' }}>{copyright.text}</Box>
            <Box component="span" sx={{ color: 'inherit' }} aria-hidden="true">
              •
            </Box>
            <Box
              sx={{
                display: 'flex',
                gap: 1.5,
                alignItems: 'center',
              }}
            >
              <a
                href={whiteLabel.COMMUNITY.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: 'inherit', textDecoration: 'none' }}
              >
                {whiteLabel.COMMUNITY.label}
              </a>
              <Box component="span" sx={{ color: 'inherit' }} aria-hidden="true">
                •
              </Box>
              <a
                href={whiteLabel.DOCUMENTATION.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: 'inherit', textDecoration: 'none' }}
              >
                {whiteLabel.DOCUMENTATION.label}
              </a>
            </Box>
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default DashboardLayout;
