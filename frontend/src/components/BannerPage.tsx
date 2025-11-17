import React from 'react';
import { Box, Typography, useTheme } from '@mui/material';
import { Link } from 'react-router-dom';

interface BannerPageProps {
  routeName?: string;
  routeDescription?: string;
  showHelp?: boolean;
}

/**
 * A banner page component that displays the BrainDrive logo and tagline.
 * Used as a default page for routes that don't have a default page or component assigned.
 */
export const BannerPage: React.FC<BannerPageProps> = ({ 
  routeName, 
  routeDescription,
  showHelp = true 
}) => {
  const theme = useTheme();

  return (
    <Box sx={{ 
      p: 4, 
      textAlign: 'center', 
      display: 'flex', 
      flexDirection: 'column', 
      alignItems: 'center', 
      gap: 3,
      minHeight: '70vh',
      justifyContent: 'center',
      bgcolor: 'background.default'
    }}>
      {/* BrainDrive Logo */}
      <Box sx={{ width: '300px', maxWidth: '80%', mb: 2 }}>
        <img 
          src={theme.palette.mode === 'dark' ? "/braindrive/braindrive-dark.svg" : "/braindrive/braindrive-light.svg"} 
          alt="BrainDrive Logo" 
          style={{ width: '100%', height: 'auto' }}
        />
      </Box>
      
      <Typography variant="h5" fontWeight="bold" color="text.primary" sx={{ mt: 1 }}>
        Your AI. Your Rules.
      </Typography>
      
      {routeName && (
        <Typography variant="h4" color="primary" sx={{ fontWeight: 'bold', mt: 4 }}>
          {routeName}
        </Typography>
      )}
      
      {routeDescription && (
        <Typography variant="body1" color="text.secondary" sx={{ 
          mt: 2, 
          maxWidth: '600px', 
          fontStyle: 'italic',
          fontSize: '1.1rem'
        }}>
          "{routeDescription}"
        </Typography>
      )}
      
      <Typography variant="body1" color="text.primary" sx={{ mt: 2 }}>
        Visit <Link to="/plugin-studio" style={{ color: theme.palette.primary.main, textDecoration: 'underline' }}>BrainDrive Page Builder</Link> to create your first page.
      </Typography>
      
      {showHelp && (
        <Box sx={{ 
          mt: 4, 
          p: 3, 
          bgcolor: 'background.paper', 
          borderRadius: 2, 
          maxWidth: '600px', 
          boxShadow: 2,
          border: '1px solid',
          borderColor: 'divider'
        }}>
          <Typography variant="body1" color="text.secondary">
            To assign default content to this route, go to Route Management and edit this route.
          </Typography>
        </Box>
      )}
    </Box>
  );
};

export default BannerPage;
