# Plugin API Contracts

This document defines the technical API contracts for BrainDrive plugins. For conceptual overview and development workflow, see [How BrainDrive Plugins Work](/core/concepts/plugins).

## Table of Contents

- [Lifecycle Manager Interface](#lifecycle-manager-interface)
- [Plugin Manifest Format](#plugin-manifest-format)
- [Module Metadata Format](#module-metadata-format)
- [REST API Endpoints](#rest-api-endpoints)
- [Frontend Module Federation](#frontend-module-federation)

---

## Lifecycle Manager Interface

Every plugin can include a `lifecycle_manager.py` file that implements the plugin lifecycle. This Python class handles installation, removal, and configuration.

### Required Structure

```python
class LifecycleManager:
    """
    Plugin lifecycle manager implementing the BrainDrive plugin contract.
    """

    def __init__(self, plugin_id: str, plugin_path: str):
        """
        Initialize the lifecycle manager.

        Args:
            plugin_id: Unique identifier for this plugin instance
            plugin_path: Filesystem path to the plugin directory
        """
        self.plugin_id = plugin_id
        self.plugin_path = plugin_path

    async def install_plugin(self, user_id: str, db) -> dict:
        """
        Called when a user installs the plugin.

        Args:
            user_id: The ID of the user installing the plugin
            db: Database session for plugin setup

        Returns:
            dict with 'success' (bool) and optional 'message' (str)
        """
        # Initialize plugin resources, create tables, seed data
        return {"success": True, "message": "Plugin installed successfully"}

    async def delete_plugin(self, user_id: str, db) -> dict:
        """
        Called when a user uninstalls the plugin.

        Args:
            user_id: The ID of the user uninstalling
            db: Database session for cleanup

        Returns:
            dict with 'success' (bool) and optional 'message' (str)
        """
        # Clean up plugin resources, remove data
        return {"success": True, "message": "Plugin uninstalled successfully"}

    async def get_plugin_status(self, user_id: str, db) -> dict:
        """
        Check if the plugin is properly installed for a user.

        Args:
            user_id: The user to check
            db: Database session

        Returns:
            dict with 'installed' (bool) and optional metadata
        """
        return {"installed": True, "version": "1.0.0"}

    def get_plugin_info(self) -> dict:
        """
        Return plugin metadata for the Plugin Manager UI.

        Returns:
            Plugin metadata dictionary (see Plugin Manifest Format)
        """
        return {
            "name": "My Plugin",
            "slug": "my-plugin",
            "version": "1.0.0",
            "description": "What this plugin does",
            "author": "Your Name",
            "components": [...]
        }
```

### Optional Methods

```python
class LifecycleManager:
    # ... required methods ...

    async def repair_plugin(self, user_id: str, db) -> dict:
        """
        Repair/reinstall plugin without losing user data.

        Returns:
            dict with 'success' (bool) and optional 'message' (str)
        """
        return {"success": True}

    async def upgrade_plugin(self, user_id: str, db, from_version: str, to_version: str) -> dict:
        """
        Handle version upgrades with data migration.

        Args:
            from_version: Previous installed version
            to_version: New version being installed

        Returns:
            dict with 'success' (bool) and optional 'message' (str)
        """
        return {"success": True}

    def get_routes(self) -> list:
        """
        Register custom FastAPI routes for this plugin.

        Returns:
            List of FastAPI route definitions
        """
        return []

    def get_settings_definitions(self) -> list:
        """
        Define plugin settings that appear in the Settings UI.

        Returns:
            List of setting definition dictionaries
        """
        return []
```

---

## Plugin Manifest Format

The plugin manifest is returned by `get_plugin_info()` and defines how the plugin appears in BrainDrive.

### Full Schema

```json
{
  "name": "My Plugin",
  "slug": "my-plugin",
  "version": "1.0.0",
  "description": "Brief description of what this plugin does",
  "author": "Developer Name",
  "author_url": "https://github.com/developer",
  "repository": "https://github.com/org/plugin-repo",
  "license": "MIT",
  "icon": "puzzle-piece",
  "category": "ai | utility | integration | widget",
  "tags": ["ai", "chat", "productivity"],
  "min_braindrive_version": "1.0.0",
  "components": [
    {
      "id": "my-component",
      "name": "My Component",
      "description": "What this component does",
      "category": "widgets",
      "icon": "widget-icon",
      "default_config": {
        "title": "Default Title",
        "showHeader": true
      },
      "config_schema": {
        "type": "object",
        "properties": {
          "title": { "type": "string", "title": "Title" },
          "showHeader": { "type": "boolean", "title": "Show Header" }
        }
      }
    }
  ],
  "dependencies": {
    "plugins": ["other-plugin-slug"],
    "services": ["ollama", "openai"]
  },
  "permissions": ["api", "settings", "events", "state"]
}
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable plugin name |
| `slug` | string | URL-safe unique identifier (lowercase, hyphens) |
| `version` | string | Semantic version (e.g., "1.0.0") |
| `description` | string | Brief description for Plugin Manager |
| `components` | array | List of UI components exposed by this plugin |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `author` | string | Developer/organization name |
| `author_url` | string | Link to author's website or profile |
| `repository` | string | Source code repository URL |
| `license` | string | License identifier (e.g., "MIT") |
| `icon` | string | Icon name for Plugin Manager |
| `category` | string | Plugin category for filtering |
| `tags` | array | Tags for search/discovery |
| `min_braindrive_version` | string | Minimum compatible BrainDrive version |
| `dependencies` | object | Required plugins or services |
| `permissions` | array | Requested Service Bridge permissions |

---

## Module Metadata Format

Each component in the `components` array defines a draggable UI module.

### Component Schema

```json
{
  "id": "chat-interface",
  "name": "Chat Interface",
  "description": "Interactive AI chat component",
  "category": "ai",
  "icon": "message-square",
  "default_size": {
    "width": 400,
    "height": 600,
    "minWidth": 300,
    "minHeight": 400
  },
  "default_config": {
    "model": "default",
    "systemPrompt": "",
    "showHistory": true
  },
  "config_schema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "title": "AI Model",
        "enum": ["default", "gpt-4", "claude"],
        "default": "default"
      },
      "systemPrompt": {
        "type": "string",
        "title": "System Prompt",
        "format": "textarea"
      },
      "showHistory": {
        "type": "boolean",
        "title": "Show Chat History",
        "default": true
      }
    }
  },
  "events": {
    "emits": ["chat:message-sent", "chat:response-received"],
    "listens": ["user:settings-changed"]
  }
}
```

### Component Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique component identifier within this plugin |
| `name` | string | Display name in Page Builder |
| `description` | string | Tooltip/description for component |
| `category` | string | Category in Page Builder sidebar |
| `icon` | string | Icon name for Page Builder |
| `default_size` | object | Initial dimensions when dropped |
| `default_config` | object | Default configuration values |
| `config_schema` | object | JSON Schema for config panel |
| `events` | object | Events this component emits/listens to |

### Config Schema Format

The `config_schema` follows [JSON Schema](https://json-schema.org/) with BrainDrive extensions:

```json
{
  "type": "object",
  "properties": {
    "textField": {
      "type": "string",
      "title": "Text Field",
      "description": "Help text shown below field",
      "default": "default value"
    },
    "numberField": {
      "type": "number",
      "title": "Number Field",
      "minimum": 0,
      "maximum": 100,
      "default": 50
    },
    "selectField": {
      "type": "string",
      "title": "Select Field",
      "enum": ["option1", "option2", "option3"],
      "enumNames": ["Option 1", "Option 2", "Option 3"]
    },
    "booleanField": {
      "type": "boolean",
      "title": "Toggle",
      "default": false
    },
    "colorField": {
      "type": "string",
      "title": "Color",
      "format": "color"
    },
    "textareaField": {
      "type": "string",
      "title": "Long Text",
      "format": "textarea"
    }
  },
  "required": ["textField"]
}
```

---

## REST API Endpoints

The installation, removal, and management of plugins are handled by the core backend API. These endpoints are what the BrainDrive UI calls when you interact with the Plugin Manager.

For complete details on all plugin-related endpoints, see the [**Backend REST API Reference (&raquo; Plugins)**](./backend-api.md#plugins).

---

## Frontend Module Federation

Plugins use Webpack Module Federation to expose React components to BrainDrive at runtime.

### webpack.config.js Setup

```javascript
const { ModuleFederationPlugin } = require('webpack').container;

const PLUGIN_SCOPE = 'MyPlugin';
const MODULE_NAME = 'MyPluginModule';

module.exports = {
  // ... other config
  plugins: [
    new ModuleFederationPlugin({
      name: PLUGIN_SCOPE,  // Must match plugin_data.scope from lifecycle_manager.py
      filename: 'remoteEntry.js',
      exposes: {
        // Expose modules by their module name from lifecycle_manager.py
        [`./${MODULE_NAME}`]: './src/index',
      },
      shared: {
        react: { singleton: true, requiredVersion: '^18.0.0' },
        'react-dom': { singleton: true, requiredVersion: '^18.0.0' },
      },
    }),
  ],
};
```

### Component Export Format

Each exposed component receives Service Bridges via props:

```typescript
// src/components/MyComponent.tsx
import React from 'react';

interface MyComponentProps {
  // Service Bridges (injected by BrainDrive)
  bridges: {
    api: APIBridge;
    events: EventBridge;
    theme: ThemeBridge;
    settings: SettingsBridge;
    pageContext: PageContextBridge;
    pluginState: PluginStateBridge;
  };
  // Component configuration (from Page Builder)
  config: {
    title?: string;
    showHeader?: boolean;
    // ... matches config_schema
  };
  // Page context
  pageId: string;
  componentId: string;
}

const MyComponent: React.FC<MyComponentProps> = ({ bridges, config, pageId }) => {
  const [data, setData] = React.useState(null);

  React.useEffect(() => {
    // Load saved state
    bridges.pluginState.load().then(setData);
  }, []);

  return (
    <div>
      <h2>{config.title || 'My Component'}</h2>
      {/* Component content */}
    </div>
  );
};

export default MyComponent;
```

### Build Output

After running `npm run build`, your plugin should produce:

```
dist/
├── remoteEntry.js      # Module Federation entry point (required)
├── main.js             # Main bundle
├── [hash].js           # Chunk files
└── assets/             # Static assets (images, fonts)
```

The `remoteEntry.js` file is what BrainDrive loads to access your plugin's components.

---

## Related Documentation

- [How BrainDrive Plugins Work](/core/concepts/plugins) - Conceptual overview
- [Plugin Developer Quickstart](/core/getting-started/plugin-developer-quickstart) - Step-by-step guide
- [Service Bridges API](./service-bridges-api.md) - Frontend TypeScript interfaces
- [Backend REST API](./backend-api.md) - Complete endpoint reference
