import React, { createContext, useContext, useEffect, useState, useCallback, ReactNode, useRef } from 'react';
import { ServiceRegistry } from '../services/ServiceRegistry';
import { BaseService } from '../services/base/BaseService';
import ApiService from '../services/ApiService';
import { themeService, Theme } from '../services/themeService';
import { SettingsService } from '../services/SettingsService';
import { eventService } from '../services/EventService';

interface ServiceContextValue {
  registry: ServiceRegistry;
  isInitialized: boolean;
  getService: <T extends BaseService>(name: string) => T;
  findServicesByCapability: (capability: string) => BaseService[];
  error: Error | null;
}

interface ServiceProviderProps {
  children: ReactNode;
  registry: ServiceRegistry;
}

const ServiceContext = createContext<ServiceContextValue | null>(null);

// Export the ServiceContext
export { ServiceContext };

/**
 * Custom hook for accessing services
 */
export function useService<T extends BaseService>(name: string): T {
  const context = useContext(ServiceContext);
  if (!context) {
    throw new Error('useService must be used within a ServiceProvider');
  }
  if (!context.isInitialized) {
    throw new Error('Services are not yet initialized');
  }
  return context.getService<T>(name);
}

/**
 * Custom hook for accessing services by capability
 */
export function useServicesByCapability(capability: string): BaseService[] {
  const context = useContext(ServiceContext);
  if (!context) {
    throw new Error('useServicesByCapability must be used within a ServiceProvider');
  }
  if (!context.isInitialized) {
    throw new Error('Services are not yet initialized');
  }
  return context.findServicesByCapability(capability);
}

/**
 * Custom hook for accessing the API service
 */
export function useApi(): ApiService {
  return useService<ApiService>('api');
}

/**
 * Custom hook for accessing the theme service
 */
export function useTheme() {
  return useService<typeof themeService>('theme');
}

/**
 * Custom hook for accessing the settings service
 */
export function useSettings(): SettingsService {
  return useService<SettingsService>('settings');
}

/**
 * Custom hook for accessing the event service
 */
export function useEvent() {
  return useService<typeof eventService>('event');
}

/**
 * Service initialization queue for managing service startup
 */
class ServiceInitQueue {
  private initOrder: string[] = [];
  private initialized = new Set<string>();
  private registry: ServiceRegistry;

  constructor(registry: ServiceRegistry) {
    this.registry = registry;
    this.buildInitOrder();
  }

  private buildInitOrder() {
    const visited = new Set<string>();
    const tempMark = new Set<string>();
    
    const visit = (serviceName: string) => {
      if (tempMark.has(serviceName)) {
        throw new Error(`Circular dependency detected involving ${serviceName}`);
      }
      if (!visited.has(serviceName)) {
        tempMark.add(serviceName);
        
        const dependencies = this.registry.getServiceDependencies(serviceName);
        for (const dep of dependencies) {
          if (dep.required) {
            visit(dep.serviceName);
          }
        }
        
        tempMark.delete(serviceName);
        visited.add(serviceName);
        this.initOrder.push(serviceName);
      }
    };

    for (const service of this.registry.listServices()) {
      if (!visited.has(service)) {
        visit(service);
      }
    }
  }

  async initializeNext(): Promise<boolean> {
    const nextService = this.initOrder.find(service => !this.initialized.has(service));
    if (!nextService) return false;

    const service = this.registry.getService(nextService);
    await service.initialize();
    this.initialized.add(nextService);
    return true;
  }

  async initializeAll(): Promise<void> {
    while (await this.initializeNext()) {
      // Continue initializing until no more services remain
    }
  }
}

export function ServiceProvider({ children, registry }: ServiceProviderProps) {
  const [isInitialized, setIsInitialized] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const hasInitializedRef = useRef(false);
  const initializingPromiseRef = useRef<Promise<void> | null>(null);
  const pendingDestroyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const initializeServices = useCallback(async () => {
    if (hasInitializedRef.current) {
      return;
    }
    if (initializingPromiseRef.current) {
      return initializingPromiseRef.current;
    }

    initializingPromiseRef.current = (async () => {
      try {
        const initQueue = new ServiceInitQueue(registry);
        await initQueue.initializeAll();
        hasInitializedRef.current = true;
        setIsInitialized(true);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e : new Error('Failed to initialize services'));
      } finally {
        initializingPromiseRef.current = null;
      }
    })();

    return initializingPromiseRef.current;
  }, [registry]);

  const destroyServices = useCallback(async () => {
    if (!hasInitializedRef.current) {
      return;
    }
    try {
      await registry.destroyAll();
    } catch (e) {
      console.error('Error during service cleanup:', e);
    } finally {
      hasInitializedRef.current = false;
    }
  }, [registry]);

  useEffect(() => {
    if (pendingDestroyTimeoutRef.current) {
      clearTimeout(pendingDestroyTimeoutRef.current);
      pendingDestroyTimeoutRef.current = null;
    }

    initializeServices();

    return () => {
      if (pendingDestroyTimeoutRef.current) {
        clearTimeout(pendingDestroyTimeoutRef.current);
      }
      pendingDestroyTimeoutRef.current = setTimeout(() => {
        destroyServices();
      }, 0);
    };
  }, [initializeServices, destroyServices]);

  const contextValue: ServiceContextValue = {
    registry,
    isInitialized,
    getService: <T extends BaseService>(name: string) => registry.getService<T>(name),
    findServicesByCapability: (capability: string) => registry.findServicesByCapability(capability),
    error
  };

  if (error) {
    return (
      <div style={{ color: 'red', padding: '20px' }}>
        Error initializing services: {error.message}
      </div>
    );
  }

  if (!isInitialized) {
    return (
      <div style={{ padding: '20px' }}>
        Initializing services...
      </div>
    );
  }

  return (
    <ServiceContext.Provider value={contextValue}>
      {children}
    </ServiceContext.Provider>
  );
}
