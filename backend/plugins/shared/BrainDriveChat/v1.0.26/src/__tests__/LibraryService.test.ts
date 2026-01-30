import { LibraryService } from '../services/libraryService';
import { ApiService } from '../types';

const mockApi = (): ApiService => ({
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  delete: jest.fn(),
});

describe('LibraryService', () => {
  test('fetchProjects calls correct API endpoint', async () => {
    const api = mockApi();
    (api.get as jest.Mock).mockResolvedValue({
      data: { success: true, projects: [{ slug: 'alpha', name: 'Alpha' }], count: 1 },
    });
    const service = new LibraryService(api);
    const projects = await service.fetchProjects('active');
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/plugin-api/braindrive-library/library/projects?lifecycle=active'
    );
    expect(projects).toHaveLength(1);
    expect(projects[0].slug).toBe('alpha');
  });

  test('fetchProjectContext calls correct API with slug', async () => {
    const api = mockApi();
    (api.get as jest.Mock).mockResolvedValue({
      data: {
        success: true,
        project: 'alpha',
        lifecycle: 'active',
        files: { 'AGENT.md': { content: '# Alpha', size: 7 } },
      },
    });
    const service = new LibraryService(api);
    const ctx = await service.fetchProjectContext('alpha', 'active');
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/plugin-api/braindrive-library/library/project/alpha/context?lifecycle=active'
    );
    expect(ctx.files['AGENT.md']).toBeDefined();
  });

  test('fetchProjects throws on API failure', async () => {
    const api = mockApi();
    (api.get as jest.Mock).mockResolvedValue({ status: 500 });
    const service = new LibraryService(api);
    await expect(service.fetchProjects('active')).rejects.toThrow('Failed to fetch library projects');
  });

  test('fetchProjectContext throws on API failure', async () => {
    const api = mockApi();
    (api.get as jest.Mock).mockResolvedValue({ status: 500 });
    const service = new LibraryService(api);
    await expect(service.fetchProjectContext('alpha')).rejects.toThrow('Failed to fetch project context');
  });

  test('throws if no API service', async () => {
    const service = new LibraryService(undefined);
    await expect(service.fetchProjects('active')).rejects.toThrow('API service not available');
  });
});
