import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Container, Typography, Box, Paper, Alert, AlertTitle, Button } from '@mui/material';
import { Add as AddIcon } from '@mui/icons-material';
import ModuleSearch from '../features/plugin-manager/components/ModuleSearch';
import ModuleFilters from '../features/plugin-manager/components/ModuleFilters';
import ModuleGrid from '../features/plugin-manager/components/ModuleGrid';
import PluginTypeTabs from '../features/plugin-manager/components/PluginTypeTabs';
import useModules from '../features/plugin-manager/hooks/useModules';
import useModuleFilters from '../features/plugin-manager/hooks/useModuleFilters';
import { Module, PluginType } from '../features/plugin-manager/types';

/**
 * The main page for browsing and searching modules
 */
const PluginManagerPage: React.FC = () => {
  console.log('PluginManagerPage rendering');
  const renderCount = useRef(0);
  
  useEffect(() => {
    renderCount.current += 1;
    console.log(`PluginManagerPage rendered ${renderCount.current} times`);
  });
  
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selectedPluginType, setSelectedPluginType] = useState<PluginType | 'all'>('all');
  const pageSize = 16; // 4x4 grid

  const {
    categories,
    tags,
    selectedCategory,
    selectedTags,
    setSelectedCategory,
    setSelectedTags
  } = useModuleFilters();

  const {
    modules,
    totalModules,
    loading,
    error,
    toggleModuleStatus
  } = useModules({
    search: searchQuery,
    category: selectedCategory,
    tags: selectedTags,
    pluginType: selectedPluginType === 'all' ? null : selectedPluginType,
    page,
    pageSize
  });

  const handleSearch = useCallback((query: string) => {
    console.log(`Search query changed to: ${query}`);
    setSearchQuery(query);
    setPage(1); // Reset to first page on new search
  }, []);

  const handlePluginTypeChange = useCallback((type: PluginType | 'all') => {
    console.log(`Plugin type changed to: ${type}`);
    setSelectedPluginType(type);
    setPage(1); // Reset to first page on type change
  }, []);

  const handleModuleClick = useCallback((module: Module) => {
    console.log(`Module clicked: ${module.name}`);
    navigate(`/plugin-manager/${module.pluginId}/${module.id}`);
  }, [navigate]);

  const handleToggleStatus = useCallback(async (module: Module, enabled: boolean) => {
    console.log(`Toggle status for module ${module.name} to ${enabled}`);
    await toggleModuleStatus(module.id, module.pluginId, enabled);
  }, [toggleModuleStatus]);

  const handlePageChange = useCallback((newPage: number) => {
    console.log(`Page changed to: ${newPage}`);
    setPage(newPage);
  }, []);

  const handleInstallPlugins = useCallback(() => {
    navigate('/plugin-installer');
  }, [navigate]);

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h4" component="h1">
          Plugin Manager
        </Typography>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={handleInstallPlugins}
          sx={{ minWidth: 160 }}
        >
          Install Plugins
        </Button>
      </Box>
      
      <Paper sx={{ p: 3, mb: 3 }}>
        <PluginTypeTabs
          selectedType={selectedPluginType}
          onTypeChange={handlePluginTypeChange}
        />

        <ModuleSearch onSearch={handleSearch} />

        <ModuleFilters
          categories={categories}
          selectedCategory={selectedCategory}
          onCategoryChange={setSelectedCategory}
          tags={tags}
          selectedTags={selectedTags}
          onTagsChange={setSelectedTags}
        />
      </Paper>
      
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <AlertTitle>Error</AlertTitle>
          {error.message}
        </Alert>
      )}
      
      <Box sx={{ mb: 4 }}>
        <ModuleGrid
          modules={modules}
          onModuleClick={handleModuleClick}
          onToggleStatus={handleToggleStatus}
          loading={loading}
          pagination={{
            page,
            pageSize,
            totalItems: totalModules,
            onPageChange: handlePageChange
          }}
        />
      </Box>
    </Container>
  );
};

export default PluginManagerPage;
