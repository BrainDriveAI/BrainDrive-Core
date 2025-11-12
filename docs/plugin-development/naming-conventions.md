# BrainDrive Plugin Naming Conventions

## For Plugin Developers: lifecycle_manager.py Configuration

This guide explains the **critical naming rules** you must follow in your plugin's `lifecycle_manager.py` file to avoid breaking your plugin installation and deletion.

---

## Critical Rule: Plugin Name ≠ Module Name

### ❌ WRONG - This Will Break Your Plugin

In your `lifecycle_manager.py`:

```python
class MyPluginLifecycleManager(BaseLifecycleManager):
    def __init__(self, plugins_base_dir: str = None):
        self.plugin_data = {
            "name": "CollectionViewer",
            "plugin_slug": "CollectionViewer",
            # ...
        }

        self.module_data = [
            {
                "name": "CollectionViewer",  # ❌ SAME AS PLUGIN NAME - BREAKS!
                "display_name": "Collection Viewer",
                # ...
            }
        ]
```

**Why this breaks:**
1. **Module Federation Conflict** - Webpack Module Federation uses the scope name to expose modules
2. **Database ID Collision** - Module IDs are constructed as `{user_id}_{plugin_slug}_{module_name}`
3. **Import Resolution** - The host system can't distinguish between plugin and module exports
4. **Deletion Fails** - Database constraints can't properly cascade deletes
5. **Plugin Won't Load** - Frontend module resolution fails

---

### ✅ CORRECT - Plugin and Module Have Different Names

In your `lifecycle_manager.py`:

```python
class MyPluginLifecycleManager(BaseLifecycleManager):
    def __init__(self, plugins_base_dir: str = None):
        self.plugin_data = {
            "name": "CollectionViewer",
            "plugin_slug": "CollectionViewer",
            "scope": "CollectionViewer",
            # ...
        }

        self.module_data = [
            {
                "name": "CollectionViewerModule",  # ✅ DIFFERENT FROM PLUGIN NAME
                "display_name": "Collection Viewer",  # User-friendly name
                # ...
            }
        ]
```

---

## Real-World Examples from lifecycle_manager.py

### Example 1: BrainDriveChatWithDocs

**File:** `backend/plugins/shared/BrainDriveChatWithDocs/v1.0.3/lifecycle_manager.py`

```python
class BrainDriveChatWithDocsLifecycleManager(BaseLifecycleManager):
    def __init__(self, plugins_base_dir: str = None):
        # Plugin configuration
        self.plugin_data = {
            "name": "BrainDriveChatWithDocs",
            "plugin_slug": "BrainDriveChatWithDocs",
            "scope": "BrainDriveChatWithDocs",
            # ...
        }

        # Module configuration
        self.module_data = [
            {
                "name": "BrainDriveChatWithDocsModule",  # ✅ Added "Module" suffix
                "display_name": "AI Chat With Docs Interface",
                # ...
            }
        ]
```

**IDs Created:**
- Plugin ID: `user123_BrainDriveChatWithDocs`
- Module ID: `user123_BrainDriveChatWithDocs_BrainDriveChatWithDocsModule`

---

### Example 2: CollectionViewer (Fixed)

**File:** `backend/plugins/shared/CollectionViewer/v1.0.0/lifecycle_manager.py`

```python
class CollectionViewerLifecycleManager(BaseLifecycleManager):
    def __init__(self, plugins_base_dir: str = None):
        # Plugin configuration
        self.plugin_data = {
            "name": "CollectionViewer",
            "plugin_slug": "CollectionViewer",
            "scope": "CollectionViewer",
            # ...
        }

        # Module configuration
        self.module_data = [
            {
                "name": "CollectionViewerModule",  # ✅ Added "Module" suffix
                "display_name": "Collection Viewer",
                # ...
            }
        ]
```

**IDs Created:**
- Plugin ID: `user123_CollectionViewer`
- Module ID: `user123_CollectionViewer_CollectionViewerModule`

---

## Recommended Naming Patterns for lifecycle_manager.py

### Pattern 1: Add "Module" Suffix (Recommended)

```python
# In your lifecycle_manager.py
self.plugin_data = {
    "name": "MyPlugin",
    "plugin_slug": "MyPlugin"
}

self.module_data = [
    {
        "name": "MyPluginModule",  # Just add "Module"
        "display_name": "My Plugin"
    }
]
```

**Advantages:**
- ✅ Clear relationship between plugin and module
- ✅ Easy to remember
- ✅ Consistent pattern across all plugins

---

### Pattern 2: Descriptive Module Name

```python
# In your lifecycle_manager.py
self.plugin_data = {
    "name": "DataAnalyzer",
    "plugin_slug": "DataAnalyzer"
}

self.module_data = [
    {
        "name": "DataAnalyzerInterface",  # or "DataAnalyzerViewer", "DataAnalyzerComponent"
        "display_name": "Data Analyzer"
    }
]
```

**Advantages:**
- ✅ More descriptive
- ✅ Better for plugins with multiple modules
- ✅ Clearer purpose

---

### Pattern 3: Multiple Modules

If your plugin has multiple modules, define them all in `lifecycle_manager.py`:

```python
# In your lifecycle_manager.py
self.plugin_data = {
    "name": "DocumentManager",
    "plugin_slug": "DocumentManager"
}

self.module_data = [
    {
        "name": "DocumentManagerUploader",
        "display_name": "Document Uploader"
    },
    {
        "name": "DocumentManagerViewer",
        "display_name": "Document Viewer"
    },
    {
        "name": "DocumentManagerSettings",
        "display_name": "Document Settings"
    }
]
```

**Advantages:**
- ✅ Perfect for plugins with multiple modules
- ✅ Each module has clear purpose
- ✅ Easy to identify in toolbox

---

## Understanding Database ID Construction

When you define these in `lifecycle_manager.py`, the system creates these IDs:

### Plugin ID Format

```python
f"{user_id}_{plugin_slug}"
```

**Example from lifecycle_manager.py:**
```python
self.plugin_data = {
    "plugin_slug": "CollectionViewer"
}

# Creates: user_abc123_CollectionViewer
```

### Module ID Format

```python
f"{user_id}_{plugin_slug}_{module_name}"
```

**Example from lifecycle_manager.py:**
```python
self.plugin_data = {
    "plugin_slug": "CollectionViewer"
}

self.module_data = [
    {
        "name": "CollectionViewerModule"
    }
]

# Creates: user_abc123_CollectionViewer_CollectionViewerModule
```

---

## Why This Matters in lifecycle_manager.py

### 1. Webpack Module Federation

Your `webpack.config.js` must match your `lifecycle_manager.py`:

```javascript
// webpack.config.js
const PLUGIN_NAME = "CollectionViewer";
const PLUGIN_MODULE_NAME = "CollectionViewerModule";  // Must match lifecycle_manager.py

new ModuleFederationPlugin({
  name: PLUGIN_NAME,  // Plugin scope - matches plugin_data.scope from lifecycle_manager.py
  exposes: {
    [`./` + PLUGIN_MODULE_NAME]: "./src/index"  // ✅ CRITICAL: Use module name, NOT plugin name
  }
})
```

```python
# lifecycle_manager.py
self.plugin_data = {
    "name": "CollectionViewer",
    "scope": "CollectionViewer"  # Must match webpack name
}

self.module_data = [
    {
        "name": "CollectionViewerModule"  # Must match webpack exposes path
    }
]
```

**Critical Rules:**
- Webpack `name` field = `plugin_data["scope"]` (plugin name)
- Webpack `exposes` path = `module_data["name"]` (module name)
- These **must be different** or Module Federation can't resolve exports correctly

---

### 2. Database Foreign Keys

The `lifecycle_manager.py` creates these database records:

```python
# In _create_database_records() method
plugin_id = f"{user_id}_{self.plugin_data['plugin_slug']}"
module_id = f"{user_id}_{self.plugin_data['plugin_slug']}_{module_data['name']}"
```

If `module_name == plugin_slug`, you get:
```python
module_id = "user_id_plugin_slug_plugin_slug"  # ❌ Confusing and breaks deletion!
```

---

### 3. Frontend Import Resolution

```typescript
// Host system loads your plugin based on lifecycle_manager.py config
const module = await window.CollectionViewer.get('./CollectionViewer');
```

If both have the same name, this creates ambiguity.

---

## Deletion Issues in lifecycle_manager.py

### Why Deletion Can Fail

**Problem:** If plugin and module have the same name in `lifecycle_manager.py`, the `_delete_database_records()` method may fail.

**Your lifecycle_manager.py delete method:**

```python
async def _delete_database_records(self, user_id: str, plugin_id: str, db: AsyncSession):
    # Delete modules first (foreign key constraint)
    module_delete_stmt = text("""
        DELETE FROM module
        WHERE plugin_id = :plugin_id AND user_id = :user_id
    """)
    await db.execute(module_delete_stmt, {'plugin_id': plugin_id, 'user_id': user_id})

    # Then delete plugin
    plugin_delete_stmt = text("""
        DELETE FROM plugin
        WHERE id = :plugin_id AND user_id = :user_id
    """)
    await db.execute(plugin_delete_stmt, {'plugin_id': plugin_id, 'user_id': user_id})

    await db.commit()
```

**Solution:** Ensure in your `lifecycle_manager.py` that module names are unique from plugin names.

---

## Complete lifecycle_manager.py Template

Here's a complete template for your `lifecycle_manager.py`:

```python
#!/usr/bin/env python3
"""
YourPlugin Plugin Lifecycle Manager
"""

import json
import datetime
from pathlib import Path
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import structlog

logger = structlog.get_logger()

from app.plugins.base_lifecycle_manager import BaseLifecycleManager


class YourPluginLifecycleManager(BaseLifecycleManager):
    """Lifecycle manager for YourPlugin"""

    def __init__(self, plugins_base_dir: str = None):
        """Initialize the lifecycle manager"""

        # ✅ Plugin configuration - use your plugin name
        self.plugin_data = {
            "name": "YourPlugin",                    # Plugin name
            "description": "Your plugin description",
            "version": "1.0.0",
            "type": "frontend",
            "icon": "your_icon",
            "category": "your_category",
            "official": False,
            "author": "Your Name",
            "compatibility": "1.0.0",
            "scope": "YourPlugin",                   # Must match webpack name
            "bundle_method": "webpack",
            "bundle_location": "dist/remoteEntry.js",
            "is_local": False,
            "long_description": "Detailed description",
            "plugin_slug": "YourPlugin",             # URL-safe identifier
            "source_type": "github",
            "source_url": "https://github.com/yourorg/your-plugin",
            "update_check_url": "https://github.com/yourorg/your-plugin/releases/latest",
            "last_update_check": None,
            "update_available": False,
            "latest_version": None,
            "installation_type": "remote",
            "permissions": ["api.access"]
        }

        # ✅ Module configuration - MUST be different from plugin name
        self.module_data = [
            {
                "name": "YourPluginModule",          # ✅ Add "Module" suffix
                "display_name": "Your Plugin",       # User-friendly name
                "description": "Module description",
                "icon": "your_icon",
                "category": "your_category",
                "priority": 1,
                "props": {},
                "config_fields": {},
                "messages": {},
                "required_services": {},
                "dependencies": [],
                "layout": {
                    "minWidth": 4,
                    "minHeight": 4,
                    "defaultWidth": 6,
                    "defaultHeight": 6
                },
                "tags": ["tag1", "tag2"]
            }
        ]

        # Initialize base class
        if plugins_base_dir:
            shared_path = Path(plugins_base_dir) / "shared" / self.plugin_data['plugin_slug'] / f"v{self.plugin_data['version']}"
        else:
            shared_path = Path(__file__).parent

        super().__init__(
            plugin_slug=self.plugin_data['plugin_slug'],
            version=self.plugin_data['version'],
            shared_storage_path=shared_path
        )

    # ... rest of your lifecycle manager implementation
```

---

## Checklist for Plugin Development

Before deploying your plugin, verify **BOTH** files are correctly configured:

### lifecycle_manager.py Checklist

- [ ] `self.plugin_data["name"]` is set to your plugin name
- [ ] `self.module_data[0]["name"]` is **different** from plugin name
- [ ] Module name ends with "Module" or has descriptive suffix
- [ ] `plugin_data["scope"]` matches webpack Module Federation name
- [ ] `plugin_data["plugin_slug"]` is unique across all plugins
- [ ] All module names are unique within the plugin
- [ ] `display_name` is user-friendly (can match plugin name)

### webpack.config.js Checklist

- [ ] `PLUGIN_NAME` constant is defined (matches `plugin_data["scope"]`)
- [ ] `PLUGIN_MODULE_NAME` constant is defined (matches `module_data["name"]`)
- [ ] `PLUGIN_MODULE_NAME` is **different** from `PLUGIN_NAME`
- [ ] `exposes` section uses `PLUGIN_MODULE_NAME`, NOT `PLUGIN_NAME`
- [ ] ModuleFederationPlugin `name` field uses `PLUGIN_NAME`

**Example:**
```javascript
const PLUGIN_NAME = "MyPlugin";              // Matches plugin_data["scope"]
const PLUGIN_MODULE_NAME = "MyPluginModule"; // Matches module_data["name"]

exposes: {
  [`./` + PLUGIN_MODULE_NAME]: "./src/index"  // ✅ Correct
  // NOT: [`./` + PLUGIN_NAME]: "./src/index" // ❌ Wrong
}
```

---

## Quick Reference for lifecycle_manager.py

| Field in lifecycle_manager.py | Purpose | Must Be Unique | Example |
|-------------------------------|---------|----------------|---------|
| `plugin_data["name"]` | Plugin identifier | Yes (globally) | `"CollectionViewer"` |
| `plugin_data["plugin_slug"]` | URL-safe identifier | Yes (globally) | `"CollectionViewer"` |
| `plugin_data["scope"]` | Module Federation scope | Yes (globally) | `"CollectionViewer"` |
| `module_data[0]["name"]` | Module identifier | Yes (within plugin) | `"CollectionViewerModule"` |
| `module_data[0]["display_name"]` | User-facing name | No | `"Collection Viewer"` |

---

## Common Mistakes in lifecycle_manager.py

### Mistake 1: Identical Names
```python
self.plugin_data = {"name": "MyPlugin"}
self.module_data = [{"name": "MyPlugin"}]  # ❌ BAD
```

### Mistake 2: Only Changing Case
```python
self.plugin_data = {"name": "MyPlugin"}
self.module_data = [{"name": "myplugin"}]  # ❌ STILL BAD
```

### Mistake 3: Only Adding Space
```python
self.plugin_data = {"name": "MyPlugin"}
self.module_data = [{"name": "My Plugin"}]  # ❌ STILL BAD
```

### Mistake 4: Wrong Field
```python
self.plugin_data = {"name": "MyPlugin"}
self.module_data = [{"display_name": "MyPluginModule"}]  # ❌ Wrong field!
# Should be: {"name": "MyPluginModule"}
```

---

## Testing Your lifecycle_manager.py

After updating your `lifecycle_manager.py`, test it:

```bash
# Navigate to your plugin directory
cd backend/plugins/shared/YourPlugin/v1.0.0

# Run the test script
python lifecycle_manager.py
```

Expected output:
```
YourPlugin Plugin Lifecycle Manager - Test Mode
==================================================
Plugin: YourPlugin
Version: 1.0.0
Slug: YourPlugin
Modules: 1
  - Your Plugin (YourPluginModule)
```

Verify that:
- ✅ Plugin name is correct
- ✅ Module name is **different** from plugin name
- ✅ No errors are displayed

---

## Summary

**Golden Rule for lifecycle_manager.py:**

Plugin name (`self.plugin_data["name"]`) and module name (`self.module_data[0]["name"]`) **must be different**.

**Recommended Pattern:**
```python
self.plugin_data = {
    "name": "PluginName",
    "plugin_slug": "PluginName"
}

self.module_data = [
    {
        "name": "PluginNameModule",  # Add "Module" suffix
        "display_name": "Plugin Name"
    }
]
```

**Why:** Prevents Module Federation conflicts, database ID collisions, import resolution failures, and deletion errors.

**Result:** Your plugin installs, loads, and deletes correctly! ✅
