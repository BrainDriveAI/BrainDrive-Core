# BrainDrive Frontend

The web interface for BrainDrive - your user-owned AI platform.

The frontend includes **PageBuilder**, a drag-and-drop interface for building AI-powered applications using [BrainDrive plugins](https://docs.braindrive.ai/plugins/intro). Create responsive, interactive pages without coding - from multi-model chat interfaces to AI-powered games and custom dashboards.

> **Note:** The frontend requires the [BrainDrive Backend](../backend/README.md) to be running. See the [Installation Guide](https://docs.braindrive.ai/core/getting-started/install) for complete setup instructions.

![BrainDrive PageBuilder](../images/Page-Builder.png)

## Features

- **Plugin Management**: Browse, install, and manage plugins
- **Visual Editor**: Drag-and-drop interface for arranging plugins on a canvas
- **Responsive Design**: Support for desktop, tablet, and mobile layouts
- **Page Management**: Create, edit, and organize pages
- **Route Management**: Define and manage navigation routes
- **Component Configuration**: Configure plugin properties through a user-friendly interface
- **Theme Support**: Light and dark mode with customizable themes
- **Authentication**: Secure user authentication and authorization
- **Real-time Preview**: Instantly preview changes as you build
- **JSON Export/Import**: Export and import configurations as JSON
- **Error Handling**: Robust error boundaries and error reporting
- **Service Architecture**: Modular service-based architecture for extensibility

## Tech Stack

- **[React](https://reactjs.org/)** (v18.3.1): A JavaScript library for building user interfaces
- **[TypeScript](https://www.typescriptlang.org/)**: Typed JavaScript for better developer experience
- **[Vite](https://vitejs.dev/)**: Next-generation frontend tooling for fast development and optimized builds
- **[Material UI](https://mui.com/)** (v5.14.4): React UI framework with Material Design components
- **[React Router](https://reactrouter.com/)** (v7.2.0): Declarative routing for React applications
- **[React Grid Layout](https://github.com/react-grid-layout/react-grid-layout)**: Draggable and resizable grid layout
- **[Axios](https://axios-http.com/)**: Promise-based HTTP client
- **[Zod](https://zod.dev/)**: TypeScript-first schema validation

## Installation

See the [Installation Guide](https://docs.braindrive.ai/core/getting-started/install) for complete setup instructions including both frontend and backend.

## Running the Application

### Development Mode

To run the frontend in development mode with hot-reload:

```bash
npm run dev
```

This will start the development server at http://localhost:5173 (or another port if 5173 is in use).

### Building for Production

```bash
npm run build
```

The built files will be in the `dist` directory.

### Preview Production Build

```bash
npm run preview
```

## Project Structure

- `src/`: Source code
  - `components/`: Reusable UI components
  - `contexts/`: React context providers
  - `features/`: Feature-specific code
    - `plugin-manager/`: Plugin management feature
    - `plugin-studio/`: PageBuilder editor feature
  - `hooks/`: Custom React hooks
  - `pages/`: Page components
  - `plugin/`: Plugin system code
  - `services/`: Service layer for API communication
  - `App.tsx`: Main application component
  - `main.tsx`: Application entry point
  - `routes.tsx`: Application routes

## Contributing

Interested in developing plugins or contributing to BrainDrive? See the [Plugin Developer Quickstart](https://docs.braindrive.ai/core/getting-started/plugin-developer-quickstart).

## Documentation

Full documentation is available at [docs.braindrive.ai](https://docs.braindrive.ai).

## Questions?

Post at [community.braindrive.ai](https://community.braindrive.ai). We're here to help build the future of user-owned AI together.

## License

Licensed under the [MIT License](../LICENSE). Your AI. Your Rules.
