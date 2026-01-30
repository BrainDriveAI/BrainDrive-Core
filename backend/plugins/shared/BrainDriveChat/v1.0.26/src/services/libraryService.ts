import { ApiService, ApiResponse } from '../types';

export interface LibraryProject {
  name: string;
  slug: string;
  lifecycle: string;
  path: string;
  has_agent_md: boolean;
  has_spec: boolean;
  has_build_plan: boolean;
  has_decisions: boolean;
}

export interface LibraryProjectContext {
  success: boolean;
  project: string;
  lifecycle: string;
  files: Record<string, { content: string; size: number }>;
}

export class LibraryService {
  private api: ApiService | undefined;

  constructor(api?: ApiService) {
    this.api = api;
  }

  async fetchProjects(lifecycle: string = 'active'): Promise<LibraryProject[]> {
    if (!this.api) throw new Error('API service not available');
    const response: ApiResponse = await this.api.get(
      `/api/v1/plugin-api/braindrive-library/library/projects?lifecycle=${encodeURIComponent(lifecycle)}`
    );
    if (response?.data?.success) {
      return response.data.projects || [];
    }
    if (response?.success) {
      return (response as any).projects || [];
    }
    throw new Error('Failed to fetch library projects');
  }

  async fetchProjectContext(slug: string, lifecycle: string = 'active'): Promise<LibraryProjectContext> {
    if (!this.api) throw new Error('API service not available');
    const response: ApiResponse = await this.api.get(
      `/api/v1/plugin-api/braindrive-library/library/project/${encodeURIComponent(slug)}/context?lifecycle=${encodeURIComponent(lifecycle)}`
    );
    if (response?.data?.success) {
      return response.data;
    }
    if (response?.success) {
      return response as any;
    }
    throw new Error('Failed to fetch project context');
  }
}
