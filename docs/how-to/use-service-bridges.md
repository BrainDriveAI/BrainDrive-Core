---
title: Service Bridges
---

# Service Bridges

When developing BrainDrive plugins, you don't interact with the core system directly. Instead, you use **service bridges**.

Service bridges are standardized interfaces that allow plugins to communicate with the BrainDrive backend, frontend, and user environment without creating tight coupling or requiring external dependencies.

## Why Service Bridges?

Service bridges provide a stable abstraction layer between your plugin and BrainDrive's core functionality. This architectural approach delivers three critical benefits:

1. **No plugin dependencies or conflicts** – Plugins remain decoupled from core code and each other
2. **No breaking changes when core updates** – As long as service contracts remain stable, internal changes won't break your plugin
3. **No intimate system knowledge needed** – Ready-made helpers for common tasks eliminate boilerplate code

The result? Service bridges are designed to **save you 90% of typical development time**.

## The Six Service Bridges

In React components, bridges are available through `this.props.services`. Backend lifecycle code can import the same service interfaces.

<table className="service-bridges-table">
  <colgroup>
    <col className="service-bridges-table__col--bridge" />
    <col className="service-bridges-table__col--purpose" />
    <col className="service-bridges-table__col--usage" />
    <col className="service-bridges-table__col--demo" />
  </colgroup>
  <thead>
    <tr>
      <th>Service Bridge</th>
      <th>Purpose</th>
      <th>Example Usage</th>
      <th>Working Demo</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>🔗 API Bridge</strong></td>
      <td>Call backend REST endpoints—including your own plugin's routes—without setting up an HTTP client manually.</td>
      <td><code>await services.api.get('/my_plugin/endpoint')</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-API-Service-Bridge-Example-Plugin">ServiceExample_API</a></td>
    </tr>
    <tr>
      <td><strong>⚡ Event Bridge</strong></td>
      <td>Send and listen for cross-plugin events to coordinate state or actions.</td>
      <td><code>services.event.emit('eventName', data)</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-Events-Service-Bridge-Example-Plugin">ServiceExample_Events</a></td>
    </tr>
    <tr>
      <td><strong>🎨 Theme Bridge</strong></td>
      <td>Access the current theme (light or dark) and subscribe to changes so components adapt their styles.</td>
      <td><code>const theme = services.theme.getCurrentTheme()</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-Theme-Service-Bridge-Example-Plugin">ServiceExample_Theme</a></td>
    </tr>
    <tr>
      <td><strong>⚙️ Settings Bridge</strong></td>
      <td>Read or update user preferences and plugin settings, with support for system and user-level scopes.</td>
      <td><code>services.settings.getSetting('myKey')</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-Settings-Service-Bridge-Example-Plugin">ServiceExample_Settings</a></td>
    </tr>
    <tr>
      <td><strong>📍 PageContext Bridge</strong></td>
      <td>Retrieve the current page or route information—page IDs, paths, parameters—for context-aware plugins.</td>
      <td><code>services.pageContext.getContext()</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-Page-Context-Service-Bridge-Example-Plugin">ServiceExample_PageContext</a></td>
    </tr>
    <tr>
      <td><strong>💾 PluginState Bridge</strong></td>
      <td>Store and retrieve persistent key-value data scoped to your plugin, perfect for lightweight caching.</td>
      <td><code>await services.pluginState.save(data)</code></td>
      <td><a href="https://github.com/BrainDriveAI/BrainDrive-Plugin-State-Service-Bridge-Example-Plugin">ServiceExample_PluginState</a></td>
    </tr>
  </tbody>
</table>

## Learn By Doing

Each service bridge has a working example with visual demonstrations and full documentation. This is the fastest way to master service bridges:

**The proven learning path:**

1. Follow the [plugin developer quick start](https://docs.braindrive.ai/core/PLUGIN_DEVELOPER_QUICKSTART) to get up and running
2. Install any service bridge example through BrainDrive's Plugin Manager
3. Add the demo modules to a page using drag & drop
4. Interact with the components to see the service in action
5. Review the developer guide and study the code patterns in the repository
6. Copy those patterns to your own plugins

**What you'll see in each demo:**

- **API Bridge** – Authentication, streaming, error handling
- **Event Bridge** – Real-time messaging between modules
- **Theme Bridge** – Automatic light/dark theme switching
- **Settings Bridge** – Multi-scope configuration management
- **PageContext Bridge** – Location-aware plugin behavior
- **PluginState Bridge** – Persistent data storage

Each example includes a comprehensive [developer guide](https://github.com/BrainDriveAI/BrainDrive-API-Service-Bridge-Example-Plugin/blob/main/DEVELOPER_GUIDE.md) with production-ready patterns you can use immediately.

## Best Practices

- **Lean on Bridges First:** Favor bridges over rolling your own networking or storage logic to stay aligned with the ecosystem
- **Emit Meaningful Events:** Use descriptive event names and document payloads so other plugins can subscribe effectively
- **Stay Theme-Aware:** Subscribe to theme updates via the Theme Bridge so your UI matches BrainDrive's light and dark modes
- **Respect User Preferences:** Read and write through the Settings Bridge to honor user expectations across sessions
- **Keep Cached Data Small:** The Plugin State Bridge is best for small amounts of data. For larger needs, expose backend endpoints via the API Bridge

## Next Steps

Start building by exploring the service bridge examples hands-on, or dive into the [Plugin Developer Quick Start](https://docs.braindrive.ai/core/PLUGIN_DEVELOPER_QUICKSTART) to scaffold your first plugin.

By leaning on service bridges, you can ship powerful, forward-compatible plugins with minimal overhead.

## Questions?

Post in the developer forum at [community.braindrive.ai](https://community.braindrive.ai/). We're here to help build the future of user-owned AI together.
