import { useState, useEffect, useCallback, useRef, useLayoutEffect } from 'react';
import { usePluginStudio } from '../../../hooks';
import { usePlugins } from '../../../hooks/plugin/usePlugins';
import { normalizeObjectKeys } from '../../../../../utils/caseConversion';

interface UseConfigDialogStateProps {
  open: boolean;
  selectedItem: any;
  currentPage: any;
}

/**
 * Custom hook to manage the state for the ConfigDialog component
 */
export const useConfigDialogState = ({ open, selectedItem, currentPage }: UseConfigDialogStateProps) => {
  const { availablePlugins, updatePage, viewMode, setCurrentPage } = usePluginStudio();
  const { getModuleById } = usePlugins();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [layoutConfig, setLayoutConfig] = useState<Record<string, any>>({});
  const [configMode, setConfigMode] = useState<Record<string, 'global' | 'layout'>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  
  // Get the current device type from the viewMode
  const currentDeviceType = viewMode?.type || 'desktop';
  
  // Find the selected module in the current page - using useMemo to prevent unnecessary recalculations
  const selectedModule = useRef<any>(null);
  selectedModule.current = (() => {
    if (!selectedItem || !currentPage?.modules) {
      return null;
    }
    
    // Try to find the module with the exact key first
    let module = currentPage.modules[selectedItem.i];
    
    // If not found, try with the underscore removed (to handle the format difference)
    if (!module) {
      const moduleKey = selectedItem.i.replace(/_/g, '');
      module = currentPage.modules[moduleKey];
    }
    
    // If still not found, try all modules and look for similar IDs
    if (!module && Object.keys(currentPage.modules).length > 0) {
      // Extract the moduleId part from the selectedItem.i (after the last underscore or dash)
      const parts = selectedItem.i.split(/[-_]/);
      const moduleIdPart = parts[parts.length - 2]; // The module ID is usually the second-to-last part
      
      // Look for a module with a similar ID
      const moduleKey = Object.keys(currentPage.modules).find(key =>
        key.includes(moduleIdPart)
      );
      
      if (moduleKey) {
        module = currentPage.modules[moduleKey];
      }
    }
    
    return module;
  })();
  
  // Find the plugin and module definition - using useMemo to prevent unnecessary recalculations
  const pluginDef = useRef<any>(null);
  pluginDef.current = (() => {
    if (!selectedModule.current) {
      return null;
    }
    
    const plugin = availablePlugins.find(p => p.id === selectedModule.current.pluginId);
    return plugin;
  })();

  const moduleDef = useRef<any>(null);
  moduleDef.current = (() => {
    if (!selectedModule.current) {
      return null;
    }
    
    // Use getModuleById from usePlugins hook to get the module definition
    const foundModule = getModuleById(
      selectedModule.current.pluginId, 
      selectedModule.current.moduleId || selectedModule.current.moduleName
    );
    
    return foundModule;
  })();
  
  // Get the config fields from the module definition - using useMemo to prevent unnecessary recalculations
  const configFields = useRef<any[]>([]);
  configFields.current = (() => {
    if (!moduleDef.current) {
      return [];
    }
    
    // Check for configFields, config_fields, or props
    let fields: Array<{
      name: string;
      label?: string;
      type?: string;
      default?: any;
      description?: string;
      required?: boolean;
      [key: string]: any;
    }> = [];
    
    if (moduleDef.current.configFields && Object.keys(moduleDef.current.configFields).length > 0) {
      // Convert object-style configFields to array-style
      fields = Object.entries(moduleDef.current.configFields).map(([name, field]) => ({
        name,
        ...(typeof field === 'object' ? field : { type: 'string', default: field })
      }));
    } else if ((moduleDef.current as any).config_fields && Object.keys((moduleDef.current as any).config_fields).length > 0) {
      // Convert object-style config_fields to array-style
      fields = Object.entries((moduleDef.current as any).config_fields).map(([name, field]) => ({
        name,
        ...(typeof field === 'object' ? field : { type: 'string', default: field })
      }));
    } else if (moduleDef.current.props && Object.keys(moduleDef.current.props).length > 0) {
      // Convert props to configFields format
      fields = Object.entries(moduleDef.current.props).map(([name, prop]) => ({
        name,
        label: name,
        type: (prop as any)?.type || 'string',
        default: (prop as any)?.default,
        description: (prop as any)?.description,
        required: (prop as any)?.required || false
      }));
    }
    
    return fields;
  })();
  
  // Initialize config when the dialog opens or the selected item changes
  // Using a ref to track if we've already initialized to prevent infinite loops
  const initializedRef = useRef(false);
  
  useEffect(() => {
    // Only initialize once when the dialog opens
    if (open && selectedModule.current && !initializedRef.current) {
      // Initialize with current config or defaults
      const initialConfig: Record<string, any> = {};
      const initialLayoutConfig: Record<string, any> = {};
      const initialConfigMode: Record<string, 'global' | 'layout'> = {};
      
      // Check if there are layout-specific overrides in the current layout
      let layoutOverrides: Record<string, any> = {};
      
      // First check if the selectedItem has configOverrides directly
      if (selectedItem && 'configOverrides' in selectedItem && selectedItem.configOverrides) {
        layoutOverrides = selectedItem.configOverrides;
      }
      // Then check if there are configOverrides in the current layout
      else if (currentPage?.layouts?.[currentDeviceType]) {
        const layoutItem = currentPage.layouts[currentDeviceType].find(
          (li: any) => li.i === selectedItem?.i || li.moduleUniqueId === selectedItem?.i
        );
        
        if (layoutItem && 'configOverrides' in layoutItem && layoutItem.configOverrides) {
          layoutOverrides = layoutItem.configOverrides;
        }
      }
      // Finally check if the module has layoutConfig for the current device type
      else if ((selectedModule.current as any).layoutConfig?.[currentDeviceType]) {
        layoutOverrides = (selectedModule.current as any).layoutConfig[currentDeviceType];
      }
      
      if (Array.isArray(configFields.current)) {
        configFields.current.forEach((field: any) => {
          const fieldName = field.name;
          
          // Check if this field has a layout-specific override
          const hasLayoutOverride =
            layoutOverrides[fieldName] !== undefined ||
            (selectedModule.current as any).layoutConfig?.[currentDeviceType]?.[fieldName] !== undefined;
          
          // Set the config mode
          initialConfigMode[fieldName] = hasLayoutOverride ? 'layout' : 'global';
          
          // Set the global config value
          const configValue = selectedModule.current.config?.[fieldName] !== undefined ?
            selectedModule.current.config[fieldName] :
            field.default;
          
          // Special handling for array values
          if (Array.isArray(configValue)) {
            initialConfig[fieldName] = [...configValue]; // Create a new array reference
          } else {
            initialConfig[fieldName] = configValue;
          }
            
          // Set the layout config value if it exists
          if (hasLayoutOverride) {
            let layoutValue;
            
            // Prioritize layoutOverrides from the layout item
            if (layoutOverrides[fieldName] !== undefined) {
              layoutValue = layoutOverrides[fieldName];
            }
            // Fall back to layoutConfig from the module
            else if ((selectedModule.current as any).layoutConfig?.[currentDeviceType]?.[fieldName] !== undefined) {
              layoutValue = (selectedModule.current as any).layoutConfig[currentDeviceType][fieldName];
            }
            
            // Special handling for array values in layout config
            if (Array.isArray(layoutValue)) {
              initialLayoutConfig[fieldName] = [...layoutValue]; // Create a new array reference
            } else if (layoutValue !== undefined) {
              initialLayoutConfig[fieldName] = layoutValue;
            }
          }
        });
      }
      
      // Normalize configs to ensure consistent camelCase property names
      const normalizedConfig = normalizeObjectKeys(initialConfig);
      const normalizedLayoutConfig = normalizeObjectKeys(initialLayoutConfig);
      
      setConfig(normalizedConfig);
      setLayoutConfig(normalizedLayoutConfig);
      setConfigMode(initialConfigMode);
      setErrors({});
      initializedRef.current = true;
    } else if (!open) {
      // Reset when dialog closes
      initializedRef.current = false;
    }
  }, [open, currentDeviceType, selectedItem, currentPage, availablePlugins]);
  
  // Track the currently focused field and selection position
  const focusedFieldRef = useRef<string | null>(null);
  const selectionStartRef = useRef<number | null>(null);
  const selectionEndRef = useRef<number | null>(null);
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  
  // Handle input focus
  const handleInputFocus = useCallback((fieldName: string) => {
    focusedFieldRef.current = fieldName;
  }, []);
  
  // Handle config field change
  const handleConfigChange = useCallback((fieldName: string, value: any, event?: React.ChangeEvent<HTMLInputElement>) => {
    // Save current selection position if available
    if (event && event.target) {
      selectionStartRef.current = event.target.selectionStart;
      selectionEndRef.current = event.target.selectionEnd;
    }
    
    // Save the field name that's being changed
    focusedFieldRef.current = fieldName;
    
    
    // Determine if this is a global or layout-specific config
    const isLayoutSpecific = configMode[fieldName] === 'layout';
    
    if (isLayoutSpecific) {
      // Update layout config
      setLayoutConfig(prev => {
        const newConfig = { ...prev };
        
        // Special handling for arrays to ensure they're properly stored
        if (Array.isArray(value)) {
          // Make a deep copy of the array to avoid reference issues
          newConfig[fieldName] = [...value];
        } else {
          newConfig[fieldName] = value;
        }
        
        return newConfig;
      });
    } else {
      // Update global config
      setConfig(prev => {
        const newConfig = { ...prev };
        
        // Special handling for arrays to ensure they're properly stored
        if (Array.isArray(value)) {
          // Make a deep copy of the array to avoid reference issues
          newConfig[fieldName] = [...value];
        } else {
          newConfig[fieldName] = value;
        }
        
        return newConfig;
      });
    }
    
    // Clear error for this field
    if (errors[fieldName]) {
      setErrors(prev => {
        const newErrors = { ...prev };
        delete newErrors[fieldName];
        return newErrors;
      });
    }
  }, [configMode, errors]);
  
  // Use layout effect to restore focus and selection after render
  useLayoutEffect(() => {
    // Only attempt to restore focus if we have a field that should be focused
    if (focusedFieldRef.current) {
      try {
        const inputElement = inputRefs.current[focusedFieldRef.current];
        
        // Make sure the element exists and has a focus method before trying to use it
        if (inputElement && typeof inputElement.focus === 'function') {
          inputElement.focus();
          
          // Restore selection position if available and element supports it
          if (selectionStartRef.current !== null &&
              selectionEndRef.current !== null &&
              typeof inputElement.setSelectionRange === 'function') {
            try {
              inputElement.setSelectionRange(selectionStartRef.current, selectionEndRef.current);
            } catch (selectionError) {
              // Silently handle selection errors
              // Silently handle selection errors
            }
          }
        }
      } catch (error) {
        // Silently handle any errors that might occur during focus or selection
        // Silently handle any errors that might occur during focus or selection
      }
    }
  }); // No dependency array, so it runs after every render to maintain focus during typing
  
  // Handle toggling between global and layout-specific config
  const handleConfigModeToggle = useCallback((fieldName: string) => {
    const currentMode = configMode[fieldName];
    const newMode = currentMode === 'global' ? 'layout' : 'global';
    
    // Update the config mode
    setConfigMode(prev => ({
      ...prev,
      [fieldName]: newMode
    }));
    
    // If switching from layout to global, remove the layout override
    if (newMode === 'global') {
      setLayoutConfig(prev => {
        const newConfig = { ...prev };
        delete newConfig[fieldName];
        return newConfig;
      });
    }
    // If switching from global to layout, add the layout override with the global value
    else {
      setLayoutConfig(prev => ({
        ...prev,
        [fieldName]: config[fieldName]
      }));
    }
  }, [configMode, config]);
  
  // Validate config
  const validateConfig = useCallback((): boolean => {
    const newErrors: Record<string, string> = {};
    
    if (!Array.isArray(configFields.current)) {
      return true;
    }
    
    configFields.current.forEach((field: any) => {
      const fieldName = field.name;
      const isLayoutSpecific = configMode[fieldName] === 'layout';
      const value = isLayoutSpecific ? layoutConfig[fieldName] : config[fieldName];
      
      // Check required fields
      if (field.required && (value === undefined || value === null || value === '')) {
        newErrors[fieldName] = 'This field is required';
      }
      // Check type validation
      if (value !== undefined && value !== null) {
        switch (field.type) {
          case 'number':
            if (typeof value !== 'number' && isNaN(Number(value))) {
              newErrors[fieldName] = 'Must be a number';
            }
            break;
          case 'boolean':
            if (typeof value !== 'boolean') {
              newErrors[fieldName] = 'Must be a boolean';
            }
            break;
          case 'array':
            if (!Array.isArray(value)) {
              // If not an array, try to convert it
              if (typeof value === 'string') {
                // Auto-convert string to array if needed
                const arrayValue = value
                  .split(',')
                  .map(item => item.trim())
                  .filter(item => item !== '');
                
                // Update the value in the config
                if (isLayoutSpecific) {
                  layoutConfig[fieldName] = arrayValue;
                } else {
                  config[fieldName] = arrayValue;
                }
              } else {
                newErrors[fieldName] = 'Must be an array';
              }
            }
            break;
          // Add more type validations as needed
        }
        
        // Special handling for arrays that don't have type 'array' explicitly set
        if (Array.isArray(value) && field.type !== 'array') {
          // It's an array but the field type isn't set to 'array'
          // This is fine, we'll handle it in the UI
        }
      }
      
      // Check min/max for numbers
      if (field.type === 'number' && value !== undefined && value !== null) {
        const numValue = Number(value);
        if (field.min !== undefined && numValue < field.min) {
          newErrors[fieldName] = `Must be at least ${field.min}`;
        }
        if (field.max !== undefined && numValue > field.max) {
          newErrors[fieldName] = `Must be at most ${field.max}`;
        }
      }
    });
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [configMode, config, layoutConfig]);
  
  // Handle save
  const handleSave = useCallback(async (onSuccess?: () => void) => {
    // Validate config and check if we have the necessary data
    if (!validateConfig() || !selectedItem || !currentPage) {
      return false;
    }
    
    try {
      // Set saving state
      setIsSaving(true);
      
      
      // Only proceed if we have a valid item and page
      if (selectedItem.i && currentPage.id) {
        // Create a deep copy of the modules and layouts to avoid mutation issues
        const newPage = {
          ...currentPage,
          modules: { ...currentPage.modules },
          layouts: { ...currentPage.layouts }
        };
        
        // Find the correct module key
        let moduleKey = selectedItem.i;
        if (!newPage.modules[moduleKey] && selectedModule.current) {
          // Find the key in the modules object that corresponds to this module
          const foundKey = Object.keys(newPage.modules).find(key =>
            newPage.modules[key] === selectedModule.current
          );
          if (foundKey) {
            moduleKey = foundKey;
          } else {
            // If still not found, try with the underscore removed
            const noUnderscoreKey = selectedItem.i.replace(/_/g, '');
            if (newPage.modules[noUnderscoreKey]) {
              moduleKey = noUnderscoreKey;
            }
          }
        }
        
        // Update the module config
        if (newPage.modules[moduleKey]) {
          newPage.modules[moduleKey] = {
            ...newPage.modules[moduleKey],
            config: { ...config }
          };
          
          // If we have layout-specific config, update the layout config
          if (Object.keys(layoutConfig).length > 0) {
            // Make sure the layoutConfig object exists
            if (!newPage.modules[moduleKey].layoutConfig) {
              newPage.modules[moduleKey].layoutConfig = {};
            }
            
            // Make sure the device type object exists
            if (!newPage.modules[moduleKey].layoutConfig[currentDeviceType]) {
              newPage.modules[moduleKey].layoutConfig[currentDeviceType] = {};
            }
            
            // Update the layout config
            newPage.modules[moduleKey].layoutConfig[currentDeviceType] = {
              ...newPage.modules[moduleKey].layoutConfig[currentDeviceType],
              ...layoutConfig
            };
          }
          
          // If we have layout-specific config, also update the layout item's configOverrides
          if (Object.keys(layoutConfig).length > 0 && newPage.layouts?.[currentDeviceType]) {
            // Find the layout item - try both the original key and the found key
            let layoutIndex = newPage.layouts[currentDeviceType].findIndex(
              (li: any) => li.i === selectedItem.i || li.moduleUniqueId === selectedItem.i
            );
            
            // If not found with the original key, try with the found key
            if (layoutIndex === -1 && moduleKey !== selectedItem.i) {
              layoutIndex = newPage.layouts[currentDeviceType].findIndex(
                (li: any) => li.i === moduleKey || li.moduleUniqueId === moduleKey
              );
            }
            
            if (layoutIndex !== -1) {
              // Create a new array to avoid mutation
              newPage.layouts[currentDeviceType] = [...newPage.layouts[currentDeviceType]];
              
              // Update the layout item
              newPage.layouts[currentDeviceType][layoutIndex] = {
                ...newPage.layouts[currentDeviceType][layoutIndex],
                configOverrides: { ...layoutConfig }
              };
              
            } else {
            }
          }
        } else {
          // If the module wasn't found with the key, create it
          
          // Create a new module entry using the selected module's data
          if (selectedModule.current) {
            newPage.modules[moduleKey] = {
              ...selectedModule.current,
              config: { ...config }
            };
          } else {
            // Cannot create module: no selected module data
          }
        }
        
        // Ensure all modules have the correct structure
        Object.keys(newPage.modules).forEach(moduleId => {
          if (!newPage.modules[moduleId].config) {
            newPage.modules[moduleId].config = {};
          }
        });
        
        
        // Update the page
        // Make sure we're passing the ID as a string, not an object
        const pageId = typeof currentPage.id === 'string' ? currentPage.id :
                      (currentPage.id && typeof currentPage.id === 'object' && 'id' in currentPage.id) ?
                      currentPage.id.id :
                      currentPage.route;
                      
        
        // Persist via updatePage with proper content envelope so backend stores modules/layouts
        await updatePage(pageId, {
          content: {
            layouts: newPage.layouts,
            modules: newPage.modules,
            canvas: newPage.canvas
          }
        });
        
        // Update the current page in the context with the same shape we persisted
        setCurrentPage({
          ...newPage,
          content: {
            ...(newPage.content || {}),
            layouts: newPage.layouts,
            modules: newPage.modules,
            canvas: newPage.canvas
          }
        });
        
        
        // Call the success callback if provided
        if (onSuccess) {
          onSuccess();
        }
        
        return true;
      }
      
      return false;
    } catch (error) {
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [validateConfig, selectedItem, currentPage, config, layoutConfig, currentDeviceType, updatePage, setCurrentPage]);

  return {
    config,
    layoutConfig,
    configMode,
    errors,
    isSaving,
    configFields: configFields.current,
    selectedModule: selectedModule.current,
    moduleDef: moduleDef.current,
    pluginDef: pluginDef.current,
    currentDeviceType,
    handleConfigChange,
    handleConfigModeToggle,
    handleSave,
    handleInputFocus,
    validateConfig,
    inputRefs
  };
};
