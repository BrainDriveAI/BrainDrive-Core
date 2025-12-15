import { RemotePluginResponse, LoadedRemotePlugin, LoadedModule } from '../types/remotePlugin';
import { DynamicPluginConfig } from '../types/index';
import ApiService from './ApiService';
import { config } from '../config/index';

declare global {
  interface Window {
    [key: string]: any;
    __webpack_init_sharing__?: (scope: string) => Promise<void>;
    __webpack_share_scopes__?: { default: any };
  }
}

class RemotePluginService {
  private loadedPlugins: Map<string, LoadedRemotePlugin> = new Map();
  private loadingPromises: Map<string, Promise<LoadedRemotePlugin>> = new Map();
  private retryTimeout = 5000; // 5 seconds
  private initialized = false;
  private api = ApiService.getInstance();

  private async initializeSharing(): Promise<void> {
    // Check if sharing is already initialized
    if (window.__webpack_share_scopes__?.default) {
      // console.log('Webpack share scopes already initialized');
      return;
    }
    
    // console.log('Initializing webpack share scopes');
    
    // Initialize the share scope
    // @ts-ignore
    window.__webpack_share_scopes__ = window.__webpack_share_scopes__ || {};
    // @ts-ignore
    window.__webpack_share_scopes__.default = window.__webpack_share_scopes__.default || {};
    
    // Initialize the shared modules
    // Check if __webpack_init_sharing__ is available
    if (typeof window.__webpack_init_sharing__ !== 'function') {
      console.warn('__webpack_init_sharing__ is not a function, creating a polyfill');
      // Create a simple polyfill for __webpack_init_sharing__
      window.__webpack_init_sharing__ = async (scope) => {
        // console.log(`Polyfill: initializing webpack share scope: ${scope}`);
        // This is a simplified version that just ensures the scope exists
        window.__webpack_share_scopes__ = window.__webpack_share_scopes__ || {};
        window.__webpack_share_scopes__[scope] = window.__webpack_share_scopes__[scope] || {};
        return Promise.resolve();
      };
    }
    
    // Now initialize sharing
    await window.__webpack_init_sharing__('default');
    
    //// console.log('Webpack share scopes initialized successfully');
    //// console.log('Available shared modules:', Object.keys(window.__webpack_share_scopes__.default));
  }

  async getRemotePluginManifest(): Promise<DynamicPluginConfig[]> {
    try {
      const response = await this.api.get<Record<string, DynamicPluginConfig>>('/api/v1/plugins/manifest/designer');
      
      //// console.log('Received plugin manifest:', response);
      
      // Convert the object with plugin IDs as keys to an array of plugin configs
      if (response) {
        const plugins = Object.values(response);
        //// console.log(`Found ${plugins.length} plugins in manifest`);
        
        // Log each plugin's bundlelocation
        plugins.forEach(plugin => {
          //// console.log(`Plugin ${plugin.id} bundlelocation: ${plugin.bundlelocation}`);
          
          // Ensure each plugin has an id
          if (!plugin.id) {
            console.warn('Plugin missing id:', plugin);
          }
          
          // Ensure each plugin has a bundlelocation
          if (!plugin.bundlelocation) {
            console.warn(`Plugin ${plugin.id || 'unknown'} missing bundlelocation`);
          }
        });
        
        return plugins;
      }
      
      console.warn('Plugin manifest response is empty');
      return [];
    } catch (error) {
      console.error('Failed to fetch remote plugin manifest:', error);
      return [];
    }
  }

  private getFullUrl(url: string | undefined | null, pluginId?: string, manifest?: DynamicPluginConfig): string {
    // Get the base API URL from config
    const baseApiUrl = config.api.baseURL;
    
    // If url is undefined or null, return the base API URL
    if (!url) {
      console.warn('URL is undefined or null, using base API URL');
      return `${baseApiUrl}/api/v1/`;
    }
    
    // If the URL is already absolute, return it as is
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    
    // Remove leading slash if present to avoid double slashes
    const cleanUrl = url.startsWith('/') ? url.slice(1) : url;
    
    // If pluginId is provided, construct the URL for a plugin bundle
    if (pluginId) {
      // Use database_id from manifest if available for multi-user support
      const effectiveId = manifest?.database_id || pluginId;
      
      // Log the ID resolution for debugging
      if (manifest?.database_id) {
        //// console.log(`Using database_id ${manifest.database_id} instead of plugin ID ${pluginId} for URL construction`);
      }
      
      // Use the public endpoint for plugin bundles to avoid authentication issues
      const pluginUrl = `${baseApiUrl}/api/v1/public/plugins/${effectiveId}/${cleanUrl}`;
      //// console.log(`Constructed plugin URL: ${pluginUrl} for plugin ${pluginId} with path ${cleanUrl}`);
      return pluginUrl;
    }
    
    // Otherwise, use the standard API URL
    return `${baseApiUrl}/api/v1/${cleanUrl}`;
  }

  private async loadRemoteEntry(url: string | undefined | null, pluginId?: string, manifest?: DynamicPluginConfig): Promise<void> {
    if (!url) {
      throw new Error('URL is undefined or null');
    }
    
    const fullUrl = this.getFullUrl(url, pluginId, manifest);
    //// console.log(`Loading remote entry from: ${fullUrl} for plugin ${pluginId || 'unknown'}`);
    
    // Check if script is already loaded
    const existingScript = document.querySelector(`script[src="${fullUrl}"]`);
    if (existingScript) {
      //// console.log(`Remote entry script already loaded: ${fullUrl}`);
      return;
    }
    
    return new Promise((resolve, reject) => {
      try {
        const script = document.createElement('script');
        script.src = fullUrl;
        script.type = 'text/javascript';
        script.async = true;
        
        script.onload = () => {
          //// console.log(`Remote entry script loaded successfully: ${fullUrl}`);
          // Add a small delay to ensure the script is fully initialized
          setTimeout(() => {
           // // console.log('Remote entry initialization complete');
            
            // Check if the plugin's scope is available in the window object
            if (pluginId) {
              const scopeVariations = [
                pluginId,
                pluginId.replace(/([A-Z])/g, '_$1').toLowerCase(),
                pluginId.replace(/-/g, '_'),
                pluginId.replace(/_/g, '-'),
                pluginId.replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase()
              ];
              
              for (const scope of scopeVariations) {
                if ((window as any)[scope]) {
                  //// console.log(`Found global scope for plugin: ${scope}`);
                  break;
                }
              }
            }
            
            resolve();
          }, 100);
        };
        
        script.onerror = (error) => {
          console.error(`Error loading remote entry script: ${fullUrl}`, error);
          reject(new Error(`Failed to load remote entry script: ${fullUrl}`));
        };
        
        //// console.log(`Appending remote entry script to document head: ${fullUrl}`);
        document.head.appendChild(script);
      } catch (error) {
        console.error(`Error creating script element: ${fullUrl}`, error);
        reject(error);
      }
    });
  }

  async loadRemotePlugin(manifest: DynamicPluginConfig): Promise<LoadedRemotePlugin | null> {
    const { bundlelocation, scope, modules } = manifest;
    
    //// console.log(`Loading remote plugin: ${manifest.id} with bundlelocation: ${bundlelocation} and scope: ${scope}`);

    // Check if already loaded
    if (this.loadedPlugins.has(manifest.id)) {
      return this.loadedPlugins.get(manifest.id)!;
    }

    // Check if currently loading
    if (this.loadingPromises.has(manifest.id)) {
      return this.loadingPromises.get(manifest.id)!;
    }

    const loadingPromise = (async () => {
      try {
        // Initialize sharing first
        await this.initializeSharing();

        // Validate required fields
        if (!scope) {
          throw new Error(`Plugin ${manifest.id} is missing required 'scope' field`);
        }
        
        if (!modules || !Array.isArray(modules) || modules.length === 0) {
          throw new Error(`Plugin ${manifest.id} has no modules defined`);
        }

        // Load the remote entry with retry
        let retries = 3;
        while (retries > 0) {
          try {
            // Load the remote entry script if bundlelocation is provided
            if (bundlelocation) {
              await this.loadRemoteEntry(bundlelocation, manifest.id, manifest);
            } else {
              console.warn(`Plugin ${manifest.id} has no bundlelocation, skipping script loading`);
            }
 
            // Try different variations of the scope name
            const scopeVariations = [
              scope,                                // Original scope (e.g., "randomColorPlugin")
              scope.replace(/([A-Z])/g, '_$1').toLowerCase(), // camelCase to snake_case (e.g., "random_color_plugin")
              scope.replace(/-/g, '_'),             // kebab-case to snake_case
              scope.replace(/_/g, '-'),             // snake_case to kebab-case
              scope.replace(/([a-z])([A-Z])/g, '$1-$2').toLowerCase() // camelCase to kebab-case
            ];
            
            let container = null;
            let usedScope = '';
            
            // Try each scope variation
            for (const scopeVar of scopeVariations) {
              //// console.log(`Trying scope variation: ${scopeVar}`);
              container = (window as any)[scopeVar];
              if (container) {
                usedScope = scopeVar;
                //// console.log(`Found container with scope: ${usedScope}`);
                break;
              }
            }
            
            if (!container) {
              const windowMatches = Object.keys(window)
                .filter(key => key.toLowerCase().includes(scope.toLowerCase()) || key.toLowerCase().includes((manifest?.id || '').toLowerCase()))
                .slice(0, 20);
              console.error(
                `[plugins] Scope container missing for ${scope}. Tried variations: ${scopeVariations.join(", ")}` +
                (windowMatches.length ? ` | window matches: ${windowMatches.join(", ")}` : '')
              );
              throw new Error(`Container not found for any scope variation of ${scope}`);
            }

            // Initialize the container with our share scope
            await container.init(window.__webpack_share_scopes__.default);
            
            // Load all modules defined in the plugin configuration
            const loadedModules = await Promise.all(
              modules.map(async (moduleConfig) => {
                try {
                  // Get the factory for this module
                  // Try with and without "./" prefix for the module name
                  const moduleNames = [
                    moduleConfig.name,
                    `./${moduleConfig.name}`,
                    // For webpack, module names are often prefixed with "./"
                    moduleConfig.name.startsWith('./') ? moduleConfig.name.substring(2) : moduleConfig.name
                  ];
                  
                  let factory = null;
                  let usedModuleName = '';
                  
                  // Try each module name variation
                  for (const moduleName of moduleNames) {
                    //// console.log(`Trying to get factory for module: ${moduleName}`);
                    try {
                      // Check if container has the get method
                      if (typeof container.get !== 'function') {
                        console.warn(`Container for ${manifest.id} does not have a get method`);
                        continue;
                      }
                      
                      factory = await container.get(moduleName);
                      if (factory) {
                        usedModuleName = moduleName;
                        //// console.log(`Found factory with module name: ${usedModuleName}`);
                        break;
                      }
                    } catch (e) {
                      //// console.log(`Module ${moduleName} not found, trying next variation`);
                    }
                  }
                  
                  if (!factory) {
                    // Log available modules in container
                    //// console.log('Available modules in container:', Object.keys(container));
                    const availableKeys = Object.keys(container || {}).slice(0, 10);
                    console.error(
                      `[plugins] Module factory missing for ${moduleConfig.name} in ${usedScope || scope}. Tried: ${moduleNames.join(", ")}; available keys: ${availableKeys.join(", ")}`
                    );
                    
                    // Try to use a direct property if available
                    if (typeof container[moduleConfig.name] === 'function') {
                     // // console.log(`Found module ${moduleConfig.name} as a direct property`);
                      factory = container[moduleConfig.name];
                    } else if (moduleConfig.name.startsWith('./') && typeof container[moduleConfig.name.substring(2)] === 'function') {
                      const simpleName = moduleConfig.name.substring(2);
                      //// console.log(`Found module ${simpleName} as a direct property`);
                      factory = container[simpleName];
                    }
                    
                    // If still no factory, check for global exports from the script
                    if (!factory) {
                      const globalName = manifest.id.replace(/-/g, '_');
                      //// console.log(`Checking for global exports with name: ${globalName}`);
                      
                      if ((window as any)[globalName]) {
                        //// console.log(`Found global export for ${manifest.id}`);
                        
                        // Check if the module is directly exposed on the global object
                        if ((window as any)[globalName][moduleConfig.name]) {
                          //// console.log(`Found module ${moduleConfig.name} on global object`);
                          factory = () => (window as any)[globalName][moduleConfig.name];
                        } else if ((window as any)[globalName]['default']) {
                          //// console.log(`Found default export on global object`);
                          factory = () => (window as any)[globalName]['default'];
                        }
                      }
                      
                      // Last resort: try to find the module in the window object directly
                      if (!factory) {
                        // Try common naming patterns for the module
                        const moduleNames = [
                          moduleConfig.name,
                          moduleConfig.name.charAt(0).toLowerCase() + moduleConfig.name.slice(1),
                          moduleConfig.name.charAt(0).toUpperCase() + moduleConfig.name.slice(1)
                        ];
                        
                        for (const name of moduleNames) {
                          if ((window as any)[name]) {
                            //// console.log(`Found module ${name} directly in window object`);
                            factory = () => (window as any)[name];
                            break;
                          }
                        }
                      }
                      
                      // If still no factory, create a fallback component
                      if (!factory) {
                        console.warn(`Creating fallback component for ${moduleConfig.name}`);
                        factory = () => {
                          // Create a React component that shows an error message
                          return {
                            default: (props: any) => {
                              return {
                                $$typeof: Symbol.for('react.element'),
                                type: 'div',
                                props: {
                                  children: [
                                    {
                                      $$typeof: Symbol.for('react.element'),
                                      type: 'h3',
                                      props: { children: `Plugin Module Not Found: ${moduleConfig.name}` },
                                      key: null,
                                      ref: null
                                    },
                                    {
                                      $$typeof: Symbol.for('react.element'),
                                      type: 'p',
                                      props: { children: `The module "${moduleConfig.name}" could not be loaded from plugin "${manifest.id}".` },
                                      key: null,
                                      ref: null
                                    }
                                  ],
                                  style: {
                                    padding: '16px',
                                    border: '1px solid #f44336',
                                    borderRadius: '4px',
                                    backgroundColor: '#ffebee'
                                  }
                                },
                                key: null,
                                ref: null
                              };
                            }
                          };
                        };
                      }
                    }
                  }
                  
                  //// console.log(`Factory received for ${usedModuleName}:`, factory);
                  
                  // Create the module instance
                  const moduleInstance = factory();
                  //// console.log(`Module instance created for ${usedModuleName}:`, moduleInstance);
                  
                  // Debug the module configuration
                  //// console.log(`[ModuleDebug] Module config for ${moduleConfig.name}:`, {
                  //  moduleConfig,
                  //  hasRequiredServices: !!moduleConfig.requiredServices,
                  //  requiredServices: moduleConfig.requiredServices
                  //});
                  
                  // Return the loaded module
                  return {
                    name: moduleConfig.name,
                    id: moduleConfig.id || moduleConfig.name,
                    displayName: moduleConfig.displayName || moduleConfig.name,
                    description: moduleConfig.description,
                    icon: moduleConfig.icon,
                    category: moduleConfig.category,
                    tags: moduleConfig.tags,
                    props: moduleConfig.props,
                    configFields: moduleConfig.configFields,
                    messages: moduleConfig.messages,
                    priority: moduleConfig.priority,
                    dependencies: moduleConfig.dependencies,
                    layout: moduleConfig.layout,
                    type: moduleConfig.type,
                    component: moduleInstance.default || moduleInstance,
                    requiredServices: moduleConfig.requiredServices
                  } as LoadedModule;
                } catch (error) {
                  console.error(`Failed to load module ${moduleConfig.name}:`, error);
                  throw error;
                }
              })
            );

            // Filter out any failed module loads
            const successfullyLoadedModules = loadedModules.filter(Boolean);
            
            if (successfullyLoadedModules.length === 0) {
              throw new Error(`No modules could be loaded for plugin ${manifest.id}`);
            }
            
            // Create the loaded plugin object
            const loadedPlugin: LoadedRemotePlugin = {
              ...manifest,
              islocal: false, // Explicitly mark as remote plugin
              loadedModules: successfullyLoadedModules,
              // For backward compatibility, use the first module as the main component
              component: successfullyLoadedModules[0]!.component,
              config: manifest
            };
            
            // Store the loaded plugin
            this.loadedPlugins.set(manifest.id, loadedPlugin);
            //// console.log(`Plugin ${manifest.id} loaded successfully with ${successfullyLoadedModules.length} modules`);
            
            return loadedPlugin;
          } catch (error) {
            retries--;
            if (retries === 0) throw error;
            console.warn(`Retrying plugin load for ${manifest.id}, attempts remaining: ${retries}`);
            await new Promise(resolve => setTimeout(resolve, this.retryTimeout));
          }
        }
        throw new Error('Failed to load plugin after retries');
      } catch (error) {
        console.error(`Failed to load remote plugin ${manifest.id}:`, error);
        return null;
      } finally {
        this.loadingPromises.delete(manifest.id);
      }
    })();

    this.loadingPromises.set(manifest.id, loadingPromise);
    return loadingPromise;
  }

  getLoadedPlugin(pluginId: string): LoadedRemotePlugin | undefined {
    return this.loadedPlugins.get(pluginId);
  }

  /**
   * List IDs of all loaded plugins
   */
  listLoadedPluginIds(): string[] {
    return Array.from(this.loadedPlugins.keys());
  }

  /**
   * Get a specific module from a loaded plugin
   * @param pluginId The ID of the plugin
   * @param moduleId The ID or name of the module to retrieve
   * @returns The loaded module or undefined if not found
   */
  getLoadedModule(pluginId: string, moduleId: string): LoadedModule | undefined {
    const plugin = this.getLoadedPlugin(pluginId);
    if (!plugin) return undefined;
    
    return plugin.loadedModules.find(
      module => module.id === moduleId || module.name === moduleId
    );
  }

  /**
   * Find a loaded plugin that contains a module with the given id or name
   */
  findLoadedPluginByModuleId(moduleId: string): { plugin: LoadedRemotePlugin; module: LoadedModule } | undefined {
    const norm = (s: string) => (s || '').toLowerCase();
    const target = norm(moduleId);
    for (const plugin of this.loadedPlugins.values()) {
      const mod = plugin.loadedModules.find(m => norm(m.id) === target || norm(m.name) === target);
      if (mod) return { plugin, module: mod };
    }
    return undefined;
  }

  /**
   * Get all loaded modules across all plugins
   * @returns Array of all loaded modules
   */
  getAllLoadedModules(): LoadedModule[] {
    const modules: LoadedModule[] = [];
    this.loadedPlugins.forEach(plugin => {
      modules.push(...plugin.loadedModules);
    });
    return modules;
  }

  /**
   * Get all loaded modules that match the specified criteria
   * @param criteria Filter criteria for modules
   * @returns Array of matching modules
   */
  getModulesByFilter(criteria: Partial<LoadedModule>): LoadedModule[] {
    const modules = this.getAllLoadedModules();
    
    return modules.filter(module => {
      // Check each criteria property
      for (const [key, value] of Object.entries(criteria)) {
        if (key === 'tags' && Array.isArray(value) && Array.isArray(module.tags)) {
          // For tags, check if any tag in the criteria matches any tag in the module
          if (!value.some(tag => module.tags?.includes(tag))) {
            return false;
          }
        } else if (module[key as keyof LoadedModule] !== value) {
          return false;
        }
      }
      return true;
    });
  }

  /**
   * Get all modules in a specific category
   * @param category The category to filter by
   * @returns Array of modules in the specified category
   */
  getModulesByCategory(category: string): LoadedModule[] {
    return this.getModulesByFilter({ category });
  }

  /**
   * Get all modules with a specific tag
   * @param tag The tag to filter by
   * @returns Array of modules with the specified tag
   */
  getModulesByTag(tag: string): LoadedModule[] {
    return this.getModulesByFilter({ tags: [tag] });
  }

  getAllLoadedPlugins(): LoadedRemotePlugin[] {
    return Array.from(this.loadedPlugins.values());
  }
}

export const remotePluginService = new RemotePluginService();
