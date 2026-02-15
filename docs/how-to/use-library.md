# Use the Library in Chat

The BrainDrive Library connects chat with your local project files. It lets you read project context, create new projects, and write updates directly to your Library.

## Prerequisites

- BrainDrive is running locally.
- LIBRARY_PATH is configured (default: `~/BrainDrive-Library`).
- The **BrainDrive Library** backend plugin is installed and enabled.

## Install the Library Plugin

1. Open **Plugin Manager**.
2. Find **BrainDrive Library** in Backend Plugins.
3. Install and enable it.

Once enabled, its endpoints are available under `/api/v1/plugin-api/braindrive-library/...`.

## Select Library Scope in Chat

1. Open the chat.
2. Click the **+** menu.
3. Choose **Library**.
4. Select **All** or a specific project.

When a project is selected, a checkmark appears next to it, and the chat scope reflects the selection.

## Read Project Context

With a project selected, you can ask the assistant to summarize or reference files like `AGENT.md`, `spec.md`, and `build-plan.md`. The Library plugin aggregates these files for the assistant.

## Create a Project

Ask the assistant to create a new project (e.g., “Create a new active project called My App”). The Library plugin creates:

- `AGENT.md`
- `spec.md`
- `build-plan.md`
- `decisions.md`
- `notes.md`

## File Types and Safety

- Allowed file types: `.md`, `.txt`, `.json`, `.yaml`, `.yml`
- Delete operations require admin privileges
- Paths are validated to prevent traversal outside the Library root

## Troubleshooting

- If Library doesn’t appear in the **+** menu, ensure the plugin is installed and enabled.
- If no projects appear, verify `~/BrainDrive-Library/projects/active` exists.
- Check backend logs for “plugin-api” route loading errors.
