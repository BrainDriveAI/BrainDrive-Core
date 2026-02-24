import { AbstractBaseService } from './base/BaseService';
import { EnhancedPluginStateConfig } from './StateConfigurationManager';

// Types for database persistence
export interface DatabaseStateRecord {
  id: string;
  plugin_id: string;
  page_id?: string;
  state_key?: string;
  state_data: any;
  state_strategy: 'none' | 'session' | 'persistent' | 'custom';
  state_schema_version?: string;
  compression_type?: string;
  state_size: number;
  last_accessed: string;
  access_count: number;
  is_active: boolean;
  version: number;
  device_id?: string;
  sync_status: 'synced' | 'pending' | 'conflict';
  ttl_expires_at?: string;
  created_at: string;
  updated_at: string;
}

export interface DatabaseStateQuery {
  plugin_id?: string;
  page_id?: string;
  state_key?: string;
  state_strategy?: 'none' | 'session' | 'persistent' | 'custom';
  sync_status?: 'synced' | 'pending' | 'conflict';
  is_active?: boolean;
  device_id?: string;
  limit?: number;
  offset?: number;
}

export interface DatabaseStateStats {
  total_states: number;
  active_states: number;
  total_size: number;
  plugins_with_state: number;
  average_state_size: number;
  last_activity?: string;
}

export interface SaveResult {
  success: boolean;
  record?: DatabaseStateRecord;
  error?: string;
}

export interface LoadResult {
  success: boolean;
  data?: any;
  record?: DatabaseStateRecord;
  error?: string;
}

export interface SyncResult {
  success: boolean;
  synced: DatabaseStateRecord[];
  conflicts: any[];
  errors: any[];
}

export interface DatabasePersistenceManagerInterface {
  // Basic CRUD operations
  saveState(pluginId: string, state: any, options?: DatabaseSaveOptions): Promise<SaveResult>;
  loadState(pluginId: string, options?: DatabaseLoadOptions): Promise<LoadResult>;
  clearState(pluginId: string, options?: DatabaseClearOptions): Promise<boolean>;
  
  // Query operations
  queryStates(query: DatabaseStateQuery): Promise<DatabaseStateRecord[]>;
  getStateStats(): Promise<DatabaseStateStats>;
  
  // Sync operations
  syncStates(states: any[], options?: DatabaseSyncOptions): Promise<SyncResult>;
  
  // Migration operations
  migrateFromSession(pluginId: string): Promise<boolean>;
  migrateToDatabase(sessionData: Record<string, any>): Promise<SyncResult>;
  
  // Cleanup operations
  cleanupExpiredStates(): Promise<number>;
}

export interface DatabaseSaveOptions {
  pageId?: string;
  stateKey?: string;
  deviceId?: string;
  ttlHours?: number;
  schemaVersion?: string;
  strategy?: 'none' | 'session' | 'persistent' | 'custom';
}

export interface DatabaseLoadOptions {
  pageId?: string;
  stateKey?: string;
  includeInactive?: boolean;
}

export interface DatabaseClearOptions {
  pageId?: string;
  stateKey?: string;
  clearAll?: boolean;
}

export interface DatabaseSyncOptions {
  deviceId?: string;
  forceOverwrite?: boolean;
}

class DatabasePersistenceManagerImpl extends AbstractBaseService implements DatabasePersistenceManagerInterface {
  private baseUrl: string;
  private deviceId: string;

  constructor() {
    super(
      'database-persistence-manager',
      { major: 1, minor: 0, patch: 0 },
      [
        {
          name: 'database-state-persistence',
          description: 'Database-backed plugin state persistence',
          version: '1.0.0'
        },
        {
          name: 'cross-device-sync',
          description: 'Cross-device state synchronization',
          version: '1.0.0'
        }
      ]
    );

    this.baseUrl = '/api/v1/plugin-state';
    this.deviceId = this.generateDeviceId();
  }

  async initialize(): Promise<void> {
    // Initialize database persistence manager
    console.log('Database persistence manager initialized');
  }

  async destroy(): Promise<void> {
    // Cleanup database persistence manager
    console.log('Database persistence manager destroyed');
  }

  private generateDeviceId(): string {
    // Generate a unique device identifier
    let deviceId = localStorage.getItem('braindrive_device_id');
    if (!deviceId) {
      deviceId = `device_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      localStorage.setItem('braindrive_device_id', deviceId);
    }
    return deviceId;
  }

  private async makeRequest(endpoint: string, options: RequestInit = {}): Promise<Response> {
    const url = `${this.baseUrl}${endpoint}`;
    const defaultHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    // Add authentication token if available
    const token = localStorage.getItem('accessToken');
    if (token) {
      defaultHeaders['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(url, {
      ...options,
      headers: {
        ...defaultHeaders,
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
    }

    return response;
  }

  async saveState(pluginId: string, state: any, options: DatabaseSaveOptions = {}): Promise<SaveResult> {
    try {
      const payload = {
        plugin_id: pluginId,
        page_id: options.pageId,
        state_key: options.stateKey,
        state_data: state,
        device_id: options.deviceId || this.deviceId,
        state_strategy: options.strategy || 'persistent',
        state_schema_version: options.schemaVersion,
        ttl_expires_at: options.ttlHours ? 
          new Date(Date.now() + options.ttlHours * 60 * 60 * 1000).toISOString() : 
          undefined
      };

      // Try to update existing state first
      const existingStates = await this.queryStates({
        plugin_id: pluginId,
        page_id: options.pageId,
        state_key: options.stateKey,
        limit: 1
      });

      let response: Response;
      if (existingStates.length > 0) {
        // Update existing state
        response = await this.makeRequest(`/${existingStates[0].id}`, {
          method: 'PUT',
          body: JSON.stringify(payload)
        });
      } else {
        // Create new state
        response = await this.makeRequest('/', {
          method: 'POST',
          body: JSON.stringify(payload)
        });
      }

      const record: DatabaseStateRecord = await response.json();
      return { success: true, record };

    } catch (error) {
      console.error('Database save error:', error);
      return { 
        success: false, 
        error: error instanceof Error ? error.message : 'Unknown error' 
      };
    }
  }

  async loadState(pluginId: string, options: DatabaseLoadOptions = {}): Promise<LoadResult> {
    try {
      const query: DatabaseStateQuery = {
        plugin_id: pluginId,
        page_id: options.pageId,
        state_key: options.stateKey,
        is_active: options.includeInactive ? undefined : true,
        limit: 1
      };

      const states = await this.queryStates(query);
      
      if (states.length === 0) {
        return { success: true, data: null };
      }

      const record = states[0];
      return { 
        success: true, 
        data: record.state_data,
        record 
      };

    } catch (error) {
      console.error('Database load error:', error);
      return { 
        success: false, 
        error: error instanceof Error ? error.message : 'Unknown error' 
      };
    }
  }

  async clearState(pluginId: string, options: DatabaseClearOptions = {}): Promise<boolean> {
    try {
      if (options.clearAll) {
        // Clear all states for the plugin
        const states = await this.queryStates({ plugin_id: pluginId });
        for (const state of states) {
          await this.makeRequest(`/${state.id}`, { method: 'DELETE' });
        }
        return true;
      } else {
        // Clear specific state
        const query: DatabaseStateQuery = {
          plugin_id: pluginId,
          page_id: options.pageId,
          state_key: options.stateKey,
          limit: 1
        };

        const states = await this.queryStates(query);
        if (states.length > 0) {
          await this.makeRequest(`/${states[0].id}`, { method: 'DELETE' });
        }
        return true;
      }

    } catch (error) {
      console.error('Database clear error:', error);
      return false;
    }
  }

  async queryStates(query: DatabaseStateQuery): Promise<DatabaseStateRecord[]> {
    try {
      const params = new URLSearchParams();
      
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          params.append(key, value.toString());
        }
      });

      const response = await this.makeRequest(`/?${params.toString()}`);
      return await response.json();

    } catch (error) {
      console.error('Database query error:', error);
      return [];
    }
  }

  async getStateStats(): Promise<DatabaseStateStats> {
    try {
      const response = await this.makeRequest('/stats');
      return await response.json();

    } catch (error) {
      console.error('Database stats error:', error);
      return {
        total_states: 0,
        active_states: 0,
        total_size: 0,
        plugins_with_state: 0,
        average_state_size: 0
      };
    }
  }

  async syncStates(states: any[], options: DatabaseSyncOptions = {}): Promise<SyncResult> {
    try {
      const payload = {
        device_id: options.deviceId || this.deviceId,
        states: states.map(state => ({
          plugin_id: state.pluginId,
          page_id: state.pageId,
          state_key: state.stateKey,
          state_data: state.data,
          state_strategy: state.strategy || 'persistent',
          device_id: options.deviceId || this.deviceId
        })),
        force_overwrite: options.forceOverwrite || false
      };

      const response = await this.makeRequest('/sync', {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      return await response.json();

    } catch (error) {
      console.error('Database sync error:', error);
      return {
        success: false,
        synced: [],
        conflicts: [],
        errors: [{ error: error instanceof Error ? error.message : 'Unknown error' }]
      };
    }
  }

  async migrateFromSession(pluginId: string): Promise<boolean> {
    try {
      // Get session storage data
      const sessionKey = `braindrive_plugin_state_${pluginId}`;
      const sessionData = sessionStorage.getItem(sessionKey);
      
      if (!sessionData) {
        return true; // Nothing to migrate
      }

      const parsedData = JSON.parse(sessionData);
      
      // Save to database
      const result = await this.saveState(pluginId, parsedData, {
        strategy: 'persistent',
        deviceId: this.deviceId
      });

      if (result.success) {
        // Remove from session storage after successful migration
        sessionStorage.removeItem(sessionKey);
        return true;
      }

      return false;

    } catch (error) {
      console.error('Session migration error:', error);
      return false;
    }
  }

  async migrateToDatabase(sessionData: Record<string, any>): Promise<SyncResult> {
    try {
      const states = Object.entries(sessionData).map(([pluginId, data]) => ({
        pluginId,
        data,
        strategy: 'persistent' as const
      }));

      const result = await this.syncStates(states, {
        deviceId: this.deviceId,
        forceOverwrite: false
      });

      // Clear session storage for successfully migrated states
      if (result.success) {
        result.synced.forEach(record => {
          const sessionKey = `braindrive_plugin_state_${record.plugin_id}`;
          sessionStorage.removeItem(sessionKey);
        });
      }

      return result;

    } catch (error) {
      console.error('Database migration error:', error);
      return {
        success: false,
        synced: [],
        conflicts: [],
        errors: [{ error: error instanceof Error ? error.message : 'Unknown error' }]
      };
    }
  }

  async cleanupExpiredStates(): Promise<number> {
    try {
      const response = await this.makeRequest('/cleanup', { method: 'DELETE' });
      const result = await response.json();
      return result.deleted_count || 0;

    } catch (error) {
      console.error('Database cleanup error:', error);
      return 0;
    }
  }
}

// Export singleton instance
export const databasePersistenceManager = new DatabasePersistenceManagerImpl();
export default databasePersistenceManager;