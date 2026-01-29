# BrainDriveChat Plugin

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/BrainDriveAI/BrainDrive-Chat-Plugin)
[![License](https://img.shields.io/badge/License-MIT%20License-green.svg)](LICENSE)
[![BrainDrive](https://img.shields.io/badge/BrainDrive-Plugin-purple.svg)](https://github.com/BrainDriveAI/BrainDrive-Core)

The BrainDrive Chat Plugin is the default, modular chat experience for [BrainDrive](https://github.com/BrainDriveAI/BrainDrive-Core). 

It combines chat, model selection, personas and conversation history into a single, extensible UI you can customize, fork, and ship on your own terms. 

![BrainDrive chat interface](https://raw.githubusercontent.com/BrainDriveAI/BrainDrive-Core/94401c8adfed9df554b955adaee709adcd943a55/images/chat-interface.png)

Think **WordPress for AI**—[install BrainDrive-Core](https://docs.braindrive.ai/core/INSTALL), add this plugin, and you’re chatting with local or API models in minutes. 

**Your AI. Your Rules.**

## Features

- **Unified chat experience:** send prompts, stream responses, and browse conversation history in one place.  
- **Model selection:** pick from local or API models exposed by installed provider plugins (e.g., [Ollama Plugin](https://github.com/BrainDriveAI/BrainDrive-Ollama-Plugin), [OpenRouter Plugin](https://github.com/BrainDriveAI/BrainDrive-Openrouter-Plugin)).
- **Drop-in modularity:** add the chat module to any page via the **Page Builder** UI. No code required to compose experiences.  
- **Decoupled services:** interacts with BrainDrive through [Service Bridges](https://github.com/BrainDriveAI/BrainDrive-Core/blob/main/docs/how-to/use-service-bridges.md) for forward-compatibility.
- **1-minute dev cycle:** edit → build → refresh, powered by **Module Federation** and BrainDrive’s plugin system.

## Quick Start (2 paths)

### A) One-click install via Plugin Manager

1. Open **BrainDrive → Plugin Manager → Install Plugin**.

   ![BrainDrive plugin manager](https://raw.githubusercontent.com/BrainDriveAI/BrainDrive-Core/94401c8adfed9df554b955adaee709adcd943a55/images/plugin-manager.png)

2. Paste this repository URL and click **Install**.

   ![Installing BrainDrive plugin](https://raw.githubusercontent.com/BrainDriveAI/BrainDrive-Core/94401c8adfed9df554b955adaee709adcd943a55/images/installing-plugin.png)

3. Open **Page Builder**, drag the **BrainDriveChat** component onto a page, **Publish**, and start chatting.

   ![Building a BrainDrive page](https://raw.githubusercontent.com/BrainDriveAI/BrainDrive-Core/94401c8adfed9df554b955adaee709adcd943a55/images/building-a-page.png)

> The Plugin Manager fetches and registers plugins dynamically; no app rebuild required.

### B) Local development & hot-reload

> Use this path if you want to modify or contribute. It gives you a rapid edit→build→refresh cycle.

1. **Clone & install**
   ```bash
   git clone https://github.com/BrainDriveAI/BrainDrive-Chat-Plugin.git
   cd BrainDrive-Chat-Plugin
   npm install
   ```
2. **Point build output to BrainDrive (optional but fastest)**  
   In `webpack.config.js`, set `output.path` to your local BrainDrive plugins dir, e.g.:
   ```
   /path/to/BrainDrive-Core/backend/plugins/shared/BrainDriveChat/<version>/dist
   ```
   This lets BrainDrive load your freshly built `remoteEntry.js` without re-installing.

3. **Disable browser cache** in DevTools so the host app fetches the latest bundle on each refresh.

4. **Build (watch)**
   ```bash
   npm run dev   # or: npm run build, then refresh the BrainDrive page
   ```
   Edit code → build completes → refresh the BrainDrive page → changes appear.


## Usage

1. Add **BrainDriveChat** to any page via **Page Builder**.  
2. Choose a model (local or API) from the model selector (models come from installed provider plugins).  
3. Chat normally; your conversation history persists with BrainDrive storage.  

## Development Guide

### Prerequisites
- **Node.js 16+**  
- **Python 3.9+ (3.11 recommended)**  
- **Git**  
- BrainDrive [Installed](https://docs.braindrive.ai/core/INSTALL) & running locally (frontend & backend)

### Setup
```bash
git clone https://github.com/YourOrg/BrainDrive-Chat-Plugin.git
cd BrainDrive-Chat-Plugin
npm install
```

### Run in dev mode
```bash
npm run dev
```
- Keep **BrainDrive** running.  
- Refresh your BrainDrive page to load the new bundle. The host uses **Module Federation** to load the plugin at runtime.

### Build for production
```bash
npm run build
# outputs ./dist/remoteEntry.js for Module Federation
```
- Commit `dist/` in releases if you want frictionless installs via the Plugin Manager.

---

## Architecture Overview

### How it fits into BrainDrive
- **Core Frontend:** React + TypeScript + MUI  
- **Core Backend:** Python + FastAPI + SQLite  
- **Plugins:** Separate repos loaded at runtime via **Webpack Module Federation**; managed by a Python **Lifecycle Manager** that handles install/uninstall/repair/status with a universal API.

This chat plugin is a federated frontend module that talks to BrainDrive via **[Service Bridges](https://docs.braindrive.ai/core/how-to/use-service-bridges)**. You don’t call core internals directly; you call stable bridge contracts so updates don’t break your plugin.

### Components, hooks, and state
- **React components** render the header (model selector), history, and input areas.  
- **Hooks** manage chat messages, streaming, and side effects.  
- **State** (UI + conversation metadata) is persisted through BrainDrive services (e.g., Plugin State, Settings).

> The modular design keeps changes “surgical”: tweak the input behavior without touching model selection; swap a renderer without breaking history.

## Architecture

The plugin follows a modular design pattern:

- **BrainDriveChat.tsx**: Main component combining all functionality
- **types.ts**: TypeScript type definitions
- **utils.ts**: Utility functions and helpers
- **BrainDriveChat.css**: Tailwind-like CSS utilities and component styles

## Styling

The plugin uses a custom CSS framework that mimics Tailwind CSS utilities while being compatible with the BrainDrive environment. It includes:

- Responsive design utilities
- Light/dark theme support
- Flexbox and grid utilities
- Spacing and typography utilities

## API Integration

The plugin integrates with the BrainDrive API for:

- User authentication
- Model management
- Conversation storage
- AI provider communication

## Browser Support

- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

## License

[MIT License](LICENSE) **Your AI. Your Rules**

## Author

[BrainDrive.ai](https://www.braindrive.ai) Team

## Resources

## Resources

* [BrainDrive Docs Site](https://docs.braindrive.ai) - Learn how to use, modify,and build on your BrainDrive.
* [BrainDrive Community](https://community.braindrive.ai) - Get support and collaborate with us in building the future of the user-owned AI movement.
* [Contributing](https://docs.braindrive.ai/core/CONTRIBUTING) - We encourage and appreciate contributions, including improving this settings plugin and/or building your own.

We're on a mission to build a superior, user-owned alternative to Big Tech AI systems. 

Thank you for joining us on this journey away from Big Tech extraction and towards individual freedom and empowerment. 




