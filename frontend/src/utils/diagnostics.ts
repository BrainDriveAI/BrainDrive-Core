import { getAllModules, getAllPluginConfigs } from '../plugins';

export type LogLevel = 'info' | 'warn' | 'error';

export interface LogEntry {
  level: LogLevel;
  message: string;
  ts: string;
  context?: Record<string, any>;
}

const LOG_LIMIT = 50;
const logBuffer: LogEntry[] = [];

const pushLog = (entry: LogEntry) => {
  logBuffer.push(entry);
  if (logBuffer.length > LOG_LIMIT) {
    logBuffer.splice(0, logBuffer.length - LOG_LIMIT);
  }
};

export const diagnosticsLog = {
  info: (message: string, context?: Record<string, any>) =>
    pushLog({ level: 'info', message, context, ts: new Date().toISOString() }),
  warn: (message: string, context?: Record<string, any>) =>
    pushLog({ level: 'warn', message, context, ts: new Date().toISOString() }),
  error: (message: string, context?: Record<string, any>) =>
    pushLog({ level: 'error', message, context, ts: new Date().toISOString() }),
  entries: (): LogEntry[] => [...logBuffer],
  clear: () => {
    logBuffer.splice(0, logBuffer.length);
  },
};

export interface ClientDiagnostics {
  browser: {
    userAgent?: string;
    language?: string;
    languages?: readonly string[];
    platform?: string;
    vendor?: string;
  };
  viewport: {
    width?: number;
    height?: number;
    devicePixelRatio?: number;
  };
  hardware: {
    cores?: number;
    memoryGB?: number;
  };
  locale: {
    timezone?: string;
    locale?: string;
  };
}

export interface FrontendRegistrySummary {
  pluginCount: number;
  moduleCount: number;
  plugins: {
    id?: string;
    version?: string;
    moduleCount?: number;
    bundlelocation?: string;
  }[];
}

export interface DiagnosticsSnapshot {
  backend?: Record<string, any>;
  client: ClientDiagnostics;
  frontend: FrontendRegistrySummary;
  logs: LogEntry[];
  timestamp: string;
}

export const collectClientDiagnostics = (): ClientDiagnostics => {
  if (typeof window === 'undefined') {
    return { browser: {}, viewport: {}, hardware: {}, locale: {} };
  }

  const nav = window.navigator || ({} as Navigator);
  const timezone = Intl?.DateTimeFormat?.().resolvedOptions?.().timeZone;

  return {
    browser: {
      userAgent: nav.userAgent,
      language: nav.language,
      languages: nav.languages,
      platform: (nav as any).platform,
      vendor: (nav as any).vendor,
    },
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
    },
    hardware: {
      cores: (nav as any).hardwareConcurrency,
      memoryGB: (nav as any).deviceMemory,
    },
    locale: {
      timezone,
      locale: nav.language,
    },
  };
};

export const collectFrontendRegistry = (): FrontendRegistrySummary => {
  const pluginsObj = getAllPluginConfigs();
  const plugins = Object.values(pluginsObj || {});
  const modules = getAllModules();

  return {
    pluginCount: plugins.length,
    moduleCount: modules.length,
    plugins: plugins.map((plugin) => ({
      id: (plugin as any).id || (plugin as any).plugin_slug,
      version: (plugin as any).version,
      moduleCount: (plugin as any).modules?.length,
      bundlelocation: (plugin as any).bundlelocation || (plugin as any).bundle_location,
    })),
  };
};

export const buildDiagnosticsSnapshot = (backend?: Record<string, any>): DiagnosticsSnapshot => ({
  backend,
  client: collectClientDiagnostics(),
  frontend: collectFrontendRegistry(),
  logs: diagnosticsLog.entries(),
  timestamp: new Date().toISOString(),
});

export const buildIssueText = (data: DiagnosticsSnapshot): string => {
  const lines: string[] = [];

  lines.push('## BrainDrive Diagnostics');

  const app = data.backend?.app || {};
  lines.push(`App: ${app.name || 'BrainDrive'} (${app.environment || 'unknown'})`);
  if (app.version) lines.push(`Version: ${app.version}`);
  if (app.commit) lines.push(`Commit: ${app.commit}`);

  const db = data.backend?.backend?.db || data.backend?.db || {};
  lines.push(
    `Backend DB: ${db.type || 'unknown'}`
      + (db.version ? ` v${db.version}` : '')
      + (db.migration_version ? ` | migration ${db.migration_version}` : '')
  );

  const plugins = data.backend?.plugins || {};
  lines.push(
    `Plugins: ${plugins.count ?? data.frontend.pluginCount ?? 0} (modules: ${
      plugins.modules ?? data.frontend.moduleCount ?? 0
    })`
  );

  lines.push(
    `Browser: ${data.client.browser.userAgent || 'unknown'} | Locale: ${
      data.client.locale.locale || 'n/a'
    } | TZ: ${data.client.locale.timezone || 'n/a'}`
  );
  lines.push(
    `Viewport: ${data.client.viewport.width || '?'}x${data.client.viewport.height || '?'} @ ${
      data.client.viewport.devicePixelRatio || 1
    }`
  );

  if (Array.isArray(data.logs) && data.logs.length > 0) {
    lines.push('Recent Logs:');
    data.logs.slice(-5).forEach((log) => {
      lines.push(`- [${log.level}] ${log.ts} ${log.message}`);
    });
  }

  return lines.join('\n');
};
