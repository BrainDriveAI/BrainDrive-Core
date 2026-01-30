// Base installation method types
export type InstallationMethod = 'github' | 'local-file';

export interface BaseInstallRequest {
  method: InstallationMethod;
}

export interface GitHubInstallRequest extends BaseInstallRequest {
  method: 'github';
  repo_url: string;
  version?: string;
}

export interface LocalFileInstallRequest extends BaseInstallRequest {
  method: 'local-file';
  file: File;
  filename: string;
}

export type PluginInstallRequest = GitHubInstallRequest | LocalFileInstallRequest;

// Legacy support - keeping for backward compatibility during transition
export interface LegacyPluginInstallRequest {
  repo_url: string;
  version?: string;
}

export type PluginType = 'frontend' | 'backend' | 'fullstack';

export interface PluginInstallResponse {
  status: 'success' | 'error';
  message: string;
  data?: {
    plugin_id: string;
    plugin_slug: string;
    modules_created: string[];
    plugin_directory: string;
    source: string;
    repo_url?: string; // Optional for file uploads
    version?: string; // Optional for file uploads
    filename?: string; // For file uploads
    file_size?: number; // For file uploads
    plugin_type?: PluginType; // Backend plugin architecture field
  };
  error?: string;
}

export interface InstallationStep {
  id: string;
  label: string;
  status: 'pending' | 'in-progress' | 'completed' | 'error';
  message?: string;
  error?: string;
}

export interface PluginUpdateInfo {
  plugin_id: string;
  current_version: string;
  latest_version: string;
  repo_url: string;
}

export interface AvailableUpdatesResponse {
  status: 'success' | 'error';
  data: {
    available_updates: PluginUpdateInfo[];
    total_count: number;
  };
}

export interface ErrorDetails {
  error: string;
  step: string;
  repo_url?: string;
  version?: string;
  user_id?: string;
  plugin_slug?: string;
  exception_type?: string;
  validation_error?: string;
  filename?: string; // For file upload errors
  file_size?: number; // For file upload errors
}

export interface PluginInstallationState {
  isInstalling: boolean;
  currentStep: number;
  steps: InstallationStep[];
  result: PluginInstallResponse | null;
  error: string | null;
  errorDetails?: ErrorDetails;
  suggestions?: string[];
}

// File upload specific types
export interface FileUploadState {
  file: File | null;
  uploading: boolean;
  progress: number;
  error: string | null;
}

export interface ArchiveValidationResult {
  isValid: boolean;
  format: 'zip' | 'rar' | 'tar.gz' | 'unknown';
  size: number;
  error?: string;
}

export interface FileUploadProgress {
  loaded: number;
  total: number;
  percentage: number;
}

// Plugin Testing Types
export interface PluginTestResponse {
  status: 'success' | 'error' | 'partial';
  message: string;
  details: {
    backend: BackendTestResult;
    frontend: FrontendTestResult;
    overall: OverallTestResult;
  };
}

export interface BackendTestResult {
  plugin_installed: boolean;
  files_exist: boolean;
  manifest_valid: boolean;
  bundle_accessible: boolean;
  modules_configured: ModuleConfigTest[];
  errors: string[];
  warnings: string[];
}

export interface FrontendTestResult {
  success: boolean;
  loadedModules?: number;
  moduleTests?: ModuleInstantiationTest[];
  error?: string;
}

export interface ModuleConfigTest {
  moduleName: string;
  configured: boolean;
  hasRequiredFields: boolean;
  issues: string[];
}

export interface ModuleInstantiationTest {
  moduleName: string;
  success: boolean;
  error?: string;
  componentCreated: boolean;
}

export interface OverallTestResult {
  canLoad: boolean;
  canInstantiate: boolean;
  issues: string[];
  recommendations: string[];
}

export interface PluginTestState {
  isLoading: boolean;
  result: PluginTestResponse | null;
  hasRun: boolean;
}

// Installation method configuration
export interface InstallationMethodConfig {
  id: InstallationMethod;
  label: string;
  description: string;
  icon: React.ComponentType;
  disabled?: boolean;
}

// File validation constants
export const SUPPORTED_ARCHIVE_FORMATS = ['.zip', '.rar', '.tar.gz', '.tgz'] as const;
export const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB
export const MIN_FILE_SIZE = 1024; // 1KB

export type SupportedArchiveFormat = typeof SUPPORTED_ARCHIVE_FORMATS[number];