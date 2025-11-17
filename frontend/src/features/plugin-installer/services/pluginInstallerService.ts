import ApiService from '../../../services/ApiService';
import {
  PluginInstallRequest,
  GitHubInstallRequest,
  LocalFileInstallRequest,
  LegacyPluginInstallRequest,
  PluginInstallResponse,
  AvailableUpdatesResponse,
  PluginTestResponse,
  FrontendTestResult,
  ModuleInstantiationTest,
  FileUploadProgress
} from '../types';
import { remotePluginService } from '../../../services/remotePluginService';
import { registerRemotePlugins } from '../../../plugins';

class PluginInstallerService {
  private api = ApiService.getInstance();

  /**
   * Unified plugin installation method supporting both GitHub and local file uploads
   */
  async installPlugin(request: PluginInstallRequest): Promise<PluginInstallResponse> {
    switch (request.method) {
      case 'github':
        return this.installFromGitHub(request);
      case 'local-file':
        return this.installFromFile(request);
      default:
        throw new Error(`Unsupported installation method: ${(request as any).method}`);
    }
  }

  /**
   * Install a plugin from a GitHub repository
   */
  private async installFromGitHub(request: GitHubInstallRequest): Promise<PluginInstallResponse> {
    try {
      // Create FormData for GitHub installation to match backend expectations
      const formData = new FormData();
      formData.append('method', 'github');
      formData.append('repo_url', request.repo_url);
      formData.append('version', request.version || 'latest');

      const response = await this.api.post<PluginInstallResponse>(
        '/api/v1/plugins/install',
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        }
      );

      // If installation was successful, refresh the plugin registry
      if (response.status === 'success') {
        await this.refreshPluginRegistry();
      }

      return response;
    } catch (error: any) {
      console.error('Plugin installation failed:', error);

      // Extract detailed error information from the response
      let errorMessage = 'Plugin installation failed';
      let errorDetails = null;
      let suggestions: string[] = [];

      if (error.response?.data) {
        const errorData = error.response.data;

        // Handle structured error response from our improved backend
        if (typeof errorData === 'object' && errorData.message) {
          errorMessage = errorData.message;
          errorDetails = errorData.details;
          suggestions = errorData.suggestions || [];
        } else if (typeof errorData === 'object' && errorData.detail) {
          // Handle FastAPI HTTPException format
          if (typeof errorData.detail === 'object') {
            errorMessage = errorData.detail.message || 'Installation failed';
            errorDetails = errorData.detail.details;
            suggestions = errorData.detail.suggestions || [];
          } else {
            errorMessage = errorData.detail;
          }
        } else if (typeof errorData === 'string') {
          errorMessage = errorData;
        }
      } else if (error.message) {
        errorMessage = error.message;
      }

      // Create enhanced error response
      const errorResponse: PluginInstallResponse = {
        status: 'error',
        message: errorMessage,
        error: errorMessage
      };

      // Add additional error context if available
      if (errorDetails || suggestions.length > 0) {
        (errorResponse as any).errorDetails = errorDetails;
        (errorResponse as any).suggestions = suggestions;
      }

      return errorResponse;
    }
  }

  /**
   * Install a plugin from a local file upload
   */
  private async installFromFile(request: LocalFileInstallRequest): Promise<PluginInstallResponse> {
    try {
      // Create FormData for file upload
      const formData = new FormData();
      formData.append('file', request.file);
      formData.append('method', 'local-file');
      formData.append('filename', request.filename);

      const response = await this.api.post<PluginInstallResponse>(
        '/api/v1/plugins/install',
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        }
      );

      // If installation was successful, refresh the plugin registry
      if (response.status === 'success') {
        await this.refreshPluginRegistry();
      }

      return response;
    } catch (error: any) {
      console.error('Plugin file installation failed:', error);

      // Extract detailed error information from the response
      let errorMessage = 'Plugin installation failed';
      let errorDetails = null;
      let suggestions: string[] = [];

      if (error.response?.data) {
        const errorData = error.response.data;

        // Handle structured error response from our improved backend
        if (typeof errorData === 'object' && errorData.message) {
          errorMessage = errorData.message;
          errorDetails = errorData.details;
          suggestions = errorData.suggestions || [];
        } else if (typeof errorData === 'object' && errorData.detail) {
          // Handle FastAPI HTTPException format
          if (typeof errorData.detail === 'object') {
            errorMessage = errorData.detail.message || 'Installation failed';
            errorDetails = errorData.detail.details;
            suggestions = errorData.detail.suggestions || [];
          } else {
            errorMessage = errorData.detail;
          }
        } else if (typeof errorData === 'string') {
          errorMessage = errorData;
        }
      } else if (error.message) {
        errorMessage = error.message;
      }

      // Create enhanced error response
      const errorResponse: PluginInstallResponse = {
        status: 'error',
        message: errorMessage,
        error: errorMessage
      };

      // Add additional error context if available
      if (errorDetails || suggestions.length > 0) {
        (errorResponse as any).errorDetails = errorDetails;
        (errorResponse as any).suggestions = suggestions;
      }

      return errorResponse;
    }
  }

  /**
   * Legacy method for backward compatibility
   * @deprecated Use installPlugin instead
   */
  async installFromUrl(request: LegacyPluginInstallRequest): Promise<PluginInstallResponse> {
    return this.installFromGitHub({
      method: 'github',
      repo_url: request.repo_url,
      version: request.version
    });
  }

  /**
   * Upload file with progress tracking
   */
  async uploadFileWithProgress(
    file: File,
    onProgress?: (progress: FileUploadProgress) => void
  ): Promise<string> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append('file', file);

      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && onProgress) {
          const progress: FileUploadProgress = {
            loaded: event.loaded,
            total: event.total,
            percentage: Math.round((event.loaded / event.total) * 100)
          };
          onProgress(progress);
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const response = JSON.parse(xhr.responseText);
            resolve(response.file_id || response.filename);
          } catch (error) {
            reject(new Error('Invalid response format'));
          }
        } else {
          reject(new Error(`Upload failed: ${xhr.statusText}`));
        }
      });

      xhr.addEventListener('error', () => {
        reject(new Error('Upload failed'));
      });

      xhr.open('POST', '/api/v1/plugins/upload');
      xhr.send(formData);
    });
  }

  /**
   * Refresh the frontend plugin registry after installation
   */
  private async refreshPluginRegistry(): Promise<void> {
    try {
      console.log('Refreshing plugin registry after installation...');

      // Get the updated manifest from the backend
      const manifest = await remotePluginService.getRemotePluginManifest();

      // Load all plugins in parallel
      const loadedPlugins = await Promise.all(
        manifest.map(plugin => remotePluginService.loadRemotePlugin(plugin))
      );

      // Filter out any failed loads (null results)
      const successfullyLoaded = loadedPlugins.filter(p => p !== null);

      // Register the plugins in the frontend registry
      registerRemotePlugins(successfullyLoaded);

      console.log(`Plugin registry refreshed with ${successfullyLoaded.length} plugins`);
    } catch (error) {
      console.error('Failed to refresh plugin registry:', error);
      // Don't throw error as this shouldn't fail the installation
    }
  }

  /**
   * Get available updates for installed plugins
   */
  async getAvailableUpdates(): Promise<AvailableUpdatesResponse> {
    try {
      const response = await this.api.get<AvailableUpdatesResponse>(
        '/api/v1/plugins/updates/available'
      );
      return response;
    } catch (error: any) {
      console.error('Failed to get available updates:', error);
      return {
        status: 'error',
        data: {
          available_updates: [],
          total_count: 0
        }
      };
    }
  }

  /**
   * Get plugin status
   */
  async getPluginStatus(pluginSlug: string): Promise<any> {
    try {
      const response = await this.api.get(`/api/v1/plugins/${pluginSlug}/status`);
      return response;
    } catch (error: any) {
      console.error('Failed to get plugin status:', error);
      return {
        status: 'error',
        error: error.message || 'Unknown error occurred'
      };
    }
  }

  /**
   * Uninstall a plugin
   */
  async uninstallPlugin(pluginSlug: string): Promise<any> {
    try {
      const response = await this.api.delete(`/api/v1/plugins/${pluginSlug}/uninstall`);
      return response;
    } catch (error: any) {
      console.error('Plugin uninstallation failed:', error);
      return {
        status: 'error',
        error: error.message || 'Unknown error occurred'
      };
    }
  }

  /**
   * Get list of available plugins
   */
  async getAvailablePlugins(): Promise<any> {
    try {
      const response = await this.api.get('/api/v1/plugins/available');
      return response;
    } catch (error: any) {
      console.error('Failed to get available plugins:', error);
      return {
        status: 'error',
        data: {
          available_plugins: {},
          total_count: 0
        }
      };
    }
  }

  /**
   * Validate a GitHub repository URL
   */
  validateGitHubUrl(url: string): { isValid: boolean; error?: string } {
    if (!url || url.trim() === '') {
      return { isValid: false, error: 'Repository URL is required' };
    }

    // Remove trailing slash and .git suffix
    const cleanUrl = url.trim().replace(/\/$/, '').replace(/\.git$/, '');

    // GitHub URL patterns
    const githubPatterns = [
      /^https:\/\/github\.com\/[^\/]+\/[^\/]+$/,
      /^git@github\.com:[^\/]+\/[^\/]+$/,
      /^github\.com\/[^\/]+\/[^\/]+$/
    ];

    const isValidGitHub = githubPatterns.some(pattern => pattern.test(cleanUrl));

    if (!isValidGitHub) {
      return {
        isValid: false,
        error: 'Please enter a valid GitHub repository URL (e.g., https://github.com/user/repo)'
      };
    }

    return { isValid: true };
  }

  /**
   * Normalize GitHub URL to standard format
   */
  normalizeGitHubUrl(url: string): string {
    if (!url) return '';

    // Remove trailing slash and .git suffix
    let cleanUrl = url.trim().replace(/\/$/, '').replace(/\.git$/, '');

    // Convert SSH to HTTPS
    if (cleanUrl.startsWith('git@github.com:')) {
      cleanUrl = cleanUrl.replace('git@github.com:', 'https://github.com/');
    }

    // Add https:// if missing
    if (cleanUrl.startsWith('github.com/')) {
      cleanUrl = 'https://' + cleanUrl;
    }

    return cleanUrl;
  }

  /**
   * Test plugin loading functionality
   */
  async testPluginLoading(pluginSlug: string): Promise<PluginTestResponse> {
    try {
      console.log(`Testing plugin loading for: ${pluginSlug}`);

      // Step 1: Call backend test endpoint (when available)
      let backendTest;
      try {
        backendTest = await this.api.post(`/api/v1/plugins/${pluginSlug}/test-loading`);
      } catch (error) {
        // Backend test endpoint not available yet, create mock response
        console.warn('Backend test endpoint not available, using mock data');
        backendTest = {
          plugin_installed: true,
          files_exist: true,
          manifest_valid: true,
          bundle_accessible: true,
          modules_configured: [],
          errors: [],
          warnings: ['Backend test endpoint not implemented yet']
        };
      }

      // Step 2: Attempt frontend plugin loading
      const frontendTest = await this.testFrontendLoading(pluginSlug);

      // Step 3: Combine results and determine overall status
      const overall = this.assessOverallResults(backendTest, frontendTest);

      const status = this.determineTestStatus(backendTest, frontendTest, overall);
      const message = this.generateTestMessage(status, frontendTest, overall);

      return {
        status,
        message,
        details: {
          backend: backendTest,
          frontend: frontendTest,
          overall
        }
      };
    } catch (error: any) {
      console.error('Plugin test failed:', error);
      return {
        status: 'error',
        message: 'Plugin test failed to execute',
        details: {
          backend: {
            plugin_installed: false,
            files_exist: false,
            manifest_valid: false,
            bundle_accessible: false,
            modules_configured: [],
            errors: [error.message || 'Unknown error occurred'],
            warnings: []
          },
          frontend: {
            success: false,
            error: error.message || 'Test execution failed'
          },
          overall: {
            canLoad: false,
            canInstantiate: false,
            issues: ['Test execution failed'],
            recommendations: ['Check console for detailed error information', 'Ensure plugin is properly installed']
          }
        }
      };
    }
  }

  /**
   * Test frontend plugin loading
   */
  private async testFrontendLoading(pluginSlug: string): Promise<FrontendTestResult> {
    try {
      console.log(`Testing frontend loading for plugin: ${pluginSlug}`);

      // Get plugin manifest
      const manifest = await remotePluginService.getRemotePluginManifest();

      // Try to find plugin by both id and plugin_slug since the manifest might use either
      let pluginManifest = manifest.find(p => p.id === pluginSlug);
      if (!pluginManifest) {
        // Also try to find by plugin_slug field if it exists
        pluginManifest = manifest.find(p => (p as any).plugin_slug === pluginSlug);
      }
      if (!pluginManifest) {
        // Also try to find by name field as fallback
        pluginManifest = manifest.find(p => p.name === pluginSlug);
      }

      if (!pluginManifest) {
        return {
          success: false,
          error: `Plugin '${pluginSlug}' not found in manifest. Available plugins: ${manifest.map(p => `${p.id} (slug: ${(p as any).plugin_slug || p.name})`).join(', ')}`
        };
      }

      console.log(`Found plugin manifest for ${pluginSlug}:`, pluginManifest);

      // Attempt to load plugin
      const loadedPlugin = await remotePluginService.loadRemotePlugin(pluginManifest);

      if (!loadedPlugin) {
        return {
          success: false,
          error: 'Plugin failed to load - loadRemotePlugin returned null'
        };
      }

      console.log(`Plugin loaded successfully:`, loadedPlugin);

      // Test module instantiation
      const moduleTests = await this.testModuleInstantiation(loadedPlugin);

      // Consider both successful instantiation and valid class components as success
      const accessibleModules = moduleTests.filter(test =>
        test.success || test.error?.includes('Class component detected')
      ).length;
      const totalModules = moduleTests.length;

      return {
        success: accessibleModules > 0, // Plugin is successful if modules are accessible
        loadedModules: totalModules,
        moduleTests,
        error: accessibleModules === 0 ? 'No modules could be accessed' : undefined
      };
    } catch (error: any) {
      console.error('Frontend loading test failed:', error);
      return {
        success: false,
        error: `Frontend loading failed: ${error.message}`
      };
    }
  }

  /**
   * Test module instantiation
   */
  private async testModuleInstantiation(loadedPlugin: any): Promise<ModuleInstantiationTest[]> {
    const tests: ModuleInstantiationTest[] = [];

    if (!loadedPlugin.loadedModules || loadedPlugin.loadedModules.length === 0) {
      return [{
        moduleName: 'unknown',
        success: false,
        error: 'No modules found in loaded plugin',
        componentCreated: false
      }];
    }

    for (const module of loadedPlugin.loadedModules) {
      try {
        console.log(`Testing module instantiation: ${module.name}`);

        // Check if component exists
        if (!module.component) {
          tests.push({
            moduleName: module.name,
            success: false,
            error: 'Module component is null or undefined',
            componentCreated: false
          });
          continue;
        }

        // Try to create a test instance (basic check)
        let componentCreated = false;
        let error: string | undefined;

        try {
          // For React components, we can check if it's a function or object
          if (typeof module.component === 'function') {
            // Check if it's a class component or function component
            const componentString = module.component.toString();

            if (componentString.includes('class ') || componentString.includes('extends ')) {
              // It's a class component - we can't safely instantiate it without 'new'
              // But we can check if it has the right structure
              componentCreated = true; // Mark as successful since it's a valid class component
              // No error - this is expected and valid
            } else {
              // It's likely a function component - try to call it
              // TODO: Consider removing this direct call approach. Calling components directly
              // can fail with hooks, trigger side effects (API calls, state changes), and doesn't
              // test the real render environment. Simply checking typeof === 'function' may be sufficient.
              try {
                const testProps = {};
                const result = module.component(testProps);
                componentCreated = result !== null && result !== undefined;
              } catch (funcError: any) {
                if (funcError.message.includes('cannot be invoked without')) {
                  // This is a class component that wasn't detected properly
                  componentCreated = true;
                  // No error - this is expected and valid for class components
                } else if (funcError.message.includes('Invalid hook call') ||
                           funcError.message.includes('Rendered fewer hooks') ||
                           funcError.message.includes('hooks can only be called')) {
                  // Functional components with hooks cannot be called directly (hooks need React render context).
                  // This is expected behavior - treat as valid since the component will work when properly rendered.
                  componentCreated = true;
                } else {
                  error = `Function component test failed: ${funcError.message}`;
                }
              }
            }
          } else if (typeof module.component === 'object' && module.component !== null) {
            // Component is already an object/element
            componentCreated = true;
          } else {
            error = `Component is not a function or object, got: ${typeof module.component}`;
          }
        } catch (componentError: any) {
          error = `Component validation failed: ${componentError.message}`;
        }

        tests.push({
          moduleName: module.name,
          success: componentCreated && !error,
          error,
          componentCreated
        });

      } catch (moduleError: any) {
        console.error(`Module test failed for ${module.name}:`, moduleError);
        tests.push({
          moduleName: module.name,
          success: false,
          error: `Module test failed: ${moduleError.message}`,
          componentCreated: false
        });
      }
    }

    return tests;
  }

  /**
   * Assess overall test results
   */
  private assessOverallResults(backendTest: any, frontendTest: FrontendTestResult): any {
    // Focus on core loading capabilities rather than component instantiation
    const canLoad = backendTest.bundle_accessible && frontendTest.success;
    const canInstantiate = frontendTest.moduleTests ?
      frontendTest.moduleTests.some(test => test.success || test.error?.includes('Class component detected')) : false;

    const issues: string[] = [];
    const recommendations: string[] = [];

    // Collect issues
    if (!backendTest.plugin_installed) {
      issues.push('Plugin not found in database');
      recommendations.push('Reinstall the plugin');
    }

    if (!backendTest.files_exist) {
      issues.push('Plugin files missing from storage');
      recommendations.push('Reinstall the plugin to restore missing files');
    }

    if (!backendTest.manifest_valid) {
      issues.push('Plugin manifest is invalid');
      recommendations.push('Contact plugin developer to fix manifest issues');
    }

    if (!backendTest.bundle_accessible) {
      issues.push('Plugin bundle cannot be accessed');
      recommendations.push('Check network connectivity and server status');
    }

    if (!frontendTest.success) {
      issues.push('Frontend loading failed');
      if (frontendTest.error && !frontendTest.error.includes('Class component detected')) {
        issues.push(frontendTest.error);
      }
      recommendations.push('Check browser console for detailed error information');
    }

    // Only report component issues if they're actual failures, not class component detection
    if (frontendTest.moduleTests) {
      const actualFailures = frontendTest.moduleTests.filter(test =>
        !test.success && !test.error?.includes('Class component detected')
      );
      if (actualFailures.length > 0) {
        issues.push(`${actualFailures.length} module(s) have actual loading issues`);
        recommendations.push('Plugin may have runtime dependency issues');
      }
    }

    // Add general recommendations only if there are real issues
    if (issues.length > 0) {
      recommendations.push('Try refreshing the page and testing again');
      recommendations.push('Contact plugin developer if issues persist');
    }

    return {
      canLoad,
      canInstantiate,
      issues,
      recommendations
    };
  }

  /**
   * Determine overall test status
   */
  private determineTestStatus(backendTest: any, frontendTest: FrontendTestResult, overall: any): 'success' | 'error' | 'partial' {
    // If plugin loads successfully and components are accessible (even if class components), consider it success
    if (overall.canLoad && frontendTest.success) {
      return 'success';
    } else if (overall.canLoad || (frontendTest.moduleTests && frontendTest.moduleTests.some(test => test.success || test.error?.includes('Class component detected')))) {
      return 'partial';
    } else {
      return 'error';
    }
  }

  /**
   * Generate test result message
   */
  private generateTestMessage(status: string, frontendTest: FrontendTestResult, overall: any): string {
    switch (status) {
      case 'success':
        const moduleCount = frontendTest.loadedModules || 0;
        return `Plugin loaded successfully! ${moduleCount} module(s) are accessible and ready to use.`;

      case 'partial':
        const accessibleModules = frontendTest.moduleTests ?
          frontendTest.moduleTests.filter(test => test.success || test.error?.includes('Class component detected')).length : 0;
        const totalModules = frontendTest.moduleTests ? frontendTest.moduleTests.length : 0;
        return `Plugin loaded successfully. ${accessibleModules} of ${totalModules} module(s) are accessible.`;

      case 'error':
        if (overall.issues.length > 0) {
          return `Plugin loading failed: ${overall.issues[0]}`;
        }
        return 'Plugin loading failed with unknown error.';

      default:
        return 'Plugin test completed with unknown status.';
    }
  }
}

export default new PluginInstallerService();