import { useState, useCallback } from 'react';
import { ModelInfo, Services } from '../types';
import { PROVIDER_SETTINGS_ID_MAP } from '../constants';

export const useModelSelection = (services?: Services) => {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null);
  const [isLoadingModels, setIsLoadingModels] = useState(true);

  /**
   * Load provider settings and models
   */
  const loadProviderSettings = useCallback(async () => {
    setIsLoadingModels(true);

    if (!services?.api) {
      setIsLoadingModels(false);
      return [] as ModelInfo[];
    }

    try {
      const resp = await services.api.get('/api/v1/ai/providers/all-models');
      // Extract models array in a tolerant way
      const raw = (resp && (resp as any).models)
        || (resp && (resp as any).data && (resp as any).data.models)
        || (Array.isArray(resp) ? resp : []);

      const loadedModels: ModelInfo[] = Array.isArray(raw)
        ? raw.map((m: any) => {
            const provider = m.provider || 'ollama';
            const providerId = PROVIDER_SETTINGS_ID_MAP[provider] || provider;
            const serverId = m.server_id || m.serverId || 'unknown';
            const serverName = m.server_name || m.serverName || 'Unknown Server';
            const name = m.name || m.id || '';
            return {
              name,
              provider,
              providerId,
              serverName,
              serverId,
            } as ModelInfo;
          })
        : [];

      if (loadedModels.length > 0) {
        setModels(loadedModels);
        setSelectedModel(loadedModels[0]);
        setIsLoadingModels(false);
        return loadedModels;
      }

      // Fallback: Try Ollama-only via settings + /api/v1/ollama/models
      try {
        const settingsResp = await services.api.get('/api/v1/settings/instances', {
          params: {
            definition_id: 'ollama_servers_settings',
            scope: 'user',
            user_id: 'current',
          },
        });

        let settingsData: any = null;
        if (Array.isArray(settingsResp) && settingsResp.length > 0) settingsData = settingsResp[0];
        else if (settingsResp && typeof settingsResp === 'object') {
          const obj = settingsResp as any;
          if (obj.data) settingsData = Array.isArray(obj.data) ? obj.data[0] : obj.data;
          else settingsData = settingsResp;
        }

        const fallbackModels: ModelInfo[] = [];
        if (settingsData && settingsData.value) {
          const parsedValue = typeof settingsData.value === 'string'
            ? JSON.parse(settingsData.value)
            : settingsData.value;
          const servers = Array.isArray(parsedValue?.servers) ? parsedValue.servers : [];
          for (const server of servers) {
            try {
              const params: Record<string, string> = {
                server_url: encodeURIComponent(server.serverAddress),
                settings_id: 'ollama_servers_settings',
                server_id: server.id,
              };
              if (server.apiKey) params.api_key = server.apiKey;
              const modelResponse = await services.api.get('/api/v1/ollama/models', { params });
              const serverModels = Array.isArray(modelResponse) ? modelResponse : [];
              for (const m of serverModels) {
                fallbackModels.push({
                  name: m.name,
                  provider: 'ollama',
                  providerId: 'ollama_servers_settings',
                  serverName: server.serverName,
                  serverId: server.id,
                });
              }
            } catch (innerErr) {
              console.error('Fallback: error loading Ollama models for server', server?.serverName, innerErr);
            }
          }
        }

        setModels(fallbackModels);
        setSelectedModel(fallbackModels.length > 0 ? fallbackModels[0] : null);
        setIsLoadingModels(false);
        return fallbackModels;
      } catch (fallbackErr) {
        console.error('Fallback: error loading Ollama settings/models:', fallbackErr);
        setModels([]);
        setSelectedModel(null);
        setIsLoadingModels(false);
        return [] as ModelInfo[];
      }
    } catch (error: any) {
      console.error('Error loading models from all providers:', error);
      setModels([]);
      setSelectedModel(null);
      setIsLoadingModels(false);
      return [] as ModelInfo[];
    }
  }, [services?.api]);

  /**
   * Handle model selection change
   */
  const handleModelChange = useCallback((event: React.ChangeEvent<HTMLSelectElement>) => {
    const modelId = event.target.value;
    const model = models.find(m => 
      `${m.provider}_${m.serverId}_${m.name}` === modelId
    );
    
    if (model) {
      setSelectedModel(model);
      return model;
    }
    return null;
  }, [models]);

  /**
   * Broadcast model selection event
   */
  const broadcastModelSelection = useCallback((model: ModelInfo) => {
    if (!services?.event) {
      return;
    }
    
    // Create model selection message
    const modelInfo = {
      type: 'model.selection',
      content: {
        model: {
          name: model.name,
          provider: model.provider,
          providerId: model.providerId,
          serverName: model.serverName,
          serverId: model.serverId
        },
        timestamp: new Date().toISOString()
      }
    };
    
    // Send to event system
    services.event.sendMessage('ai-prompt-chat', modelInfo.content);
  }, [services?.event]);

  return {
    models,
    selectedModel,
    isLoadingModels,
    loadProviderSettings,
    handleModelChange,
    broadcastModelSelection,
    setSelectedModel
  };
};
