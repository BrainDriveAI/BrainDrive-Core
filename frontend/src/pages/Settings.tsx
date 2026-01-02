import React, { useState, useEffect, useMemo } from "react";
import { config } from "../config";
import {
  Box,
  Paper,
  Typography,
  Divider,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Switch,
  FormControlLabel,
  TextField,
  Grid,
  MenuItem,
  Select,
  FormControl,
  InputLabel,
  Button,
  IconButton,
  Card,
  CardContent,
  CardHeader,
  CardActions,
  Tooltip,
  Alert,
  CircularProgress,
  Collapse,
} from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LanguageIcon from '@mui/icons-material/Language';
import StorageIcon from '@mui/icons-material/Storage';
import AddIcon from '@mui/icons-material/Add';
import SettingsIcon from '@mui/icons-material/Settings';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { useTheme } from '../contexts/ServiceContext';
import { getAllModules, getModuleById } from '../plugins';
import { DynamicModuleConfig } from '../types/index';
import { LegacyModuleAdapter } from '../features/unified-dynamic-page-renderer/adapters';
import { useApi } from '../contexts/ServiceContext';

// Interface for settings plugin with additional metadata
interface SettingsPlugin {
	pluginId: string;
	moduleId: string;
	moduleName: string;
	displayName: string;
	category: string;
	priority: number;
	settingName: string;
	isActive: boolean;
}

// Interface for settings data from the database
interface SettingsData {
	id: string;
	name: string;
	value: any;
	definition_id?: string;
	scope?: string;
	user_id?: string;
	page_id?: string;
	created_at?: string;
	updated_at?: string;
}

const Settings = () => {
	console.log("Settings component rendered");
	const themeService = useTheme();
	const apiService = useApi();
	const [apiEndpoint, setApiEndpoint] = useState(config.api.baseURL);
	const [language, setLanguage] = useState("en");

	// Settings plugins state
	const [categories, setCategories] = useState<string[]>([]);
	const [selectedCategory, setSelectedCategory] = useState<string>("");
	const [availablePlugins, setAvailablePlugins] = useState<SettingsPlugin[]>(
		[]
	);
	const [selectedPlugin, setSelectedPlugin] = useState<string>("");
	const [activePlugins, setActivePlugins] = useState<SettingsPlugin[]>([]);
	const [existingSettings, setExistingSettings] = useState<SettingsData[]>([]);
	const [isLoading, setIsLoading] = useState<boolean>(true);
	const [error, setError] = useState<string | null>(null);

	// Collapsed state per plugin panel
	const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
	const pluginKey = (p: SettingsPlugin) => `${p.pluginId}-${p.moduleId}`;
	const toggleCollapsed = (key: string) =>
		setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));

	// Handle theme change
	const handleThemeChange = () => {
		const newTheme =
			themeService.getCurrentTheme() === "light" ? "dark" : "light";
		themeService.setTheme(newTheme);
	};

	// Fetch all settings plugins (modules with "settings" tag)
	const fetchSettingsPlugins = () => {
		try {
		const allModules = getAllModules();
		
		// Filter modules with "settings" tag (case-insensitive)
		const settingsModules = allModules.filter(({ module }) => 
			module.tags?.some(tag => tag.toLowerCase() === 'settings')
		);
		
		// Extract categories and create settings plugins
		const pluginsMap = new Map<string, SettingsPlugin>();
		const categoriesSet = new Set<string>();
		
		settingsModules.forEach(({ pluginId, module }) => {
			// Find the settings name tag (any tag other than "settings")
			const settingNameTag = module.tags?.find(tag => 
			tag.toLowerCase() !== 'settings'
			);
			
			if (settingNameTag) {
			const category = module.category || 'General';
			categoriesSet.add(category);
			
			const settingsPlugin: SettingsPlugin = {
				pluginId,
				moduleId: module.id || module.name,
				moduleName: module.name,
				displayName: module.displayName || module.name,
				category,
				priority: module.priority || 0,
				settingName: settingNameTag,
				isActive: false, // Will be updated when we fetch existing settings
			};
			
			// Use settingName as key to ensure uniqueness
			pluginsMap.set(settingNameTag.toLowerCase(), settingsPlugin);
			}
		});
		
		// Convert to arrays
		const allCategories = Array.from(categoriesSet).sort();
		const allPlugins = Array.from(pluginsMap.values());
		
		// Set state
		setCategories(allCategories);
		setAvailablePlugins(allPlugins);
		
		// Set default category if available
		if (allCategories.length > 0 && !selectedCategory) {
			setSelectedCategory(allCategories[0]);
		}
		
		return allPlugins;
		} catch (error) {
		console.error('Error fetching settings plugins:', error);
		setError('Failed to load settings plugins');
		return [];
		}
	};

	// Fetch existing settings from the database
	const fetchExistingSettings = async () => {
		setIsLoading(true);
		setError(null);

		try {
			if (!apiService) {
				throw new Error("API service not available");
			}

			const response = await apiService.get("/api/v1/settings/instances", {
				params: {
					scope: "user",
					user_id: "current",
				},
			});

			let settingsData: SettingsData[] = [];

			if (Array.isArray(response)) {
				settingsData = response;
			} else if (response && typeof response === "object" && response.data) {
				settingsData = Array.isArray(response.data)
					? response.data
					: [response.data];
			}

			console.log("Fetched settings:", settingsData);
			setExistingSettings(settingsData);

			return settingsData;
		} catch (error) {
			console.error("Error fetching settings:", error);
			setError("Failed to load existing settings");
			return [];
		} finally {
			setIsLoading(false);
		}
	};

	// Update active plugins based on existing settings and available plugins
	const updateActivePlugins = (
		plugins: SettingsPlugin[],
		settings: SettingsData[]
	) => {
		// Create a map of setting name to plugin for quick lookup
		const pluginsBySettingName = new Map<string, SettingsPlugin>();
		plugins.forEach((plugin) => {
			// Log each plugin to debug
			console.log(
				`Available plugin: ${plugin.displayName}, category: ${plugin.category}, settingName: ${plugin.settingName}`
			);
			pluginsBySettingName.set(plugin.settingName.toLowerCase(), plugin);
		});

		// Mark plugins as active if they have a corresponding setting
		const activeByModuleId = new Map<string, SettingsPlugin>();

		settings.forEach((setting) => {
			console.log(
				`Checking setting: ${setting.name}, definition_id: ${setting.definition_id}`
			);

			// Try to match by both setting name and definition_id
			let plugin = pluginsBySettingName.get(setting.name.toLowerCase());

			if (!plugin && setting.definition_id) {
				// If not found by name, try to find by definition_id
				plugin = plugins.find(
					(p) =>
						p.settingName.toLowerCase() ===
							setting.definition_id!.toLowerCase() ||
						p.settingName
							.toLowerCase()
							.includes(setting.definition_id!.toLowerCase())
				);

				if (plugin) {
					console.log(
						`Found plugin by definition_id: ${setting.definition_id} -> ${plugin.displayName}`
					);
				}
			}

			if (plugin) {
				console.log(
					`Activating plugin: ${plugin.displayName} for setting: ${setting.name}`
				);
				activeByModuleId.set(plugin.moduleId, {
					...plugin,
					isActive: true,
				});
			} else {
				console.log(`No plugin found for setting: ${setting.name}`);

				// Special case for Ollama servers settings
				if (
					setting.definition_id &&
					setting.definition_id === "ollama_servers_settings"
				) {
					// Find any plugin in the LLM Servers category with Ollama in the name or tags
					const ollamaPlugin = plugins.find(
						(p) =>
							p.category === "LLM Servers" &&
							(p.displayName.includes("Ollama") ||
								(p.settingName &&
									p.settingName.toLowerCase().includes("ollama")))
					);

					if (ollamaPlugin) {
						console.log(
							`Found Ollama plugin by special case: ${ollamaPlugin.displayName}`
						);
						activeByModuleId.set(ollamaPlugin.moduleId, {
							...ollamaPlugin,
							isActive: true,
						});
					}
				}
			}
		});

		const active = Array.from(activeByModuleId.values());

		// Sort active plugins by priority (high to low) and then by name
		const sortedActive = [...active].sort((a, b) => {
			if (a.priority !== b.priority) {
				return b.priority - a.priority;
			}
			return a.displayName.localeCompare(b.displayName);
		});

		console.log(`Total active plugins: ${sortedActive.length}`);
		sortedActive.forEach((plugin) => {
			console.log(
				`Active plugin: ${plugin.displayName}, category: ${plugin.category}`
			);
		});

		setActivePlugins(sortedActive);
	};

	// Initialize data on component mount
	useEffect(() => {
		const initializeData = async () => {
			const plugins = await fetchSettingsPlugins();
			const settings = await fetchExistingSettings();
			updateActivePlugins(plugins, settings);
		};

		initializeData();
	}, []);

	// Effect to ensure plugins are properly displayed when category changes
	useEffect(() => {
		if (selectedCategory && activePlugins.length > 0) {
			// Force re-computation of filtered plugins when category changes
			const filtered = activePlugins.filter(
				(plugin) => plugin.category === selectedCategory
			);
			console.log(
				`Category ${selectedCategory} has ${filtered.length} active plugins`
			);

			// Special case for LLM Servers category - ensure Ollama plugin is loaded
			if (selectedCategory === "LLM Servers" && filtered.length === 0) {
				console.log(
					"LLM Servers category selected but no active plugins found, checking for Ollama plugin"
				);

				// Check if we have Ollama settings in the database
				const ollamaSettings = existingSettings.find(
					(s) => s.definition_id === "ollama_servers_settings"
				);

				if (ollamaSettings) {
					console.log(
						"Found Ollama settings in database, looking for matching plugin"
					);

					// Find Ollama plugin in available plugins
					const ollamaPlugin = availablePlugins.find(
						(p) =>
							p.category === "LLM Servers" &&
							(p.displayName.includes("Ollama") ||
								(p.settingName &&
									p.settingName.toLowerCase().includes("ollama")))
					);

					if (
						ollamaPlugin &&
						!activePlugins.some((p) => p.moduleId === ollamaPlugin.moduleId)
					) {
						console.log(
							`Found Ollama plugin (${ollamaPlugin.displayName}), activating it`
						);

						// Activate the Ollama plugin
						setActivePlugins((prev) => [
							...prev,
							{ ...ollamaPlugin, isActive: true },
						]);
					}
				}
			}
		}
	}, [selectedCategory, activePlugins, existingSettings, availablePlugins]);

	// Filter available plugins by selected category
	const filteredAvailablePlugins = useMemo(() => {
		if (!selectedCategory) return [];

		// Get plugins for the selected category that aren't already active
		const activePluginIds = new Set(
			activePlugins.map((p) => p.settingName.toLowerCase())
		);

		return availablePlugins.filter(
			(plugin) =>
				plugin.category === selectedCategory &&
				!activePluginIds.has(plugin.settingName.toLowerCase())
		);
	}, [availablePlugins, activePlugins, selectedCategory]);

	// Filter active plugins by selected category
	const filteredActivePlugins = useMemo(() => {
		if (!selectedCategory) return [];

		return activePlugins.filter(
			(plugin) => plugin.category === selectedCategory
		);
	}, [activePlugins, selectedCategory]);

	// Count of active plugins per category for display in Category dropdown
	const activeCountByCategory = useMemo(() => {
		const m: Record<string, number> = {};
		activePlugins.forEach((p) => {
			m[p.category] = (m[p.category] || 0) + 1;
		});
		return m;
	}, [activePlugins]);

	// Ensure default collapsed state: new plugin panels start collapsed
	useEffect(() => {
		setCollapsed((prev) => {
			const next: Record<string, boolean> = { ...prev };
			// Add defaults for new active plugins
			activePlugins.forEach((p) => {
				const key = pluginKey(p);
				if (next[key] === undefined) next[key] = true; // collapsed by default
			});
			// Clean up keys for plugins no longer active
			Object.keys(next).forEach((k) => {
				if (!activePlugins.some((p) => pluginKey(p) === k)) {
					delete next[k];
				}
			});
			return next;
		});
	}, [activePlugins]);

	// Add a plugin to the active list
	const handleAddPlugin = () => {
		if (!selectedPlugin) return;

		const pluginToAdd = availablePlugins.find(
			(p) => p.moduleId === selectedPlugin && p.category === selectedCategory
		);

		if (pluginToAdd) {
			const updatedActivePlugins = [
				...activePlugins,
				{ ...pluginToAdd, isActive: true },
			];

			// Sort by priority and name
			const sortedPlugins = [...updatedActivePlugins].sort((a, b) => {
				if (a.priority !== b.priority) {
					return b.priority - a.priority;
				}
				return a.displayName.localeCompare(b.displayName);
			});

			setActivePlugins(sortedPlugins);
			setSelectedPlugin("");
		}
	};

  return (
    <Box sx={{ width: '100%', p: 2 }}>
      <Typography variant="h4" gutterBottom>
        Settings
      </Typography>
      
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      
      {/* Category and Plugin Selection */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Grid container spacing={2} alignItems="center">
          {/* Category Dropdown */}
          <Grid item xs={12} sm={4}>
            <FormControl fullWidth>
              <InputLabel id="category-select-label">Category</InputLabel>
              <Select
                labelId="category-select-label"
                id="category-select"
                value={selectedCategory}
                label="Category"
                renderValue={(value) =>
                  `${value as string} (${activeCountByCategory[value as string] || 0})`
                }
                onChange={(e) => setSelectedCategory(e.target.value)}
                disabled={isLoading || categories.length === 0}
              >
                {categories.map((category) => (
                  <MenuItem key={category} value={category}>
                    {category} ({activeCountByCategory[category] || 0})
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          
          {/* Available Plugins Dropdown */}
          <Grid item xs={12} sm={6}>
            <FormControl fullWidth>
              <InputLabel id="plugin-select-label">Available Plugins</InputLabel>
              <Select
                labelId="plugin-select-label"
                id="plugin-select"
                value={selectedPlugin}
                label="Available Plugins"
                onChange={(e) => setSelectedPlugin(e.target.value)}
                disabled={isLoading || filteredAvailablePlugins.length === 0}
              >
                {filteredAvailablePlugins.map((plugin) => (
                  <MenuItem key={plugin.moduleId} value={plugin.moduleId}>
                    {plugin.displayName}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
          
          {/* Add Button */}
          <Grid item xs={12} sm={2}>
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={handleAddPlugin}
              disabled={isLoading || !selectedPlugin}
              fullWidth
            >
              Add
            </Button>
          </Grid>
        </Grid>
      </Paper>
      
      {/* Settings Plugins Grid */}
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Grid container spacing={3}>
          {filteredActivePlugins.length === 0 ? (
            <Grid item xs={12}>
              <Alert severity="info">
                No settings plugins available for this category. Select a plugin from the dropdown above to add it.
              </Alert>
            </Grid>
          ) : (
            filteredActivePlugins.map((plugin) => (
              <Grid item xs={12} sm={12} md={12} key={`${plugin.pluginId}-${plugin.moduleId}`}>
                <Card 
                  sx={{ 
                    width: '100%', 
                    display: 'flex', 
                    flexDirection: 'column',
                    position: 'relative'
                  }}
                >
                  <CardHeader
                    title={plugin.displayName}
                    subheader={`Priority: ${plugin.priority}`}
                    action={
                      <Tooltip title={collapsed[pluginKey(plugin)] ? 'Expand' : 'Collapse'}>
                        <IconButton
                          aria-label="toggle"
                          aria-expanded={!collapsed[pluginKey(plugin)]}
                          onClick={() => toggleCollapsed(pluginKey(plugin))}
                          size="small"
                        >
                          {collapsed[pluginKey(plugin)] ? (
                            <ExpandMoreIcon />
                          ) : (
                            <ExpandLessIcon />
                          )}
                        </IconButton>
                      </Tooltip>
                    }
                    avatar={<SettingsIcon />}
                  />
                  <Collapse in={!collapsed[pluginKey(plugin)]} timeout="auto" unmountOnExit>
                    <CardContent sx={{ flexGrow: 1, overflow: 'auto', minHeight: '200px' }}>
                      <LegacyModuleAdapter
                        pluginId={plugin.pluginId}
                        moduleId={plugin.moduleId}
                        moduleName={plugin.moduleName}
                        isLocal={false}
                        useUnifiedRenderer={true}
                        mode="published"
                        lazyLoading={true}
                        priority="normal"
                        enableMigrationWarnings={process.env.NODE_ENV === 'development'}
                        fallbackStrategy="on-error"
                        performanceMonitoring={process.env.NODE_ENV === 'development'}
                      />
                    </CardContent>
                  </Collapse>
                </Card>
              </Grid>
            ))
          )}
        </Grid>
      )}
    </Box>
  );
};

export default Settings;
