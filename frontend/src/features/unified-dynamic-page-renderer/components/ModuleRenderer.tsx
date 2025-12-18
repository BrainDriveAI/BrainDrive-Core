import React, { useState, useEffect, useContext, useCallback, useRef, ErrorInfo } from 'react';
import { Box, Typography, CircularProgress } from '@mui/material';
import { LoadedModule } from '../../../types/remotePlugin';
import { remotePluginService } from '../../../services/remotePluginService';
import { getPluginConfigForInstance } from '../../../plugins';
import { ServiceContext } from '../../../contexts/ServiceContext';
import ComponentErrorBoundary from '../../../components/ComponentErrorBoundary';
import { eventBus } from '../../../plugin/eventBus';
import { createServiceBridges, ServiceError } from '../../../utils/serviceBridge';

const REACT_FORWARD_REF_TYPE = Symbol.for('react.forward_ref');
const REACT_MEMO_TYPE = Symbol.for('react.memo');
const REACT_LAZY_TYPE = Symbol.for('react.lazy');
const REACT_CONTEXT_TYPE = Symbol.for('react.context');
const REACT_PROVIDER_TYPE = Symbol.for('react.provider');

function isValidReactElementType(value: any): boolean {
  if (!value) return false;
  const type = typeof value;

  if (type === 'string' || type === 'function' || type === 'symbol') return true;
  if (type !== 'object') return false;

  const $$typeof = (value as any).$$typeof;
  return (
    $$typeof === REACT_FORWARD_REF_TYPE ||
    $$typeof === REACT_MEMO_TYPE ||
    $$typeof === REACT_LAZY_TYPE ||
    $$typeof === REACT_CONTEXT_TYPE ||
    $$typeof === REACT_PROVIDER_TYPE
  );
}

function resolveFederatedComponent(maybeModule: any): React.ComponentType<any> | null {
  let current = maybeModule;
  const visited = new Set<any>();

  for (let depth = 0; depth < 6; depth++) {
    if (isValidReactElementType(current)) return current as any;

    if (!current) break;
    const type = typeof current;
    if (type !== 'object' && type !== 'function') break;

    if (visited.has(current)) break;
    visited.add(current);

    if ('default' in (current as any)) {
      current = (current as any).default;
      continue;
    }

    break;
  }

  return null;
}

export interface ModuleRendererProps {
  pluginId: string;
  moduleId: string;
  moduleName?: string;
  isLocal?: boolean;
  additionalProps?: Record<string, any>;
  fallback?: React.ReactNode;
  onError?: (error: Error, errorInfo?: ErrorInfo) => void;
}

interface ModuleRendererState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Unified ModuleRenderer that combines PluginModuleRenderer and DynamicPluginRenderer functionality
 * This creates a complete unified system for rendering plugin modules with service integration
 */
export class ModuleRenderer extends React.Component<ModuleRendererProps, ModuleRendererState> {
  private mountedRef = React.createRef<boolean>();
  private prevModuleRef = React.createRef<LoadedModule>();
  private stableModulePropsRef = React.createRef<Record<string, any>>();

  constructor(props: ModuleRendererProps) {
    super(props);
    this.state = { hasError: false, error: null };
    // @ts-ignore - Initialize ref value
    this.mountedRef.current = true;
  }

  static getDerivedStateFromError(error: Error): ModuleRendererState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error(`[ModuleRenderer] Error rendering plugin module ${this.props.pluginId}:${this.props.moduleId}:`, error, errorInfo);
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  componentWillUnmount() {
    // @ts-ignore
    this.mountedRef.current = false;
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <Box sx={{ p: 2, border: '1px solid #f44336', borderRadius: 1, bgcolor: '#ffebee' }}>
          <Typography variant="h6" color="error">Plugin Module Error</Typography>
          <Typography variant="body2" color="error">
            Failed to render module: {this.props.pluginId}:{this.props.moduleId}
          </Typography>
          <Typography variant="body2" color="error">
            {this.state.error?.message || 'Unknown error'}
          </Typography>
        </Box>
      );
    }

    return <UnifiedModuleRenderer {...this.props} mountedRef={this.mountedRef} />;
  }
}

/**
 * Internal functional component that handles the actual module loading and rendering
 */
interface UnifiedModuleRendererProps extends ModuleRendererProps {
  mountedRef: React.RefObject<boolean>;
}

const UnifiedModuleRenderer: React.FC<UnifiedModuleRendererProps> = ({
  pluginId,
  moduleId,
  moduleName,
  isLocal = false,
  additionalProps = {},
  fallback,
  onError,
  mountedRef
}) => {
  const [module, setModule] = useState<LoadedModule | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [serviceErrors, setServiceErrors] = useState<ServiceError[]>([]);
  const serviceContext = useContext(ServiceContext);
  const prevModuleRef = useRef<LoadedModule | null>(null);
  const stableModulePropsRef = useRef<Record<string, any>>({});

  // Create a getService function with special handling - same as PluginModuleRenderer
  const getService = useCallback((name: string) => {
    if (!serviceContext) {
      throw new Error('Service context not available');
    }
    
    // Special handling for pluginState service - create plugin-specific instance
    if (name === 'pluginState' && pluginId) {
      try {
        const pluginStateFactory = serviceContext.getService('pluginStateFactory') as any;
        
        if (!pluginStateFactory) {
          console.error(`[ModuleRenderer] pluginStateFactory service is null/undefined`);
          return null;
        }
        
        // Try to get existing service first, create if it doesn't exist
        let pluginStateService = pluginStateFactory.getPluginStateService(pluginId);
        if (!pluginStateService) {
          pluginStateService = pluginStateFactory.createPluginStateService(pluginId);
        }
        
        return pluginStateService;
      } catch (error) {
        console.error(`[ModuleRenderer] Failed to get plugin state service for ${pluginId}:`, error);
        return null;
      }
    }
    
    return serviceContext.getService(name);
  }, [serviceContext, pluginId]);

  // Memoized service bridge creation - same as PluginModuleRenderer
  const createServiceBridgesWithMemo = useCallback(
    (requiredServices: any) => {
      return createServiceBridges(requiredServices, getService);
    },
    [getService]
  );

  // Main module loading effect - integrated from PluginModuleRenderer
  useEffect(() => {
    let isMounted = true;
    
    const loadModule = async () => {
      if (!isMounted || !mountedRef.current) return;
      
      try {
        setLoading(true);
        setError(null);
        if (process.env.NODE_ENV === 'development') {
          console.debug(`[ModuleRenderer] Starting module load for ${pluginId}:${moduleId}`);
        }
        
        // Load the plugin module using the same logic as PluginModuleRenderer
        // Attempt direct lookup; if missing, try to resolve a compatible loaded plugin id
        let normalizedPluginId = pluginId;
        let remotePlugin = remotePluginService.getLoadedPlugin(normalizedPluginId);
        if (!remotePlugin) {
          const candidates = remotePluginService.listLoadedPluginIds();
          const variations = [
            normalizedPluginId,
            normalizedPluginId.replace(/-/g, '_'),
            normalizedPluginId.replace(/_/g, '-'),
          ];
          const foundId =
            candidates.find(id => variations.includes(id)) ||
            candidates.find(id => id.includes(normalizedPluginId)) ||
            candidates.find(id => normalizedPluginId.includes(id));
          if (foundId) {
            normalizedPluginId = foundId;
            if (process.env.NODE_ENV === 'development') {
              console.debug(`[ModuleRenderer] Resolved pluginId '${pluginId}' -> '${normalizedPluginId}' (loaded)`);
            }
            remotePlugin = remotePluginService.getLoadedPlugin(normalizedPluginId)!;
          }
        }
        // Lazy-load plugin by manifest if still missing
        if (!remotePlugin) {
          const manifest = await remotePluginService.getRemotePluginManifest();
          // Prefer manifest entries where id includes pluginId and module list includes moduleId
          const byModules = manifest.filter(m =>
            (m.id && (m.id.includes(normalizedPluginId) || normalizedPluginId.includes(m.id))) &&
            Array.isArray(m.modules) && m.modules.some(mod => mod.id === moduleId || mod.name === moduleId)
          );
          const byId = manifest.filter(m => m.id && (m.id.includes(normalizedPluginId) || normalizedPluginId.includes(m.id)));
          const candidateManifest = byModules[0] || byId[0] || manifest.find(m => m.id === normalizedPluginId);
          if (candidateManifest) {
            const loaded = await remotePluginService.loadRemotePlugin(candidateManifest);
            if (loaded) {
              normalizedPluginId = loaded.id;
              remotePlugin = loaded;
              if (process.env.NODE_ENV === 'development') {
                console.debug(`[ModuleRenderer] Lazy-loaded plugin '${normalizedPluginId}' for ${moduleId}`);
              }
            }
          }
        }
        if (!remotePlugin) {
          throw new Error(`Plugin ${normalizedPluginId} not found or not loaded`);
        }

        // Use loadedModules instead of modules - same as PluginModuleRenderer
        if (!remotePlugin.loadedModules || remotePlugin.loadedModules.length === 0) {
          throw new Error(`Plugin ${pluginId} has no loaded modules`);
        }

        // Extract additional normalized forms of moduleId for robust matching
        const normalize = (s?: string) => (s || '').toLowerCase().replace(/[_-]/g, '');
        const lastToken = (s?: string) => {
          const t = (s || '').split('_');
          return t[t.length - 1] || s || '';
        };
        const baseModuleId = moduleId ? moduleId.replace(/-\d+$/, '') : null;
        
        // Find the module by ID first, then by base ID, then by name - same logic as PluginModuleRenderer
        let foundModule: LoadedModule | undefined;
        
        if (moduleId) {
          // Exact id match
          foundModule = remotePlugin.loadedModules.find(m => m.id === moduleId);
          // Base-id match (strip numeric suffix)
          if (!foundModule && baseModuleId) {
            foundModule = remotePlugin.loadedModules.find(m => m.id === baseModuleId);
          }
          // Last token of composite id (e.g., ServiceExample_Theme_ThemeDisplay -> ThemeDisplay)
          if (!foundModule) {
            const lt = lastToken(moduleId);
            foundModule = remotePlugin.loadedModules.find(m => m.id === lt || m.name === lt);
          }
          // Loose normalized comparison (ignore case and _-/)
          if (!foundModule) {
            const target = normalize(moduleId);
            foundModule = remotePlugin.loadedModules.find(m => normalize(m.id) === target || normalize(m.name) === target);
            // If still not found, allow suffix/substring match so "WhyDetector" can match "BrainDriveWhyDetector"
            if (!foundModule && target) {
              foundModule = remotePlugin.loadedModules.find(m =>
                normalize(m.id).includes(target) || normalize(m.name).includes(target)
              );
            }
          }
        }
        
        // Special handling for BrainDriveChat plugin
        if (!foundModule && pluginId === 'BrainDriveChat') {
          // BrainDriveChat has a single module that should be used regardless of moduleId
          if (remotePlugin.loadedModules.length > 0) {
            foundModule = remotePlugin.loadedModules[0];
            if (process.env.NODE_ENV === 'development') {
              console.debug(`[ModuleRenderer] Using first module for BrainDriveChat plugin`);
            }
          }
        }
        
        if (!foundModule && moduleName) {
          // Try by name, including loose normalized comparison
          foundModule = remotePlugin.loadedModules.find(m => m.name === moduleName)
            || remotePlugin.loadedModules.find(m => normalize(m.name) === normalize(moduleName));
        }
        // Cross-plugin fallback: search all loaded plugins for this module id/name if still not found
        if (!foundModule && moduleId) {
          // Cross-plugin fallback: allow loose matching by id/name and last token
          const cross = remotePluginService.findLoadedPluginByModuleId(moduleId)
            || remotePluginService.findLoadedPluginByModuleId(baseModuleId || '')
            || remotePluginService.findLoadedPluginByModuleId(lastToken(moduleId));
          if (cross) {
            if (process.env.NODE_ENV === 'development') {
              console.debug(`[ModuleRenderer] Resolved module '${moduleId}' in plugin '${cross.plugin.id}'`);
            }
            remotePlugin = cross.plugin;
            foundModule = cross.module;
          }
        }
        if (!foundModule && !moduleId && !moduleName) {
          // Default to first module if still nothing specified
          foundModule = remotePlugin.loadedModules[0];
        }

        if (!foundModule) {
          throw new Error(`Module ${moduleId} not found in plugin ${pluginId}`);
        }

        if (!foundModule.component) {
          throw new Error(`Module ${moduleId} has no component`);
        }

        // Create service bridges using the original createServiceBridges function
        let serviceBridges = {};
        let errors: ServiceError[] = [];
        
        if (foundModule.requiredServices) {
          const result = createServiceBridgesWithMemo(foundModule.requiredServices);
          serviceBridges = result.serviceBridges;
          errors = result.errors;
        }
        
        if (errors.length > 0) {
          if (process.env.NODE_ENV === 'development') {
            console.debug(`[ModuleRenderer] Service bridge creation warnings for ${pluginId}:${moduleId}:`, errors);
          }
          setServiceErrors(errors);
        }

        // Get plugin configuration
        const pluginConfig = getPluginConfigForInstance(pluginId);
        
        // Create messaging functions - same as PluginModuleRenderer
        const sendMessage = (targetPluginId: string, message: any) => {
          eventBus.emit('plugin-message', {
            from: pluginId,
            to: targetPluginId,
            message,
            timestamp: Date.now()
          });
        };

        const addConnection = (targetPluginId: string) => {
          console.log(`[ModuleRenderer] Adding connection from ${pluginId} to ${targetPluginId}`);
        };

        const removeConnection = (targetPluginId: string) => {
          console.log(`[ModuleRenderer] Removing connection from ${pluginId} to ${targetPluginId}`);
        };

        const subscribe = (eventType: string, handler: (...args: any[]) => void) => {
          eventBus.on(eventType, handler);
          return () => eventBus.off(eventType, handler);
        };

        // Merge all props - same logic as PluginModuleRenderer
        const mergedProps = {
          ...foundModule.props,
          ...additionalProps,
          pluginId,
          moduleId,
          isLocal,
          config: pluginConfig,
          services: serviceBridges,
          sendMessage,
          addConnection,
          removeConnection,
          subscribe,
          moduleMessaging: {
            sendMessage,
            addConnection,
            removeConnection,
            subscribe
          }
        };

        // Store stable props reference
        stableModulePropsRef.current = mergedProps;

        if (!isMounted || !mountedRef.current) return;

        // Create the complete module object - same as PluginModuleRenderer
        const newModule: LoadedModule = {
          ...foundModule,
          component: foundModule.component!, // We already checked it exists
          props: mergedProps
        };

        // Only update state if the module has changed to prevent re-renders
        const shouldUpdate = !prevModuleRef.current ||
            prevModuleRef.current.id !== newModule.id ||
            JSON.stringify(getEssentialProps(prevModuleRef.current.props)) !==
            JSON.stringify(getEssentialProps(newModule.props));
            
        if (shouldUpdate) {
          prevModuleRef.current = newModule;
          setModule(newModule);
          if (process.env.NODE_ENV === 'development') {
            console.debug(`[ModuleRenderer] Module loaded successfully: ${pluginId}:${moduleId}`);
          }
        }
      } catch (err) {
        console.error(`[ModuleRenderer] Error loading module ${pluginId}:${moduleId}:`, err);
        // Sticky: If we had a previously loaded module, keep rendering it and suppress the error UI
        if (prevModuleRef.current) {
          setError(null);
          setModule(prevModuleRef.current);
        } else {
          setError(err instanceof Error ? err.message : 'Unknown error loading module');
        }
        if (onError && err instanceof Error) {
          onError(err);
        }
      } finally {
        setLoading(false);
      }
    };

    // Helper function to extract only essential props for comparison
    const getEssentialProps = (props: any) => {
      if (!props) return {};
      
      const {
        sendMessage, addConnection, removeConnection, subscribe,
        services, moduleMessaging, ...essentialProps
      } = props;
      
      return essentialProps;
    };
    
    loadModule();
    
    // Cleanup function to prevent updates after unmount
    return () => {
      isMounted = false;
    };
  }, [pluginId, moduleId, moduleName, isLocal, createServiceBridgesWithMemo, additionalProps, serviceContext, onError, mountedRef]);

  // Loading state
  if (loading && !prevModuleRef.current && !module) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', p: 2 }}>
        <CircularProgress size={24} />
        <Typography variant="body2" sx={{ ml: 1 }}>
          Loading module {pluginId}:{moduleId}...
        </Typography>
      </Box>
    );
  }

  // Error state (only when no previous successful module rendered)
  if (error && !prevModuleRef.current && !module) {
    return (
      <Box sx={{ p: 2, border: '1px solid #f44336', borderRadius: 1, bgcolor: '#ffebee' }}>
        <Typography variant="h6" color="error">Module Load Error</Typography>
        <Typography variant="body2" color="error">
          Failed to load module: {pluginId}:{moduleId}
        </Typography>
        <Typography variant="body2" color="error">{error}</Typography>
        {serviceErrors.length > 0 && (
          <Box sx={{ mt: 1 }}>
            <Typography variant="body2" color="warning.main">Service Errors:</Typography>
            {serviceErrors.map((err, idx) => (
              <Typography key={idx} variant="caption" color="warning.main" display="block">
                • {err.serviceName}: {err.error}
              </Typography>
            ))}
          </Box>
        )}
      </Box>
    );
  }

  // No module loaded
  if (!module) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          No module loaded: {pluginId}:{moduleId}
        </Typography>
      </Box>
    );
  }

  // Render the module component - integrated DynamicPluginRenderer functionality
  try {
    const Component = resolveFederatedComponent(module.component);
    
    if (!Component) {
      const exportKeys =
        module.component && typeof module.component === 'object' ? Object.keys(module.component) : [];
      throw new Error(
        `Invalid module component export for ${pluginId}:${moduleId}. ` +
          `Expected a React component (default export). ` +
          `Got ${typeof module.component}${exportKeys.length ? ` (keys: ${exportKeys.join(', ')})` : ''}`
      );
    }

    if (process.env.NODE_ENV === 'development') {
      console.debug(`[ModuleRenderer] Rendering component for ${pluginId}:${moduleId}`, {
        componentType: typeof Component,
        componentName: Component.name || Component.displayName || 'Anonymous',
        propsKeys: Object.keys(module.props || {})
      });
    }

    // Determine layout options (safe defaults)
    const centerContent: boolean = (additionalProps as any)?.centerContent !== false;
    const viewportFill: boolean = (additionalProps as any)?.viewportFill === true;

    // Render the component with error boundary and a centering wrapper
    return (
      <ComponentErrorBoundary
        fallback={fallback}
        
      >
        <div
          className={[
            'module-content',
            !centerContent && 'module-content--no-center',
            viewportFill && 'module-content--fill',
          ].filter(Boolean).join(' ')}
        >
          <Component {...module.props} />
        </div>
      </ComponentErrorBoundary>
    );
  } catch (renderError) {
    console.error(`[ModuleRenderer] Error rendering component for ${pluginId}:${moduleId}:`, renderError);
    
    return (
      <Box sx={{ p: 2, border: '1px solid #f44336', borderRadius: 1, bgcolor: '#ffebee' }}>
        <Typography variant="h6" color="error">Component Render Error</Typography>
        <Typography variant="body2" color="error">
          Failed to render component: {pluginId}:{moduleId}
        </Typography>
        <Typography variant="body2" color="error">
          {renderError instanceof Error ? renderError.message : 'Unknown render error'}
        </Typography>
      </Box>
    );
  }
};

export default ModuleRenderer;
