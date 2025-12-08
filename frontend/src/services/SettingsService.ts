import { AbstractBaseService, ServiceCapability, ServiceVersion } from './base/BaseService';
import ApiService from './ApiService';

export type SettingScope = 'system' | 'user' | 'page' | 'user_page';

export interface SettingDefinition {
  id: string;
  name: string;
  description?: string;
  category: string;
  type: 'string' | 'number' | 'boolean' | 'object' | 'array';
  default?: any;
  allowedScopes: SettingScope[];  // Array of allowed scopes
  validation?: {
    required?: boolean;
    pattern?: string;
    min?: number;
    max?: number;
    enum?: any[];
  };
  isMultiple?: boolean;
  tags?: string[];
}

export interface SettingInstance {
  id: string;
  definitionId: string;
  name: string;
  value: any;
  scope: SettingScope;
  userId?: string;
  pageId?: string;
  createdAt: string;
  updatedAt: string;
}

type SettingsSubscriber = (value: any) => void;

export class SettingsService extends AbstractBaseService {
  private definitions: Map<string, SettingDefinition> = new Map();
  private instances: Map<string, SettingInstance> = new Map();
  private subscribers: Map<string, Set<SettingsSubscriber>> = new Map();
  private categorySubscribers: Map<string, Set<SettingsSubscriber>> = new Map();
  private serviceRegistry: any;
  private definitionsLoadedFromBackend = false;

  constructor(serviceRegistry?: any) {
    const capabilities: ServiceCapability[] = [
      {
        name: 'settings.read',
        description: 'Ability to read settings',
        version: '1.0.0'
      },
      {
        name: 'settings.write',
        description: 'Ability to write settings',
        version: '1.0.0'
      },
      {
        name: 'settings.delete',
        description: 'Ability to delete settings',
        version: '1.0.0'
      },
      {
        name: 'settings.subscribe',
        description: 'Ability to subscribe to setting changes',
        version: '1.0.0'
      }
    ];

    super('settings', { major: 1, minor: 0, patch: 0 }, capabilities);
    this.serviceRegistry = serviceRegistry;
    this.loadFromStorage();
  }
  
  /**
   * Set the service registry
   * This is called by the ServiceRegistry when the service is registered
   */
  setServiceRegistry(serviceRegistry: any): void {
    this.serviceRegistry = serviceRegistry;
  }

  async initialize(): Promise<void> {
    // Load any persisted settings from backend when available
    // For now, we're using localStorage
    this.loadFromStorage();
  }

  async destroy(): Promise<void> {
    // Clean up subscribers
    this.subscribers.clear();
    this.categorySubscribers.clear();
  }

  // Core CRUD operations
  async getSetting<T>(definitionId: string, context: { userId?: string; pageId?: string } = {}): Promise<T | undefined> {
    // console.log(`SettingsService: Getting setting ${definitionId} with context:`, context);
    try {
      // Ensure we have the definition loaded before attempting anything
      await this.ensureDefinitionLoaded(definitionId);

      // First try to get from backend
      // console.log(`SettingsService: Attempting to load ${definitionId} from backend`);
      const backendValue = await this.loadFromBackend(definitionId, context);
      if (backendValue !== null) {
        // console.log(`SettingsService: Successfully loaded ${definitionId} from backend:`, backendValue);
        return backendValue as T;
      } else {
        // console.log(`SettingsService: No value found for ${definitionId} in backend`);
      }
      
      // Fall back to local instance if backend fails
      // console.log(`SettingsService: Falling back to local instance for ${definitionId}`);
      const instance = await this.getSettingInstance(definitionId, context);
      if (instance) {
        // console.log(`SettingsService: Found local instance for ${definitionId}:`, instance.value);
      } else {
        // console.log(`SettingsService: No local instance found for ${definitionId}`);
      }
      return instance?.value as T;
    } catch (error) {
      console.error(`SettingsService: Error in getSetting for ${definitionId}:`, error);
      // Fall back to local instance if there's an error
      const instance = await this.getSettingInstance(definitionId, context);
      if (instance) {
        // console.log(`SettingsService: Found local instance after error for ${definitionId}:`, instance.value);
      } else {
        // console.log(`SettingsService: No local instance found after error for ${definitionId}`);
      }
      return instance?.value as T;
    }
  }

  async setSetting<T>(definitionId: string, value: T, context: { userId?: string; pageId?: string } = {}): Promise<void> {
    // console.log(`SettingsService: Setting ${definitionId} with value:`, value, 'and context:', context);
    
    await this.ensureDefinitionLoaded(definitionId);
    const definition = this.definitions.get(definitionId);
    if (!definition) {
      console.error(`SettingsService: Setting definition ${definitionId} not found`);
      throw new Error(`Setting definition ${definitionId} not found`);
    }

    // Determine the appropriate scope based on context
    let scope: SettingScope;
    if (context.userId && context.pageId) {
      scope = 'user_page';
    } else if (context.userId) {
      scope = 'user';
    } else if (context.pageId) {
      scope = 'page';
    } else {
      scope = 'system';
    }
    // console.log(`SettingsService: Determined scope for ${definitionId}: ${scope}`);

    // Validate the scope is allowed
    if (!definition.allowedScopes.includes(scope)) {
      console.error(`SettingsService: Scope ${scope} is not allowed for setting ${definitionId}`);
      throw new Error(`Scope ${scope} is not allowed for setting ${definitionId}`);
    }

    // Validate value against definition
    try {
      this.validateValue(value, definition);
      // console.log(`SettingsService: Value validation passed for ${definitionId}`);
    } catch (validationError) {
      console.error(`SettingsService: Value validation failed for ${definitionId}:`, validationError);
      throw validationError;
    }

    // Try to save to backend first
    // console.log(`SettingsService: Attempting to save ${definitionId} to backend`);
    const backendSaved = await this.saveToBackend(definitionId, value, context);
    // console.log(`SettingsService: Backend save result for ${definitionId}:`, backendSaved ? 'Success' : 'Failed');
    
    // Also save locally regardless of backend success
    // Find or create instance
    // console.log(`SettingsService: Looking for existing instance of ${definitionId}`);
    let instance = await this.getSettingInstance(definitionId, context);
    if (!instance) {
      // console.log(`SettingsService: No existing instance found for ${definitionId}, creating new one`);
      instance = await this.createSettingInstance(definitionId, value, context);
    } else {
      // console.log(`SettingsService: Updating existing instance of ${definitionId}`);
      await this.updateSettingInstance(instance.id, value);
    }

    // Notify subscribers
    // console.log(`SettingsService: Notifying subscribers for ${definitionId}`);
    this.notifySubscribers(definitionId, value);
    this.notifyCategorySubscribers(definition.category);
    // console.log(`SettingsService: Setting ${definitionId} completed successfully`);
  }

  async deleteSetting(definitionId: string, context: { userId?: string; pageId?: string }): Promise<void> {
    const instance = await this.getSettingInstance(definitionId, context);
    if (!instance) {
      throw new Error(`Setting instance ${definitionId} not found`);
    }

    this.instances.delete(instance.id);
    this.notifySubscribers(instance.id, undefined);

    const definition = this.definitions.get(instance.definitionId);
    if (definition) {
      this.notifyCategorySubscribers(definition.category);
    }

    this.saveToStorage();
  }

  // Settings definitions
  async registerSettingDefinition(definition: SettingDefinition): Promise<void> {
    if (this.definitions.has(definition.id)) {
      throw new Error(`Setting definition ${definition.id} already exists`);
    }

    this.definitions.set(definition.id, definition);
    this.saveToStorage();
  }

  async getSettingDefinitions(filter?: { category?: string; tags?: string[] }): Promise<SettingDefinition[]> {
    let definitions = Array.from(this.definitions.values());

    if (filter?.category) {
      definitions = definitions.filter(def => def.category === filter.category);
    }

    if (filter?.tags) {
      definitions = definitions.filter(def => 
        filter.tags!.every(tag => def.tags?.includes(tag))
      );
    }

    return definitions;
  }

  // Multiple settings instances
  async createSettingInstance(definitionId: string, value: any, context: { userId?: string; pageId?: string }, name?: string): Promise<SettingInstance> {
    await this.ensureDefinitionLoaded(definitionId);
    const definition = this.definitions.get(definitionId);
    if (!definition) {
      throw new Error(`Setting definition ${definitionId} not found`);
    }

    // Check if multiple instances are allowed, and if not, check for existing instances in the same context
    if (!definition.isMultiple && this.hasInstancesForDefinitionInContext(definitionId, context)) {
      throw new Error(`Setting ${definitionId} does not support multiple instances`);
    }

    // Determine the appropriate scope based on context
    let scope: SettingScope;
    if (context.userId && context.pageId) {
      scope = 'user_page';
    } else if (context.userId) {
      scope = 'user';
    } else if (context.pageId) {
      scope = 'page';
    } else {
      scope = 'system';
    }

    // Validate the scope is allowed
    if (!definition.allowedScopes.includes(scope)) {
      throw new Error(`Scope ${scope} is not allowed for setting ${definitionId}`);
    }

    this.validateValue(value, definition);

    // Use crypto.randomUUID if available, otherwise use our fallback
    let uuid;
    try {
      uuid = crypto.randomUUID();
    } catch (e) {
      uuid = this.generateUUID();
    }
    
    const instance: SettingInstance = {
      id: uuid,
      definitionId,
      name: name || definition.name,
      value,
      scope,
      userId: context.userId,
      pageId: context.pageId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };

    this.instances.set(instance.id, instance);
    this.notifyCategorySubscribers(definition.category);
    this.saveToStorage();

    return instance;
  }

  async getSettingInstance(definitionId: string, context: { userId?: string; pageId?: string }): Promise<SettingInstance | undefined> {
    await this.ensureDefinitionLoaded(definitionId);
    const definition = this.definitions.get(definitionId);
    if (!definition) {
      throw new Error(`Setting definition ${definitionId} not found`);
    }

    // Determine the appropriate scope based on context
    let scope: SettingScope;
    if (context.userId && context.pageId) {
      scope = 'user_page';
    } else if (context.userId) {
      scope = 'user';
    } else if (context.pageId) {
      scope = 'page';
    } else {
      scope = 'system';
    }

    // Validate the scope is allowed
    if (!definition.allowedScopes.includes(scope)) {
      throw new Error(`Scope ${scope} is not allowed for setting ${definitionId}`);
    }

    // Find matching instance
    return Array.from(this.instances.values()).find(instance => 
      instance.definitionId === definitionId &&
      instance.scope === scope &&
      instance.userId === context.userId &&
      instance.pageId === context.pageId
    );
  }

  async getSettingInstances(definitionId: string, context: { userId?: string; pageId?: string }): Promise<SettingInstance[]> {
    return Array.from(this.instances.values())
      .filter(instance => instance.definitionId === definitionId && 
        (!context.userId || instance.userId === context.userId) && 
        (!context.pageId || instance.pageId === context.pageId));
  }

  async updateSettingInstance(instanceId: string, value: any): Promise<SettingInstance> {
    const instance = this.instances.get(instanceId);
    if (!instance) {
      throw new Error(`Setting instance ${instanceId} not found`);
    }

    await this.ensureDefinitionLoaded(instance.definitionId);
    const definition = this.definitions.get(instance.definitionId);
    if (!definition) {
      throw new Error(`Setting definition ${instance.definitionId} not found`);
    }

    // Validate value against definition
    this.validateValue(value, definition);

    // Update instance
    const updatedInstance = {
      ...instance,
      value,
      updatedAt: new Date().toISOString()
    };
    this.instances.set(instanceId, updatedInstance);

    // Notify subscribers
    this.notifySubscribers(instanceId, value);
    this.notifyCategorySubscribers(definition.category);

    // Persist changes
    this.saveToStorage();
    
    return updatedInstance;
  }

  async deleteSettingInstance(instanceId: string): Promise<void> {
    const instance = this.instances.get(instanceId);
    if (!instance) {
      throw new Error(`Setting instance ${instanceId} not found`);
    }

    this.instances.delete(instanceId);
    this.notifySubscribers(instanceId, undefined);

    await this.ensureDefinitionLoaded(instance.definitionId);
    const definition = this.definitions.get(instance.definitionId);
    if (definition) {
      this.notifyCategorySubscribers(definition.category);
    }

    this.saveToStorage();
  }

  // Subscription methods
  subscribe(key: string, callback: SettingsSubscriber): () => void {
    if (!this.subscribers.has(key)) {
      this.subscribers.set(key, new Set());
    }
    this.subscribers.get(key)!.add(callback);

    return () => {
      const callbacks = this.subscribers.get(key);
      if (callbacks) {
        callbacks.delete(callback);
        if (callbacks.size === 0) {
          this.subscribers.delete(key);
        }
      }
    };
  }

  subscribeToCategory(category: string, callback: SettingsSubscriber): () => void {
    if (!this.categorySubscribers.has(category)) {
      this.categorySubscribers.set(category, new Set());
    }
    this.categorySubscribers.get(category)!.add(callback);

    return () => {
      const callbacks = this.categorySubscribers.get(category);
      if (callbacks) {
        callbacks.delete(callback);
        if (callbacks.size === 0) {
          this.categorySubscribers.delete(category);
        }
      }
    };
  }

  // Private helper methods
  private validateValue(value: any, definition: SettingDefinition): void {
    // Type validation
    if (typeof value !== definition.type && definition.type !== 'object') {
      throw new Error(`Invalid type for setting ${definition.id}. Expected ${definition.type}, got ${typeof value}`);
    }

    // Required validation
    if (definition.validation?.required && (value === undefined || value === null)) {
      throw new Error(`Setting ${definition.id} is required`);
    }

    // Pattern validation
    if (definition.validation?.pattern && typeof value === 'string') {
      const regex = new RegExp(definition.validation.pattern);
      if (!regex.test(value)) {
        throw new Error(`Setting ${definition.id} does not match required pattern`);
      }
    }

    // Number range validation
    if (typeof value === 'number') {
      if (definition.validation?.min !== undefined && value < definition.validation.min) {
        throw new Error(`Setting ${definition.id} must be >= ${definition.validation.min}`);
      }
      if (definition.validation?.max !== undefined && value > definition.validation.max) {
        throw new Error(`Setting ${definition.id} must be <= ${definition.validation.max}`);
      }
    }

    // Enum validation
    if (definition.validation?.enum && !definition.validation.enum.includes(value)) {
      throw new Error(`Setting ${definition.id} must be one of: ${definition.validation.enum.join(', ')}`);
    }
  }

  private notifySubscribers(key: string, value: any): void {
    const callbacks = this.subscribers.get(key);
    if (callbacks) {
      callbacks.forEach(callback => callback(value));
    }
  }

  private notifyCategorySubscribers(category: string): void {
    const callbacks = this.categorySubscribers.get(category);
    if (callbacks) {
      const categorySettings = Array.from(this.instances.values())
        .filter(instance => {
          const definition = this.definitions.get(instance.definitionId);
          return definition?.category === category;
        });
      callbacks.forEach(callback => callback(categorySettings));
    }
  }

  private hasInstancesForDefinition(definitionId: string): boolean {
    return Array.from(this.instances.values())
      .some(instance => instance.definitionId === definitionId);
  }

  private hasInstancesForDefinitionInContext(definitionId: string, context: { userId?: string; pageId?: string }): boolean {
    // Determine the appropriate scope based on context
    let scope: SettingScope;
    if (context.userId && context.pageId) {
      scope = 'user_page';
    } else if (context.userId) {
      scope = 'user';
    } else if (context.pageId) {
      scope = 'page';
    } else {
      scope = 'system';
    }

    return Array.from(this.instances.values())
      .some(instance =>
        instance.definitionId === definitionId &&
        instance.scope === scope &&
        instance.userId === context.userId &&
        instance.pageId === context.pageId
      );
  }

  private loadFromStorage(): void {
    try {
      const definitionsData = localStorage.getItem('settings_definitions');
      const instancesData = localStorage.getItem('settings_instances');

      if (definitionsData) {
        const definitions = JSON.parse(definitionsData);
        this.definitions = new Map(Object.entries(definitions));
      }

      if (instancesData) {
        const instances = JSON.parse(instancesData);
        this.instances = new Map(Object.entries(instances));
      }
    } catch (error) {
      console.error('Error loading settings from storage:', error);
    }
  }

  private saveToStorage(): void {
    try {
      const definitions = Object.fromEntries(this.definitions);
      const instances = Object.fromEntries(this.instances);

      localStorage.setItem('settings_definitions', JSON.stringify(definitions));
      localStorage.setItem('settings_instances', JSON.stringify(instances));
    } catch (error) {
      console.error('Error saving settings to storage:', error);
    }
  }
  
  /**
   * Save setting to backend API
   * This method sends the setting to the backend API to persist it in the database
   */
  // Helper function to generate a UUID for environments where crypto.randomUUID is not available
  private generateUUID(): string {
    // Simple UUID generator for environments where crypto.randomUUID is not available
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  private async saveToBackend(definitionId: string, value: any, context: { userId?: string; pageId?: string }): Promise<boolean> {
    try {
      // Get the API service directly
      const apiService = ApiService.getInstance();
      
      // If the definition doesn't exist in our local cache, create a temporary one
      let definition = this.definitions.get(definitionId);
      if (!definition) {
        console.warn(`Definition ${definitionId} not found in local cache, creating temporary definition`);
        definition = {
          id: definitionId,
          name: definitionId,
          description: 'Auto-generated definition',
          category: 'general',
          type: 'object',
          allowedScopes: ['user', 'system', 'page', 'user_page'],
          isMultiple: false
        };
        
        // Store the temporary definition in our local cache
        this.definitions.set(definitionId, definition);
      }
      
      // Determine the scope based on context
      let scope: SettingScope;
      if (context.userId && context.pageId) {
        scope = 'user_page';
      } else if (context.userId) {
        scope = 'user';
      } else if (context.pageId) {
        scope = 'page';
      } else {
        scope = 'system';
      }
      
      // First check if an instance already exists for this definition and context
      try {
        // Build query parameters to find existing instances
        const params: Record<string, string> = {
          definition_id: definitionId,
          scope: scope
        };
        
        if (context.userId) {
          params.user_id = context.userId === 'current' ? 'current' : context.userId;
        } else if (scope === 'user') {
          params.user_id = 'current';
        }
        
        if (context.pageId) {
          params.page_id = context.pageId;
        }
        
        // console.log(`Checking for existing setting instances: ${definitionId} with params:`, params);
        
        // Use the API service to make the request
        const data = await apiService.get('/api/v1/settings/instances', { params });
        // console.log('Existing settings found:', data);
        
        // If we found an existing instance, include its ID to update it
        let existingId = null;
        if (Array.isArray(data) && data.length > 0) {
          existingId = data[0].id;
          // console.log(`Found existing setting instance with ID: ${existingId}`);
        }
        
        // Prepare the data for the API
        const settingData = {
          id: existingId, // Include ID if we found an existing instance
          definition_id: definitionId,
          name: definition.name,
          value: value,
          scope: scope,
          user_id: context.userId || 'current', // Always use 'current' if no userId is provided
          page_id: context.pageId || null
        };
        
        // console.log('Saving setting to backend:', settingData);
        
        // Use the API service to make the request
        const response = await apiService.post('/api/v1/settings/instances', settingData);
        // console.log('Setting saved to backend successfully:', response);
        return true;
      } catch (apiError) {
        console.error('API error checking or saving setting to backend:', apiError);
        
        // If we couldn't check for existing instances, try a direct save
        try {
          // Prepare the data for the API without an ID
          const settingData = {
            definition_id: definitionId,
            name: definition.name,
            value: value,
            scope: scope,
            user_id: context.userId || 'current',
            page_id: context.pageId || null
          };
          
          // console.log('Attempting direct save to backend:', settingData);
          
          // Use the API service to make the request
          const response = await apiService.post('/api/v1/settings/instances', settingData);
          // console.log('Setting saved to backend successfully:', response);
          return true;
        } catch (directSaveError) {
          console.error('API error on direct save to backend:', directSaveError);
          return false;
        }
      }
    } catch (error) {
      console.error('Error saving setting to backend:', error);
      return false;
    }
  }
  
  /**
   * Load settings from backend API
   * This method fetches settings from the backend API
   */
  private async loadFromBackend(definitionId: string, context: { userId?: string; pageId?: string }): Promise<any> {
    try {
      // Get the API service directly
      const apiService = ApiService.getInstance();
      
      // Build query parameters
      const params: Record<string, string> = {
        definition_id: definitionId
      };
      
      // For user scope, always include user_id=current if not specified
      if (context.userId) {
        params.user_id = context.userId === 'current' ? 'current' : context.userId;
      } else if (!context.pageId) {
        // If no pageId is provided, assume user scope with current user
        params.user_id = 'current';
      }
      
      if (context.pageId) {
        params.page_id = context.pageId;
      }
      
      // Determine scope based on context
      let scope: SettingScope;
      if (context.userId && context.pageId) {
        scope = 'user_page';
        params.scope = 'user_page';
      } else if (context.userId) {
        scope = 'user';
        params.scope = 'user';
      } else if (context.pageId) {
        scope = 'page';
        params.scope = 'page';
      } else {
        scope = 'system';
        params.scope = 'system';
      }
      
      // console.log(`Loading setting from backend: ${definitionId} with params:`, params);
      
      try {
        // Use the API service to make the request
        const data = await apiService.get('/api/v1/settings/instances', { params });
        // console.log('Settings loaded from backend:', data);
        
        // If we got an array, return the first item's value
        if (Array.isArray(data) && data.length > 0) {
          // If the definition doesn't exist in our local cache, create it
          if (!this.definitions.has(definitionId) && data[0].definition) {
            const definition = {
              id: definitionId,
              name: data[0].definition.name || definitionId,
              description: data[0].definition.description || '',
              category: data[0].definition.category || 'general',
              type: data[0].definition.type || 'object',
              allowedScopes: data[0].definition.allowed_scopes || ['user', 'system', 'page', 'user_page'],
              isMultiple: data[0].definition.is_multiple || false,
              tags: data[0].definition.tags || []
            };
            
            this.definitions.set(definitionId, definition);
            this.saveToStorage();
          }
          
          return data[0].value;
        }
        
        // If we got a single object with a value property
        if (data && data.value) {
          return data.value;
        }
        
        return null;
      } catch (apiError) {
        console.error('API error loading setting from backend:', apiError);
        return null;
      }
    } catch (error) {
      console.error('Error loading settings from backend:', error);
      return null;
    }
  }

  /**
   * Load all definitions from backend if not already loaded, or when a specific definition is missing.
   */
  private async ensureDefinitionLoaded(definitionId: string): Promise<void> {
    if (this.definitions.has(definitionId) && this.definitionsLoadedFromBackend) {
      return;
    }

    // Try to fetch definitions from backend
    try {
      const apiService = ApiService.getInstance();
      const defs = await apiService.get<any[]>('/api/v1/settings/definitions');
      if (Array.isArray(defs)) {
        defs.forEach(def => {
          if (!def?.id) return;
          const mapped: SettingDefinition = {
            id: def.id,
            name: def.name || def.id,
            description: def.description || '',
            category: def.category || 'general',
            type: def.type || 'object',
            allowedScopes: Array.isArray(def.allowed_scopes) ? def.allowed_scopes : def.allowedScopes || ['system', 'user', 'page', 'user_page'],
            isMultiple: def.is_multiple ?? def.isMultiple ?? false,
            tags: def.tags || [],
          };
          this.definitions.set(mapped.id, mapped);
        });
        this.definitionsLoadedFromBackend = true;
        this.saveToStorage();
      }
    } catch (error) {
      // Silent failure; callers will still error if definition not present
      console.warn('SettingsService: unable to load definitions from backend', error);
    }
  }
}
