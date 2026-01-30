import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import moduleService from '../services/moduleService';
import { PluginUpdateInfo } from '../../plugin-installer/types';
import { useAuth } from '../../../contexts/AuthContext';

const CACHE_TTL_MS = 60 * 60 * 1000;
const CACHE_PREFIX = 'pluginUpdates::';

const debugLog = (...args: unknown[]): void => {
  try {
    console.debug('[PluginUpdateFeed]', ...args);
  } catch {
    // no-op if console unavailable
  }
};

const isBrowserEnvironment = typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';



const derivePluginName = (rawName: unknown, pluginId: string): string => {
  const explicitName = typeof rawName === 'string' ? rawName.trim() : '';
  if (explicitName) {
    return explicitName;
  }

  const segments = pluginId.split('_').filter(Boolean);
  const candidate = segments.length > 1 ? segments.slice(1).join('_') : pluginId;

  const spaced = candidate
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();

  if (!spaced) {
    return pluginId;
  }

  return spaced
    .split(' ')
    .map(part => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ');
};

export type PluginUpdateFeedStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error';
export type PluginUpdateOperationStatus = 'idle' | 'updating' | 'success' | 'error';

export interface PluginUpdateFeedItem {
  pluginId: string;
  pluginName?: string;
  currentVersion: string;
  latestVersion: string;
  repoUrl?: string;
  lastChecked: string;
  status: PluginUpdateOperationStatus;
  error?: string;
}

interface PluginUpdatesCacheShape {
  fetchedAt: string;
  updates: Array<{
    pluginId: string;
    pluginName?: string;
    currentVersion: string;
    latestVersion: string;
    repoUrl?: string;
    lastChecked?: string;
  }>;
  dismissed: string[];
}

export interface PluginUpdateBatchProgress {
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
}

const normalizeUpdateInfo = (
  info: Partial<PluginUpdateInfo> & Record<string, any>,
  timestamp: string
): PluginUpdateFeedItem | null => {
  const pluginId = info.plugin_id ?? info.pluginId;
  if (!pluginId) {
    return null;
  }

  const currentVersion = info.current_version ?? info.currentVersion ?? '';
  const latestVersion = info.latest_version ?? info.latestVersion ?? '';
  const pluginName = derivePluginName(info.plugin_name ?? info.pluginName ?? info.name, pluginId);

  return {
    pluginId,
    pluginName,
    currentVersion,
    latestVersion,
    repoUrl: info.repo_url ?? info.repoUrl,
    lastChecked: timestamp,
    status: 'idle',
  };
};
export const usePluginUpdateFeed = (): UsePluginUpdateFeedResult => {
  const { user, isAuthenticated } = useAuth();
  const userIdentifier = useMemo(() => {
    if (!user) {
      return null;
    }

    const shape = user as Record<string, any>;
    const candidate = shape.id ?? shape.user_id ?? shape.email ?? shape.username;

    return candidate ? String(candidate) : null;
  }, [user]);

  const cacheKey = useMemo(() => {
    if (!isAuthenticated) {
      return null;
    }

    const suffix = userIdentifier ?? 'global';
    return `${CACHE_PREFIX}${suffix}`;
  }, [isAuthenticated, userIdentifier]);

  const [status, setStatus] = useState<PluginUpdateFeedStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [updates, setUpdates] = useState<PluginUpdateFeedItem[]>([]);
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [isUpdatingAll, setIsUpdatingAll] = useState(false);
  const [batchProgress, setBatchProgress] = useState<PluginUpdateBatchProgress>({
    total: 0,
    processed: 0,
    succeeded: 0,
    failed: 0,
  });

  const inflightRef = useRef<Promise<void> | null>(null);
  const updatesRef = useRef<PluginUpdateFeedItem[]>(updates);
  const dismissedRef = useRef<string[]>(dismissedIds);
  const lastCheckedRef = useRef<string | null>(lastChecked);
  const statusRef = useRef<PluginUpdateFeedStatus>(status);
  const isMountedRef = useRef(true);

  useEffect(() => {
    updatesRef.current = updates;
  }, [updates]);

  useEffect(() => {
    dismissedRef.current = dismissedIds;
  }, [dismissedIds]);

  useEffect(() => {
    lastCheckedRef.current = lastChecked;
  }, [lastChecked]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const dismissedSet = useMemo(() => new Set(dismissedIds), [dismissedIds]);

  const persistCache = useCallback((overrides?: Partial<{ updates: PluginUpdateFeedItem[]; dismissed: string[]; fetchedAt: string | null }>) => {
    if (!cacheKey || !isBrowserEnvironment) {
      return;
    }

    const updatesToPersist = overrides?.updates ?? updatesRef.current;
    const dismissedToPersist = overrides?.dismissed ?? dismissedRef.current;
    const fetchedAt = overrides?.fetchedAt ?? lastCheckedRef.current;

    if (!fetchedAt) {
      return;
    }

    debugLog('persistCache', { cacheKey, fetchedAt, updates: updatesToPersist.length, dismissed: dismissedToPersist.length });
    const payload: PluginUpdatesCacheShape = {
      fetchedAt,
      updates: updatesToPersist.map(({ pluginId, pluginName, currentVersion, latestVersion, repoUrl, lastChecked: itemLastChecked }) => ({
        pluginId,
        pluginName,
        currentVersion,
        latestVersion,
        repoUrl,
        lastChecked: itemLastChecked,
      })),
      dismissed: dismissedToPersist,
    };

    try {
      window.localStorage.setItem(cacheKey, JSON.stringify(payload));
    } catch (storageError) {
      console.warn('Failed to persist plugin update cache', storageError);
    }
  }, [cacheKey]);

  const loadFromCache = useCallback(() => {
    if (!cacheKey || !isBrowserEnvironment) {
      return { hasCache: false, isFresh: false };
    }

    const raw = window.localStorage.getItem(cacheKey);
    if (!raw) {
      return { hasCache: false, isFresh: false };
    }

    try {
      const parsed = JSON.parse(raw) as PluginUpdatesCacheShape;
      if (!parsed || !Array.isArray(parsed.updates)) {
        return { hasCache: false, isFresh: false };
      }

      const fetchedAt = parsed.fetchedAt ?? new Date().toISOString();
      const hydrated = parsed.updates
        .map(item => {
          const base = normalizeUpdateInfo(
            {
              plugin_id: item.pluginId,
              plugin_name: item.pluginName,
              current_version: item.currentVersion,
              latest_version: item.latestVersion,
              repo_url: item.repoUrl,
            },
            item.lastChecked ?? fetchedAt
          );
          return base;
        })
        .filter((item): item is PluginUpdateFeedItem => Boolean(item));

      if (!isMountedRef.current) {
        return { hasCache: false, isFresh: false };
      }

      setUpdates(hydrated);
      setLastChecked(fetchedAt);

      const validDismissed = Array.isArray(parsed.dismissed)
        ? parsed.dismissed.filter(id => hydrated.some(update => update.pluginId === id))
        : [];

      setDismissedIds(validDismissed);

      const isFresh = Date.now() - new Date(fetchedAt).getTime() < CACHE_TTL_MS;
      debugLog('loadFromCache', { cacheKey, hydrated: hydrated.length, dismissed: validDismissed.length, fetchedAt, isFresh });

      const visibleCount = hydrated.filter(update => !validDismissed.includes(update.pluginId)).length;
      setStatus(visibleCount > 0 ? 'ready' : 'empty');
      setError(null);

      return { hasCache: true, isFresh };
    } catch (parseError) {
      console.warn('Failed to read plugin update cache', parseError);
      window.localStorage.removeItem(cacheKey);
      return { hasCache: false, isFresh: false };
    }
  }, [cacheKey]);

  const fetchUpdates = useCallback(async (force = false) => {
    if (!cacheKey) {
      debugLog('fetchUpdates:abort-no-cacheKey', { force });
      return;
    }

    if (inflightRef.current) {
      if (!force) {
        debugLog('fetchUpdates:await-existing', { cacheKey });
        await inflightRef.current;
        return;
      }

      debugLog('fetchUpdates:force-after-await', { cacheKey });
      await inflightRef.current;
    }

    const run = (async () => {
      if (!isMountedRef.current) {
        return;
      }

      debugLog('fetchUpdates:start', { cacheKey, force });
      setStatus('loading');
      setError(null);

      try {
        const response = (await moduleService.checkForUpdates()) as PluginUpdateInfo[];
        const fetchedAt = new Date().toISOString();

        debugLog('fetchUpdates:received', { items: Array.isArray(response) ? response.length : 'unknown', fetchedAt });

        const uniqueMap = new Map<string, PluginUpdateFeedItem>();
        (response || []).forEach(item => {
          const normalized = normalizeUpdateInfo(item, fetchedAt);
          if (normalized) {
            uniqueMap.set(normalized.pluginId, normalized);
          }
        });

        const nextUpdates = Array.from(uniqueMap.values());
        if (!isMountedRef.current) {
          return;
        }

        debugLog('fetchUpdates:normalized', { total: nextUpdates.length });

        setUpdates(nextUpdates);
        setLastChecked(fetchedAt);

        const currentDismissed = dismissedRef.current;
        const validDismissed = currentDismissed.filter(id => nextUpdates.some(update => update.pluginId === id));
        setDismissedIds(validDismissed);

        const visibleCount = nextUpdates.filter(update => !validDismissed.includes(update.pluginId)).length;
        const nextStatus: PluginUpdateFeedStatus = visibleCount > 0 ? 'ready' : 'empty';
        setStatus(nextStatus);

        debugLog('fetchUpdates:status', { status: nextStatus, visibleCount, dismissed: validDismissed.length });

        persistCache({ updates: nextUpdates, dismissed: validDismissed, fetchedAt });
      } catch (fetchError: unknown) {
        if (!isMountedRef.current) {
          return;
        }

        const message = fetchError instanceof Error ? fetchError.message : 'Unable to fetch plugin updates';
        debugLog('fetchUpdates:error', { message });
        setError(message);
        setStatus('error');
      } finally {
        inflightRef.current = null;
        debugLog('fetchUpdates:complete', { cacheKey });
      }
    })();

    inflightRef.current = run;
    await run;
  }, [cacheKey, persistCache]);

  const refresh = useCallback(async () => {
    dismissedRef.current = [];
    setDismissedIds([]);
    persistCache({ dismissed: [] });
    await fetchUpdates(true);
  }, [fetchUpdates, persistCache]);

  const retry = useCallback(async () => {
    await fetchUpdates(true);
  }, [fetchUpdates]);

  const dismiss = useCallback((pluginId: string) => {
    setDismissedIds(prev => {
      if (prev.includes(pluginId)) {
        return prev;
      }
      const next = [...prev, pluginId];
      persistCache({ dismissed: next });
      const remaining = updatesRef.current.filter(update => !next.includes(update.pluginId)).length;
      if (remaining === 0 && statusRef.current !== 'loading') {
        setStatus('empty');
      }
      return next;
    });
  }, [persistCache]);

  const runPluginUpdate = useCallback(async (pluginId: string) => {
    const existing = updatesRef.current.find(update => update.pluginId === pluginId);
    if (!existing) {
      debugLog('runPluginUpdate:missing', { pluginId });
      return { success: false, error: 'Plugin not found in update feed' };
    }

    debugLog('runPluginUpdate:start', { pluginId });
    setUpdates(prev =>
      prev.map(update =>
        update.pluginId === pluginId
          ? { ...update, status: 'updating', error: undefined }
          : update
      )
    );

    try {
      await moduleService.updatePlugin(pluginId);

      let nextUpdates: PluginUpdateFeedItem[] = [];
      setUpdates(prev => {
        nextUpdates = prev.filter(update => update.pluginId !== pluginId);
        return nextUpdates;
      });

      const nextDismissed = dismissedRef.current.filter(id => id !== pluginId);
      setDismissedIds(nextDismissed);

      const updatedAt = new Date().toISOString();
      setLastChecked(updatedAt);
      persistCache({ updates: nextUpdates, dismissed: nextDismissed, fetchedAt: updatedAt });

      const remainingVisible = nextUpdates.filter(update => !nextDismissed.includes(update.pluginId)).length;
      setStatus(remainingVisible > 0 ? 'ready' : 'empty');
      debugLog('runPluginUpdate:success', { pluginId });

      return { success: true };
    } catch (updateError: unknown) {
      const message = updateError instanceof Error ? updateError.message : 'Failed to update plugin';
      debugLog('runPluginUpdate:error', { pluginId, message });
      setUpdates(prev =>
        prev.map(update =>
          update.pluginId === pluginId
            ? { ...update, status: 'error', error: message }
            : update
        )
      );
      setError(message);
      return { success: false, error: message };
    }
  }, [persistCache]);

  const triggerUpdate = useCallback(async (pluginId: string) => {
    const result = await runPluginUpdate(pluginId);
    if (!result.success) {
      throw new Error(result.error || 'Failed to update plugin');
    }
  }, [runPluginUpdate]);

  const triggerUpdateAll = useCallback(async () => {
    if (isUpdatingAll) {
      return;
    }

    const pending = updatesRef.current.filter(update => !dismissedSet.has(update.pluginId));
    if (pending.length === 0) {
      return;
    }

    setIsUpdatingAll(true);
    setBatchProgress({ total: pending.length, processed: 0, succeeded: 0, failed: 0 });

    for (const item of pending) {
      const result = await runPluginUpdate(item.pluginId);
      setBatchProgress(prev => ({
        total: pending.length,
        processed: prev.processed + 1,
        succeeded: prev.succeeded + (result.success ? 1 : 0),
        failed: prev.failed + (result.success ? 0 : 1),
      }));
    }

    setIsUpdatingAll(false);
    setBatchProgress({ total: 0, processed: 0, succeeded: 0, failed: 0 });
  }, [dismissedSet, isUpdatingAll, runPluginUpdate]);

  useEffect(() => {
    debugLog('effect:hydration-check', { isAuthenticated, cacheKey });
    if (!isAuthenticated || !cacheKey) {
      setUpdates([]);
      setDismissedIds([]);
      setLastChecked(null);
      setStatus('idle');
      setError(null);
      return;
    }

    const result = loadFromCache();
    debugLog('effect:cache-result', result);
    if (!result.isFresh) {
      fetchUpdates();
    }
  }, [cacheKey, fetchUpdates, isAuthenticated, loadFromCache]);

  const visibleUpdates = useMemo(
    () => updates.filter(update => !dismissedSet.has(update.pluginId)),
    [updates, dismissedSet]
  );

  return {
    updates: visibleUpdates,
    status,
    error,
    lastChecked,
    isUpdatingAll,
    batchProgress,
    refresh,
    retry,
    dismiss,
    triggerUpdate,
    triggerUpdateAll,
  };
};

export default usePluginUpdateFeed;







































