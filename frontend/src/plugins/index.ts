import { DynamicPluginConfig, DynamicModuleConfig } from '../types/index';

import { remotePluginService } from '../services/remotePluginService';
import { LoadedRemotePlugin } from '../types/remotePlugin';

// Plugin type configurations for local plugins
const localPluginConfigs: Record<string, DynamicPluginConfig> = {};

// Combined plugin configs (local + remote)
// Initialize with an empty object to prevent loading local plugins
let pluginConfigs: Record<string, DynamicPluginConfig> = {}; // Empty object instead of { ...localPluginConfigs }

// Plugin instance registry - maps instance IDs to plugin type IDs
// Local plugin mappings removed to prevent loading local plugins
const pluginInstances: Record<string, string> = {
  // Local plugin mappings removed
};

// Get plugin config for an instance
export const getPluginConfigForInstance = (instanceId: string): DynamicPluginConfig | undefined => {
  console.log('getPluginConfigForInstance - instanceId:', instanceId);
  console.log('getPluginConfigForInstance - pluginInstances:', pluginInstances);
  console.log('getPluginConfigForInstance - available pluginConfigs:', Object.keys(pluginConfigs));
  
  // Try direct mapping first
  let pluginId = pluginInstances[instanceId];
  
  // If no mapping exists, try to use the instanceId as a direct plugin ID
  if (!pluginId) {
    console.log(`No mapping found for instance ${instanceId}, trying as direct plugin ID`);
    pluginId = instanceId;
  }
  
  console.log('getPluginConfigForInstance - resolved pluginId:', pluginId);
  
  const config = pluginConfigs[pluginId];
  
  if (!config) {
    console.error(`Plugin config not found for instance ${instanceId}, pluginId: ${pluginId}`);
    console.log('Available plugin configs:', Object.keys(pluginConfigs));
    return undefined;
  }
  
  console.log('getPluginConfigForInstance - config found:', config);
  
  return config;
};

/**
 * Get module config for an instance
 * @param instanceId The instance ID
 * @param moduleId The module ID (optional)
 * @returns The module config for the instance
 */
export const getModuleConfigForInstance = (instanceId: string, moduleId?: string): DynamicModuleConfig | undefined => {
  // console.log('getModuleConfigForInstance - instanceId:', instanceId, 'moduleId:', moduleId);
  
  const pluginConfig = getPluginConfigForInstance(instanceId);
  
  if (!pluginConfig) {
    console.error(`Plugin config not found for instance ${instanceId}`);
    return undefined;
  }
  
  if (!pluginConfig.modules || pluginConfig.modules.length === 0) {
    console.error(`Plugin ${instanceId} has no modules`);
    return undefined;
  }
  
  // If no moduleId is provided, return the first module
  if (!moduleId) {
    // console.log('getModuleConfigForInstance - no moduleId provided, using first module');
    return pluginConfig.modules[0];
  }
  
  const moduleConfig = pluginConfig.modules.find(m => m.id === moduleId || m.name === moduleId);
  
  if (!moduleConfig) {
    console.error(`Module ${moduleId} not found in plugin ${instanceId}`);
    // console.log('Available modules:', pluginConfig.modules.map(m => ({ id: m.id, name: m.name })));
    return undefined;
  }
  
  // console.log('getModuleConfigForInstance - moduleConfig:', moduleConfig);
  
  return moduleConfig;
};

/**
 * Get all modules from all plugins
 * @returns Array of all modules with their plugin IDs
 */
export const getAllModules = (): { pluginId: string; module: DynamicModuleConfig }[] => {
  const modules: { pluginId: string; module: DynamicModuleConfig }[] = [];
  
  // console.log('getAllModules - pluginConfigs:', Object.keys(pluginConfigs));
  
  Object.entries(pluginConfigs).forEach(([pluginId, plugin]) => {
    // console.log(`getAllModules - plugin ${pluginId}:`, plugin);
    // console.log(`getAllModules - plugin ${pluginId} modules:`, plugin.modules);
    
    if (plugin.modules && plugin.modules.length > 0) {
      plugin.modules.forEach(module => {
        // console.log(`getAllModules - adding module ${module.id || module.name} from plugin ${pluginId}`);
        modules.push({ pluginId, module });
      });
    }
  });
  
  // console.log('getAllModules - all modules:', modules);
  return modules;
};

/**
 * Get all modules that match the specified criteria
 * @param criteria Filter criteria for modules
 * @returns Array of matching modules with their plugin IDs
 */
export const getModulesByFilter = (criteria: Partial<DynamicModuleConfig>): { pluginId: string; module: DynamicModuleConfig }[] => {
  const allModules = getAllModules();
  
  return allModules.filter(({ module }) => {
    // Check each criteria property
    for (const [key, value] of Object.entries(criteria)) {
      if (key === 'tags' && Array.isArray(value) && Array.isArray(module.tags)) {
        // For tags, check if any tag in the criteria matches any tag in the module
        if (!value.some(tag => module.tags?.includes(tag))) {
          return false;
        }
      } else if (module[key as keyof DynamicModuleConfig] !== value) {
        return false;
      }
    }
    return true;
  });
};

/**
 * Get all modules in a specific category
 * @param category The category to filter by
 * @returns Array of modules in the specified category with their plugin IDs
 */
export const getModulesByCategory = (category: string): { pluginId: string; module: DynamicModuleConfig }[] => {
  return getModulesByFilter({ category });
};

/**
 * Get all modules with a specific tag
 * @param tag The tag to filter by
 * @returns Array of modules with the specified tag with their plugin IDs
 */
export const getModulesByTag = (tag: string): { pluginId: string; module: DynamicModuleConfig }[] => {
  return getModulesByFilter({ tags: [tag] });
};

/**
 * Get a specific module by ID
 * @param pluginId The plugin ID that contains the module (optional)
 * @param moduleId The ID of the module to find
 * @returns The module config, or undefined if not found
 */
export const getModuleById = (pluginId?: string, moduleId?: string): DynamicModuleConfig | undefined => {
  // console.log('getModuleById - pluginId:', pluginId);
  // console.log('getModuleById - moduleId:', moduleId);
  
  if (!moduleId) return undefined;
  
  // Direct lookup if we have both pluginId and moduleId
  if (pluginId && pluginConfigs[pluginId] && pluginConfigs[pluginId].modules) {
    // console.log(`getModuleById - direct lookup for plugin ${pluginId}`);
    const module = pluginConfigs[pluginId].modules.find(m => 
      m.id === moduleId || m.name === moduleId
    );
    
    if (module) {
      // console.log(`getModuleById - found module directly in plugin ${pluginId}:`, module);
      return module;
    }
  }
  
  // Fallback to searching all modules
  const allModules = getAllModules();
  // console.log('getModuleById - allModules:', allModules);
  
  const match = allModules.find(({ module, pluginId: pid }) => 
    (module.id === moduleId || module.name === moduleId) && (!pluginId || pid === pluginId)
  );
  
  // console.log('getModuleById - match:', match);
  return match?.module;
};

/**
 * Get a specific module by name
 * @param moduleName The name of the module to find
 * @returns The module and its plugin ID, or undefined if not found
 */
export const getModuleByName = (moduleName: string): { pluginId: string; module: DynamicModuleConfig } | undefined => {
  const allModules = getAllModules();
  return allModules.find(({ module }) => module.name === moduleName);
};

/**
 * Gets the plugin ID from an instance ID by looking up the plugin instance in the registry
 */
export const getPluginIdFromInstanceId = (instanceId: string): string | undefined => {
  if (!instanceId) return undefined;
  
  // Fix: Use pluginInstances directly instead of calling a non-existent function
  if (pluginInstances[instanceId]) {
    const pluginConfig = getPluginConfigForInstance(instanceId);
    return pluginConfig?.id;
  }
  
  return undefined;
};

// Export the plugin configs
export const plugins = pluginConfigs;

// For backward compatibility, export the getter function that always returns the latest plugins
// No need to re-export, it's already exported above

// Add a plugin registry change event
const pluginRegistryChangeListeners: (() => void)[] = [];

// Function to notify listeners when the plugin registry changes
const notifyPluginRegistryChange = () => {
  pluginRegistryChangeListeners.forEach(listener => listener());
};

// Register a plugin registry change listener
export const onPluginRegistryChange = (listener: () => void) => {
  pluginRegistryChangeListeners.push(listener);
  return () => {
    const index = pluginRegistryChangeListeners.indexOf(listener);
    if (index !== -1) {
      pluginRegistryChangeListeners.splice(index, 1);
    }
  };
};

// Register a remote plugin
export const registerRemotePlugin = (plugin: LoadedRemotePlugin) => {
  // console.log('[registerRemotePlugin] Registering plugin:', plugin);
  
  // Convert LoadedRemotePlugin to DynamicPluginConfig format
  const pluginConfig: DynamicPluginConfig = {
    id: plugin.id,
    name: plugin.name,
    description: plugin.description,
    version: plugin.version,
    author: plugin.author,
    icon: plugin.icon || 'extension',
    islocal: false,
    // Create modules array from loadedModules if available
    modules: plugin.loadedModules ? plugin.loadedModules.map(module => ({
      id: module.id || module.name,
      name: module.name,
      displayName: module.displayName || module.name,
      description: module.description,
      icon: module.icon,
      category: module.category,
      tags: module.tags,
      configFields: module.configFields || {},
      messages: module.messages,
      priority: module.priority,
      dependencies: module.dependencies,
      layout: module.layout,
      type: module.type,
      props: module.props
    })) : []
  };
  
  // console.log('[registerRemotePlugin] Created plugin config:', pluginConfig);
  
  // Register in the plugin registry
  pluginConfigs[plugin.id] = pluginConfig;
  // console.log('[registerRemotePlugin] Plugin registered. Current pluginConfigs keys:', Object.keys(pluginConfigs));
  
  // Notify listeners of the change
  notifyPluginRegistryChange();
  
  // console.log(`[registerRemotePlugin] Remote plugin registered successfully: ${plugin.id}`);
};

// Register multiple remote plugins
export const registerRemotePlugins = (plugins: LoadedRemotePlugin[]) => {
  plugins.forEach(registerRemotePlugin);
};

// Get all available plugins (both local and remote)
export const getAvailablePlugins = (): DynamicPluginConfig[] => {
  return Object.values(pluginConfigs);
};

export const getAllPluginConfigs = (): Record<string, DynamicPluginConfig> => {
  return { ...pluginConfigs };
};

/**
 * Function to enable local plugins (for future use)
 * This can be called to re-enable local plugins if needed
 */
export const enableLocalPlugins = () => {
  // Add local plugins to the plugin configs
  pluginConfigs = { ...pluginConfigs, ...localPluginConfigs };
  
  // Notify listeners of the change
  notifyPluginRegistryChange();
  
  // console.log('Local plugins enabled');
};
