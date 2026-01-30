import { useState, useEffect, useCallback, useRef } from 'react';
import { Module, PluginType } from '../types';
import moduleService from '../services/moduleService';

interface UseModulesOptions {
  search?: string;
  category?: string | null;
  tags?: string[];
  pluginType?: PluginType | null;
  page?: number;
  pageSize?: number;
}

interface UseModulesResult {
  modules: Module[];
  totalModules: number;
  loading: boolean;
  error: Error | null;
  toggleModuleStatus: (moduleId: string, pluginId: string, enabled: boolean) => Promise<void>;
  refetch: () => Promise<void>;
}

/**
 * Hook for fetching and managing modules
 * 
 * Note: Currently using mock data until backend API is implemented
 */
export const useModules = (options: UseModulesOptions = {}): UseModulesResult => {
  const [modules, setModules] = useState<Module[]>([]);
  const [totalModules, setTotalModules] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const fetchCount = useRef(0);

  // Memoize options to prevent unnecessary re-renders
  const memoizedOptions = useCallback(() => {
    return {
      search: options.search || '',
      category: options.category || null,
      tags: options.tags || [],
      pluginType: options.pluginType || null,
      page: options.page || 1,
      pageSize: options.pageSize || 16
    };
  }, [options.search, options.category, options.tags?.join(','), options.pluginType, options.page, options.pageSize]);

  const fetchModules = useCallback(async () => {
    try {
      // Increment fetch count to track how many times this function is called
      fetchCount.current += 1;
      console.log(`Fetching modules (call #${fetchCount.current})`, memoizedOptions());
      
      setLoading(true);
      setError(null);
      
      // Use the moduleService to fetch modules
      const result = await moduleService.getModules(memoizedOptions());
      
      setModules(result.modules);
      setTotalModules(result.totalItems);
      console.log(`Fetched ${result.modules.length} modules`);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch modules'));
      console.error('Error fetching modules:', err);
    } finally {
      setLoading(false);
    }
  }, [memoizedOptions]);

  useEffect(() => {
    console.log('useEffect in useModules triggered');
    fetchModules();
  }, [fetchModules]);

  const toggleModuleStatus = useCallback(async (moduleId: string, pluginId: string, enabled: boolean) => {
    try {
      await moduleService.toggleModuleStatus(pluginId, moduleId, enabled);
      
      // Update the local state
      setModules(prevModules => 
        prevModules.map(module => 
          module.id === moduleId && module.pluginId === pluginId
            ? { ...module, enabled }
            : module
        )
      );
    } catch (err) {
      console.error(`Error toggling module ${moduleId} status:`, err);
      throw err;
    }
  }, []);

  return {
    modules,
    totalModules,
    loading,
    error,
    toggleModuleStatus,
    refetch: fetchModules
  };
};

export default useModules;
