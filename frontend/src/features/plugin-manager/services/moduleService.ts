import { Module, Plugin, PluginType, DependentPlugin } from '../types';
import ApiService from '../../../services/ApiService';
import { AvailableUpdatesResponse, PluginUpdateInfo } from '../../plugin-installer/types';

/**
 * Service for interacting with the Plugin Manager API
 */
export class ModuleService {
  private static instance: ModuleService;
  private apiService: ApiService;
  private basePath: string;

  private constructor() {
    this.apiService = ApiService.getInstance();
    this.basePath = '/api/v1/plugins';
  }

  /**
   * Get the singleton instance of ModuleService
   */
  public static getInstance(): ModuleService {
    if (!ModuleService.instance) {
      ModuleService.instance = new ModuleService();
    }
    return ModuleService.instance;
  }

  /**
   * Get all modules with optional filtering
   */
  async getModules(options: {
    search?: string;
    category?: string | null;
    tags?: string[];
    pluginType?: PluginType | null;
    page?: number;
    pageSize?: number;
  }): Promise<{ modules: Module[]; totalItems: number }> {
    const { search, category, tags, pluginType, page = 1, pageSize = 16 } = options;

    const params: Record<string, any> = {
      page,
      pageSize
    };

    if (search) {
      params.search = search;
    }

    if (category) {
      params.category = category;
    }

    if (tags && tags.length > 0) {
      params.tags = tags.join(',');
    }

    if (pluginType) {
      params.plugin_type = pluginType;
    }

    try {
      const response = await this.apiService.get(`${this.basePath}/manager`, { params });
      return {
        modules: response.modules || [],
        totalItems: response.totalItems || 0
      };
    } catch (error) {
      console.error('Failed to fetch modules:', error);
      throw error;
    }
  }

  /**
   * Get a specific module by ID
   */
  async getModule(pluginId: string, moduleId: string): Promise<{ module: Module; plugin: Plugin }> {
    try {
      // First test if the router is working
      const testResponse = await this.apiService.get(`/api/v1/plugins/test`);
      console.log('Test endpoint response:', testResponse);
      
      // Use the direct endpoint that was created specifically for this purpose
      const response = await this.apiService.get(`/api/v1/plugins/direct/${pluginId}/modules/${moduleId}`);

      // Check for updates and enrich plugin data
      let plugin = response.plugin;
      if (plugin && plugin.sourceUrl) {
        try {
          const updateInfo = await this.getPluginUpdateInfo(plugin);
          plugin = {
            ...plugin,
            updateAvailable: updateInfo.updateAvailable,
            latestVersion: updateInfo.latestVersion,
            lastUpdateCheck: new Date().toISOString()
          };
        } catch (updateError) {
          console.warn(`Failed to check updates for plugin ${pluginId}:`, updateError);
          // Continue with original plugin data if update check fails
        }
      }

      return {
        module: response.module,
        plugin: plugin
      };
    } catch (error) {
      console.error(`Failed to fetch module ${moduleId}:`, error);
      
      // Create a mock response for testing
      return {
        module: {
          id: moduleId,
          pluginId: pluginId,
          name: "Mock Module",
          displayName: "Mock Module Display Name",
          description: "This is a mock module for testing",
          icon: "mock-icon",
          category: "Mock Category",
          enabled: true,
          priority: 1,
          tags: ["mock", "test"],
          props: {},
          configFields: {},
          messages: {},
          requiredServices: [],
          layout: {},
          dependencies: []
        },
        plugin: {
          id: pluginId,
          name: "Mock Plugin",
          description: "This is a mock plugin for testing",
          version: "1.0.0",
          type: "mock",
          enabled: true,
          icon: "mock-icon",
          category: "Mock Category",
          status: "active",
          official: true,
          author: "Mock Author",
          lastUpdated: new Date().toISOString(),
          compatibility: "1.0.0",
          downloads: 0,
          scope: "mock",
          bundleMethod: "mock",
          bundleLocation: "mock",
          isLocal: true,
          configFields: {},
          messages: {},
          dependencies: [],
          modules: [],
          // Add source URL fields for testing update/delete buttons
          sourceType: "github",
          sourceUrl: "https://github.com/example/mock-plugin",
          updateCheckUrl: "https://api.github.com/repos/example/mock-plugin/releases/latest",
          lastUpdateCheck: new Date().toISOString(),
          updateAvailable: true,
          latestVersion: "1.1.0",
          installationType: "remote",
          permissions: []
        }
      };
    }
  }

  /**
   * Get all modules for a specific plugin
   */
  async getModulesByPlugin(pluginId: string): Promise<Module[]> {
    try {
      const response = await this.apiService.get(`${this.basePath}/${pluginId}/modules`);
      return response.modules || [];
    } catch (error) {
      console.error(`Failed to fetch modules for plugin ${pluginId}:`, error);
      throw error;
    }
  }

  /**
   * Toggle a module's enabled status
   */
  async toggleModuleStatus(pluginId: string, moduleId: string, enabled: boolean): Promise<void> {
    try {
      await this.apiService.patch(`${this.basePath}/${pluginId}/modules/${moduleId}`, {
        enabled
      });
    } catch (error) {
      console.error(`Failed to toggle module ${moduleId} status:`, error);
      throw error;
    }
  }

  /**
   * Toggle a plugin's enabled status
   */
  async togglePluginStatus(pluginId: string, enabled: boolean): Promise<void> {
    try {
      await this.apiService.patch(`${this.basePath}/${pluginId}`, {
        enabled
      });
    } catch (error) {
      console.error(`Failed to toggle plugin ${pluginId} status:`, error);
      throw error;
    }
  }

  /**
   * Get all available categories
   */
  async getCategories(): Promise<string[]> {
    try {
      const response = await this.apiService.get(`${this.basePath}/categories`);
      return response.categories || [];
    } catch (error) {
      console.error('Failed to fetch categories:', error);
      // Return empty array on error
      return [];
    }
  }

  /**
   * Get all available tags
   */
  async getTags(): Promise<string[]> {
    try {
      const response = await this.apiService.get(`${this.basePath}/tags`);
      return response.tags || [];
    } catch (error) {
      console.error('Failed to fetch tags:', error);
      // Return empty array on error
      return [];
    }
  }

  /**
   * Get plugins that depend on a backend plugin
   * Used to show dependency relationships and cascade disable warnings
   */
  async getDependentPlugins(pluginSlug: string): Promise<DependentPlugin[]> {
    try {
      const response = await this.apiService.get(`${this.basePath}/${pluginSlug}/dependents`);
      return response.dependents || [];
    } catch (error) {
      console.error(`Failed to fetch dependent plugins for ${pluginSlug}:`, error);
      return [];
    }
  }

  /**
   * Disable a backend plugin with cascade disable of dependent frontend plugins
   * Returns the list of plugins that were cascade-disabled
   */
  async disablePluginWithCascade(pluginId: string): Promise<{
    cascadeDisabled: DependentPlugin[];
  }> {
    try {
      const pluginSlug = this.extractPluginSlugFromId(pluginId);
      const response = await this.apiService.post(`${this.basePath}/${pluginSlug}/disable-cascade`);
      return {
        cascadeDisabled: response.cascade_disabled || []
      };
    } catch (error) {
      console.error(`Failed to cascade disable plugin ${pluginId}:`, error);
      throw error;
    }
  }

  /**
   * Check if disabling a plugin would affect dependent plugins
   * Returns the list of plugins that would be cascade-disabled
   */
  async checkCascadeDisable(pluginId: string): Promise<DependentPlugin[]> {
    try {
      const pluginSlug = this.extractPluginSlugFromId(pluginId);
      const response = await this.apiService.get(`${this.basePath}/${pluginSlug}/cascade-preview`);
      return response.would_disable || [];
    } catch (error) {
      console.error(`Failed to check cascade disable for ${pluginId}:`, error);
      return [];
    }
  }

  /**
   * Update a plugin to the latest version
   */
  async updatePlugin(pluginId: string): Promise<void> {
    try {
      // Extract plugin slug from plugin ID (format: {user_id}_{plugin_slug})
      const pluginSlug = this.extractPluginSlugFromId(pluginId);

      await this.apiService.post(`/api/v1/plugins/${pluginSlug}/update`);

      // After successful update, clear all caches and force hard refresh
      console.log('Plugin updated successfully, clearing all caches...');
      await this.clearAllCaches();

    } catch (error) {
      console.error(`Failed to update plugin ${pluginId}:`, error);
      throw error;
    }
  }

  /**
   * Clear all caches to force plugin reload
   */
  private async clearAllCaches(): Promise<void> {
    try {
      console.log('Clearing all caches for plugin update...');

      // 1. Clear Module Federation cache
      if (window.__webpack_require__ && window.__webpack_require__.cache) {
        console.log('Clearing webpack module cache...');
        Object.keys(window.__webpack_require__.cache).forEach(key => {
          delete window.__webpack_require__.cache[key];
        });
      }

      // 2. Clear browser caches using Cache API
      if ('caches' in window) {
        console.log('Clearing browser caches...');
        const cacheNames = await caches.keys();
        await Promise.all(
          cacheNames.map(cacheName => caches.delete(cacheName))
        );
      }

      // 3. Clear localStorage plugin-related data
      console.log('Clearing localStorage plugin data...');
      Object.keys(localStorage).forEach(key => {
        if (key.includes('plugin') || key.includes('module') || key.includes('remote')) {
          localStorage.removeItem(key);
        }
      });

      // 4. Clear sessionStorage
      console.log('Clearing sessionStorage...');
      sessionStorage.clear();

      // 5. Force hard refresh (equivalent to Ctrl+R in console)
      console.log('Forcing hard refresh...');
      //window.location.reload(true);
      const url = new URL(window.location.href);
      url.searchParams.set('cacheBust', Date.now().toString());
      window.location.replace(url.toString());


    } catch (error) {
      console.error('Error clearing caches:', error);
      // Fallback to regular reload
      window.location.reload();
    }
  }

  /**
   * Delete/uninstall a plugin completely
   */
  async deletePlugin(pluginId: string): Promise<void> {
    try {
      // Extract plugin slug from plugin ID (format: {user_id}_{plugin_slug})
      const pluginSlug = this.extractPluginSlugFromId(pluginId);

      await this.apiService.delete(`/api/v1/plugins/${pluginSlug}/uninstall`);
    } catch (error) {
      console.error(`Failed to delete plugin ${pluginId}:`, error);
      throw error;
    }
  }

  /**
   * Extract plugin slug from plugin ID
   * Plugin ID format: {user_id}_{plugin_slug}
   */
  private extractPluginSlugFromId(pluginId: string): string {
    const parts = pluginId.split('_');
    if (parts.length >= 2) {
      // Remove the first part (user_id) and join the rest as plugin_slug
      return parts.slice(1).join('_');
    }
    // Fallback: use the whole ID as slug
    return pluginId;
  }

  /**
   * Get update information for a specific plugin
   */
  private async getPluginUpdateInfo(plugin: Plugin): Promise<{ updateAvailable: boolean; latestVersion?: string }> {
    try {
      // Check if we have the necessary information to check for updates
      if (!plugin.sourceUrl || !plugin.version || !plugin.id) {
        console.log('Missing required fields for update check:', {
          hasSourceUrl: !!plugin.sourceUrl,
          hasVersion: !!plugin.version,
          hasId: !!plugin.id
        });
        return { updateAvailable: false };
      }

      // Extract plugin slug from plugin ID (format: {user_id}_{plugin_slug})
      const pluginSlug = this.extractPluginSlugFromId(plugin.id);
      console.log('Checking updates for plugin:', plugin.id, '→ slug:', pluginSlug);

      // Call the backend API to check for updates for this specific plugin
      const endpoint = `/api/v1/plugins/${pluginSlug}/update/available`;
      console.log('Calling endpoint:', endpoint);
      const response = await this.apiService.get(endpoint);
      console.log('Update check response:', response);
      console.log('Response data:', response.data);
      console.log('Update available from API:', response.data?.update_available);

      if (response.status === 'success' && response.data) {
        const updateData = response.data;
        console.log('Update data:', updateData);

        if (updateData.update_available && updateData.latest_version) {
          console.log('Update available!', updateData.latest_version);
          return {
            updateAvailable: true,
            latestVersion: updateData.latest_version
          };
        }
      }

      console.log('No update available');
      return { updateAvailable: false };
    } catch (error) {
      console.error('Failed to get plugin update info:', error);
      return { updateAvailable: false };
    }
  }

  /**
   * Compare two version strings to determine if the first is newer than the second
   */
  private isVersionNewer(version1: string, version2: string): boolean {
    try {
      // Remove 'v' prefix if present
      const v1 = version1.replace(/^v/, '');
      const v2 = version2.replace(/^v/, '');

      // Split versions into parts
      const parts1 = v1.split('.').map(n => parseInt(n, 10));
      const parts2 = v2.split('.').map(n => parseInt(n, 10));

      // Compare each part
      const maxLength = Math.max(parts1.length, parts2.length);
      for (let i = 0; i < maxLength; i++) {
        const part1 = parts1[i] || 0;
        const part2 = parts2[i] || 0;

        if (part1 > part2) return true;
        if (part1 < part2) return false;
      }

      return false; // Versions are equal
    } catch (error) {
      console.error('Error comparing versions:', error);
      return false;
    }
  }

  /**
   * Check for available updates for all plugins
   */
  async checkForUpdates(): Promise<any[]> {
    try {
      const response = await this.apiService.get('/api/v1/plugins/updates/available');
      console.debug('[ModuleService] checkForUpdates response', response);
      return response.data?.available_updates || [];
    } catch (error) {
      console.error('Failed to check for updates:', error);
      throw error;
    }
  }
}

// Export a singleton instance
export const moduleService = ModuleService.getInstance();

export default moduleService;

