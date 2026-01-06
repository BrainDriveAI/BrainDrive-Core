# Common API Workflows

This guide demonstrates how to use BrainDrive's different API surfaces together to accomplish common tasks.

---

## Workflow: Creating and Using a Custom Setting

This workflow shows how a plugin can:
1.  Define a new setting for the user to configure.
2.  Read that setting's value within a frontend component.
3.  Use the setting to alter the component's behavior.

### Step 1: Define the Setting (`lifecycle_manager.py`)

First, we define the setting in the plugin's `lifecycle_manager.py`. This makes BrainDrive aware of the setting so it can be displayed on the settings page.

We will add a setting for an `API Key`.

```python
# plugins/my-plugin/lifecycle_manager.py

class LifecycleManager:
    # ... other required methods ...

    def get_settings_definitions(self) -> list:
        """
        Define plugin settings that appear in the Settings UI.
        """
        return [
            {
                "id": "my-plugin_api_key",
                "name": "My Plugin API Key",
                "description": "API key for the external service used by My Plugin.",
                "type": "string",
                "category": "integrations",
                "default_value": "",
                "allowed_scopes": ["user"],
                "tags": ["api-key", "my-plugin"]
            }
        ]

```
*See: [Plugin API Contracts](./plugin-api-contracts.md#optional-methods)*

After the plugin is installed, this definition will cause a new input field to appear in the BrainDrive settings interface.

### Step 2: Read the Setting in a Component (Service Bridges)

Next, the plugin's frontend component can read the value the user has entered for this setting. We use the `SettingsBridge` for this.

```typescript
// src/components/MyComponent.tsx
import React from 'react';

interface MyComponentProps {
  bridges: {
    settings: SettingsBridge;
    // ... other bridges
  };
  config: Record<string, any>;
}

const MyComponent: React.FC<MyComponentProps> = ({ bridges, config }) => {
  const [apiKey, setApiKey] = React.useState<string | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);

  React.useEffect(() => {
    async function fetchApiKey() {
      // Use the Settings Bridge to get the value saved by the user.
      const savedKey = await bridges.settings.getSetting<string>('my-plugin_api_key');
      setApiKey(savedKey);
      setIsLoading(false);
    }

    fetchApiKey();
  }, [bridges.settings]);

  if (isLoading) {
    return <div>Loading settings...</div>;
  }

  return (
    <div>
      <h2>My Plugin</h2>
      {apiKey ? (
        <p>API Key is configured.</p>
      ) : (
        <p>
          API Key is not set. Please go to the BrainDrive settings page to add it.
        </p>
      )}
    </div>
  );
};

export default MyComponent;
```
*See: [Service Bridges API](./service-bridges-api.md#settings-bridge)*

### Step 3: Make a Backend Request (Optional)

If your plugin needed to use this API key to make a call to an external service from the backend, it could define a custom API endpoint.

1.  **Define a custom route** in `lifecycle_manager.py` using the `get_routes()` method.
2.  **Implement the route's logic.** This logic would fetch the setting using the backend APIs for settings.
3.  **Call the custom endpoint** from your frontend component using the `APIBridge`.

This workflow demonstrates how the `Plugin API Contract` (defining settings) and the `Service Bridges` (reading settings in the UI) work together to create a seamless experience for both the developer and the end-user.
