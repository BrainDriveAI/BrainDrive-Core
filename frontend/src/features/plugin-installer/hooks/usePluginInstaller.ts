import { useState, useCallback } from 'react';
import {
  PluginInstallRequest,
  GitHubInstallRequest,
  LocalFileInstallRequest,
  PluginInstallResponse,
  PluginInstallationState,
  InstallationStep,
  PluginTestResponse
} from '../types';
import { pluginInstallerService } from '../services';

declare global {
  interface Window {
    refreshSidebar?: () => void;
    refreshPages?: () => void;
  }
}

const GITHUB_INSTALLATION_STEPS: Omit<InstallationStep, 'status'>[] = [
  { id: 'validate', label: 'Validating repository URL' },
  { id: 'download', label: 'Downloading plugin from repository' },
  { id: 'extract', label: 'Extracting and validating plugin' },
  { id: 'install', label: 'Installing plugin for your account' },
  { id: 'complete', label: 'Installation complete' }
];

const FILE_INSTALLATION_STEPS: Omit<InstallationStep, 'status'>[] = [
  { id: 'validate', label: 'Validating archive file' },
  { id: 'upload', label: 'Uploading plugin file' },
  { id: 'extract', label: 'Extracting and validating plugin' },
  { id: 'install', label: 'Installing plugin for your account' },
  { id: 'complete', label: 'Installation complete' }
];

export const usePluginInstaller = () => {
  const [installationState, setInstallationState] = useState<PluginInstallationState>({
    isInstalling: false,
    currentStep: 0,
    steps: GITHUB_INSTALLATION_STEPS.map(step => ({ ...step, status: 'pending' })),
    result: null,
    error: null
  });

  const resetInstallation = useCallback((method: 'github' | 'local-file' = 'github') => {
    const steps = method === 'github' ? GITHUB_INSTALLATION_STEPS : FILE_INSTALLATION_STEPS;
    setInstallationState({
      isInstalling: false,
      currentStep: 0,
      steps: steps.map(step => ({ ...step, status: 'pending' })),
      result: null,
      error: null
    });
  }, []);

  const updateStep = useCallback((stepIndex: number, status: InstallationStep['status'], message?: string, error?: string) => {
    setInstallationState(prev => ({
      ...prev,
      currentStep: stepIndex,
      steps: prev.steps.map((step, index) => {
        if (index === stepIndex) {
          return { ...step, status, message, error };
        } else if (index < stepIndex) {
          return { ...step, status: 'completed' };
        }
        return step;
      })
    }));
  }, []);

  const handleInstallationResult = useCallback((result: PluginInstallResponse): PluginInstallResponse => {
    if (result.status === 'success') {
      // Step 5: Complete
      updateStep(4, 'completed', `Plugin "${result.data?.plugin_slug}" installed successfully!`);
      setInstallationState(prev => ({
        ...prev,
        isInstalling: false,
        result
      }));

      // Trigger a sidebar/page refresh so new plugin routes appear immediately
      try {
        window.refreshSidebar?.();
        window.refreshPages?.();
      } catch (err) {
        console.warn('Failed to refresh navigation after plugin install', err);
      }
    } else {
      // Determine which step failed based on the error details
      let failedStep = 3; // Default to install step
      let errorMessage = result.error || 'Installation failed';

      // Check if we have detailed error information
      const errorDetails = (result as any).errorDetails;
      if (errorDetails?.step) {
        switch (errorDetails.step) {
          case 'url_parsing':
            failedStep = 0;
            break;
          case 'release_lookup':
          case 'download_and_extract':
          case 'file_upload':
            failedStep = 1;
            break;
          case 'plugin_validation':
          case 'file_extraction':
            failedStep = 2;
            break;
          case 'lifecycle_manager_install':
          case 'lifecycle_manager_execution':
          default:
            failedStep = 3;
            break;
        }
      } else {
        // Fallback to text-based detection
        if (result.error?.includes('repository') || result.error?.includes('download') || result.error?.includes('upload')) {
          failedStep = 1;
        } else if (result.error?.includes('extract') || result.error?.includes('validate')) {
          failedStep = 2;
        }
      }

      // Create enhanced error message with suggestions if available
      let enhancedError = errorMessage;
      const suggestions = (result as any).suggestions;
      if (suggestions && suggestions.length > 0) {
        enhancedError += '\n\nSuggestions:\n' + suggestions.map((s: string) => `• ${s}`).join('\n');
      }

      updateStep(failedStep, 'error', undefined, enhancedError);
      setInstallationState(prev => ({
        ...prev,
        isInstalling: false,
        error: enhancedError,
        errorDetails: errorDetails,
        suggestions: suggestions
      }));
    }

    return result;
  }, [updateStep]);

  const handleGitHubInstallation = useCallback(async (request: GitHubInstallRequest): Promise<PluginInstallResponse> => {
    // Step 1: Validate URL
    updateStep(0, 'in-progress', 'Checking repository URL format...');
    const validation = pluginInstallerService.validateGitHubUrl(request.repo_url);
    if (!validation.isValid) {
      updateStep(0, 'error', undefined, validation.error);
      setInstallationState(prev => ({ ...prev, isInstalling: false, error: validation.error || 'Invalid URL' }));
      return {
        status: 'error',
        message: 'URL validation failed',
        error: validation.error
      };
    }
    updateStep(0, 'completed', 'Repository URL is valid');

    // Step 2: Download
    updateStep(1, 'in-progress', 'Contacting GitHub and downloading plugin...');

    // Step 3: Extract (we'll update this during the API call)
    updateStep(2, 'in-progress', 'Processing plugin files...');

    // Step 4: Install
    updateStep(3, 'in-progress', 'Installing plugin to your account...');

    // Make the actual API call
    const normalizedUrl = pluginInstallerService.normalizeGitHubUrl(request.repo_url);
    const result = await pluginInstallerService.installPlugin({
      ...request,
      repo_url: normalizedUrl
    });

    return handleInstallationResult(result);
  }, [updateStep, handleInstallationResult]);

  const handleFileInstallation = useCallback(async (request: LocalFileInstallRequest): Promise<PluginInstallResponse> => {
    // Step 1: Validate file
    updateStep(0, 'in-progress', 'Validating archive file...');
    // File validation is already done in the component, so we can mark as completed
    updateStep(0, 'completed', 'Archive file is valid');

    // Step 2: Upload
    updateStep(1, 'in-progress', 'Uploading plugin file...');

    // Step 3: Extract
    updateStep(2, 'in-progress', 'Processing plugin files...');

    // Step 4: Install
    updateStep(3, 'in-progress', 'Installing plugin to your account...');

    // Make the actual API call
    const result = await pluginInstallerService.installPlugin(request);

    return handleInstallationResult(result);
  }, [updateStep, handleInstallationResult]);

  const installPlugin = useCallback(async (request: PluginInstallRequest): Promise<PluginInstallResponse> => {
    try {
      // Set up the appropriate steps based on installation method
      const steps = request.method === 'github' ? GITHUB_INSTALLATION_STEPS : FILE_INSTALLATION_STEPS;
      
      setInstallationState(prev => ({
        ...prev,
        isInstalling: true,
        currentStep: 0,
        steps: steps.map(step => ({ ...step, status: 'pending' })),
        result: null,
        error: null
      }));

      if (request.method === 'github') {
        return await handleGitHubInstallation(request);
      } else if (request.method === 'local-file') {
        return await handleFileInstallation(request);
      } else {
        throw new Error(`Unsupported installation method: ${(request as any).method}`);
      }
    } catch (error: any) {
      console.error('Plugin installation error:', error);
      updateStep(3, 'error', undefined, error.message || 'Unexpected error occurred');
      setInstallationState(prev => ({
        ...prev,
        isInstalling: false,
        error: error.message || 'Unexpected error occurred'
      }));

      return {
        status: 'error',
        message: 'Installation failed',
        error: error.message || 'Unexpected error occurred'
      };
    }
  }, [handleGitHubInstallation, handleFileInstallation, updateStep]);

  const getPluginStatus = useCallback(async (pluginSlug: string) => {
    return await pluginInstallerService.getPluginStatus(pluginSlug);
  }, []);

  const uninstallPlugin = useCallback(async (pluginSlug: string) => {
    const result = await pluginInstallerService.uninstallPlugin(pluginSlug);

    // Refresh navigation so sidebar/pages reflect removal
    try {
      window.refreshSidebar?.();
      window.refreshPages?.();
    } catch (err) {
      console.warn('Failed to refresh navigation after plugin uninstall', err);
    }

    return result;
  }, []);

  const getAvailableUpdates = useCallback(async () => {
    return await pluginInstallerService.getAvailableUpdates();
  }, []);

  const testPluginLoading = useCallback(async (pluginSlug: string): Promise<PluginTestResponse> => {
    return await pluginInstallerService.testPluginLoading(pluginSlug);
  }, []);

  return {
    installationState,
    installPlugin,
    resetInstallation,
    getPluginStatus,
    uninstallPlugin,
    getAvailableUpdates,
    testPluginLoading,
    validateUrl: pluginInstallerService.validateGitHubUrl,
    normalizeUrl: pluginInstallerService.normalizeGitHubUrl
  };
};

export default usePluginInstaller;
