# BrainDrive Plugin Theming Guide

## For Plugin AI Coding Agent Developers

This guide explains how to properly implement dark/light mode theming in BrainDrive plugins to ensure compliance with the host system's theme switching.

---

## Quick Answer

### ❌ Can I use Tailwind CSS dark mode?

**NO** - Tailwind CSS is not installed in BrainDrive and the `dark:` variant will not work.

### ✅ What should I use instead?

**Use CSS Custom Properties (CSS Variables)** with the `.dark-theme` class selector. This is the proven, working pattern used by all existing BrainDrive plugins.

---

## How BrainDrive's Theme System Works

### 1. Theme Service Controls Everything

The host system uses a singleton `ThemeService` located at:
`frontend/src/services/themeService.ts`

**What it does:**
- Controls theme switching across the entire application
- Applies CSS classes to the DOM
- Notifies all listeners when theme changes
- Plugins don't control themes - they **react** to theme changes

### 2. DOM Changes on Theme Switch

When the user toggles dark mode, the ThemeService modifies the DOM:

```typescript
// Light mode (default)
<html>                    <!-- NO .dark class -->
<body>                    <!-- NO .dark-scrollbars class -->

// Dark mode
<html class="dark">       <!-- .dark class ADDED -->
<body class="dark-scrollbars">  <!-- .dark-scrollbars class ADDED -->
```

**Key Classes:**
- `.dark` → Applied to `document.documentElement` (`<html>`)
- `.dark-scrollbars` → Applied to `document.body`

---

## The Correct Way: CSS Custom Properties

### Step 1: Define Your Theme Variables

Create a CSS file for your plugin (e.g., `MyPlugin.css`):

```css
/* Light theme variables (default) */
:root {
  --my-plugin-bg: #ffffff;
  --my-plugin-text: #333333;
  --my-plugin-border: #e0e0e0;
  --my-plugin-card-bg: #f5f5f5;
  --my-plugin-shadow: rgba(0, 0, 0, 0.1);
  --my-plugin-hover: rgba(0, 0, 0, 0.05);
}

/* Dark theme overrides */
.dark-theme {
  --my-plugin-bg: #121a28;
  --my-plugin-text: #e0e0e0;
  --my-plugin-border: rgba(255, 255, 255, 0.1);
  --my-plugin-card-bg: #1e1e1e;
  --my-plugin-shadow: rgba(0, 0, 0, 0.3);
  --my-plugin-hover: rgba(255, 255, 255, 0.1);
}
```

### Step 2: Use Variables in Your Styles

```css
.my-plugin-container {
  background-color: var(--my-plugin-bg);
  color: var(--my-plugin-text);
  border: 1px solid var(--my-plugin-border);
  transition: background-color 0.3s ease, color 0.3s ease;
}

.my-plugin-card {
  background-color: var(--my-plugin-card-bg);
  box-shadow: 0 2px 4px var(--my-plugin-shadow);
}

.my-plugin-card:hover {
  background-color: var(--my-plugin-hover);
}
```

### Step 3: React Integration (Functional Component)

```typescript
import React, { useState, useEffect } from 'react';
import './MyPlugin.css';

interface MyPluginProps {
  services?: {
    theme?: {
      getCurrentTheme: () => 'light' | 'dark';
      addThemeChangeListener: (listener: (theme: string) => void) => void;
      removeThemeChangeListener: (listener: (theme: string) => void) => void;
    };
  };
}

const MyPlugin: React.FC<MyPluginProps> = ({ services }) => {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');

  useEffect(() => {
    // Get initial theme
    if (services?.theme) {
      const currentTheme = services.theme.getCurrentTheme();
      setTheme(currentTheme);

      // Listen for theme changes
      const handleThemeChange = (newTheme: string) => {
        setTheme(newTheme as 'light' | 'dark');
      };

      services.theme.addThemeChangeListener(handleThemeChange);

      // Cleanup listener on unmount
      return () => {
        services.theme.removeThemeChangeListener(handleThemeChange);
      };
    }
  }, [services]);

  return (
    <div className={`my-plugin-container ${theme === 'dark' ? 'dark-theme' : ''}`}>
      <h2>My Plugin</h2>
      <div className="my-plugin-card">
        This card adapts to light/dark theme automatically!
      </div>
    </div>
  );
};

export default MyPlugin;
```

---

## Why This Approach Works

### ✅ Advantages

1. **Host System Compliant** - Follows established patterns
2. **No Build Conflicts** - Works with BrainDrive's Material-UI setup
3. **Automatic Theme Sync** - Responds instantly to theme changes
4. **Smooth Transitions** - CSS transitions work perfectly
5. **Maintainable** - Clear separation of light/dark values
6. **Proven Pattern** - Used by all existing plugins

### ❌ Why Tailwind Won't Work

1. **Not Installed** - BrainDrive uses Material-UI with Emotion (CSS-in-JS)
2. **No Configuration** - No `tailwind.config.js` exists
3. **Wrong Selector** - Tailwind's `dark:` needs `class="dark"` on a parent
4. **Build Conflicts** - Would conflict with Material-UI's styling system
5. **Bundle Size** - Adds unnecessary bloat

---

## Alternative: React Hook Pattern

If you prefer a custom hook for theme integration:

```typescript
// useTheme.ts
import { useState, useEffect } from 'react';

interface ThemeService {
  getCurrentTheme: () => 'light' | 'dark';
  addThemeChangeListener: (listener: (theme: string) => void) => void;
  removeThemeChangeListener: (listener: (theme: string) => void) => void;
}

export const useTheme = (themeService?: ThemeService) => {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');

  useEffect(() => {
    if (!themeService) return;

    const currentTheme = themeService.getCurrentTheme();
    setTheme(currentTheme);

    const listener = (newTheme: string) => setTheme(newTheme as 'light' | 'dark');
    themeService.addThemeChangeListener(listener);

    return () => themeService.removeThemeChangeListener(listener);
  }, [themeService]);

  return theme;
};
```

**Usage:**
```typescript
const MyPlugin: React.FC<MyPluginProps> = ({ services }) => {
  const theme = useTheme(services?.theme);

  return (
    <div className={theme === 'dark' ? 'dark-theme' : ''}>
      {/* Your content */}
    </div>
  );
};
```

---

## Best Practices

### DO ✅

1. **Use CSS Custom Properties** for all theme-dependent colors
2. **Subscribe to theme changes** via `addThemeChangeListener`
3. **Apply `.dark-theme` class** to your plugin's root element
4. **Add smooth transitions** for better UX
5. **Test in both modes** before deployment
6. **Use semantic variable names** (e.g., `--text-primary` not `--color-1`)
7. **Namespace your variables** (e.g., `--my-plugin-bg` not `--bg`)

### DON'T ❌

1. **Don't use Tailwind CSS** - It's not supported
2. **Don't hardcode colors** - Always use variables
3. **Don't import Material-UI** in plugins - Creates conflicts
4. **Don't rely on `.dark` class** on `<html>` - Use `.dark-theme` on your component
5. **Don't forget to cleanup listeners** - Memory leaks!
6. **Don't use inline styles** for theme colors - Defeats the purpose

---

## Example: Complete Plugin with Theming

```typescript
// MyPlugin.tsx
import React, { useState, useEffect } from 'react';
import './MyPlugin.css';

interface MyPluginProps {
  services?: {
    theme?: {
      getCurrentTheme: () => 'light' | 'dark';
      addThemeChangeListener: (listener: (theme: string) => void) => void;
      removeThemeChangeListener: (listener: (theme: string) => void) => void;
    };
  };
}

const MyPlugin: React.FC<MyPluginProps> = ({ services }) => {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [data, setData] = useState<string[]>([]);

  useEffect(() => {
    // Theme integration
    if (services?.theme) {
      const currentTheme = services.theme.getCurrentTheme();
      setTheme(currentTheme);

      const handleThemeChange = (newTheme: string) => {
        setTheme(newTheme as 'light' | 'dark');
      };

      services.theme.addThemeChangeListener(handleThemeChange);
      return () => services.theme.removeThemeChangeListener(handleThemeChange);
    }
  }, [services]);

  return (
    <div className={`my-plugin ${theme === 'dark' ? 'dark-theme' : ''}`}>
      <div className="my-plugin-header">
        <h2>My Plugin</h2>
      </div>
      <div className="my-plugin-content">
        <div className="my-plugin-card">
          Current theme: {theme}
        </div>
      </div>
    </div>
  );
};

export default MyPlugin;
```

```css
/* MyPlugin.css */

/* Light theme (default) */
:root {
  --my-plugin-bg: #ffffff;
  --my-plugin-text: #333333;
  --my-plugin-border: #e0e0e0;
  --my-plugin-header-bg: #f5f5f5;
  --my-plugin-card-bg: #ffffff;
  --my-plugin-shadow: rgba(0, 0, 0, 0.1);
}

/* Dark theme */
.dark-theme {
  --my-plugin-bg: #121a28;
  --my-plugin-text: #e0e0e0;
  --my-plugin-border: rgba(255, 255, 255, 0.1);
  --my-plugin-header-bg: #1a2332;
  --my-plugin-card-bg: #1e1e1e;
  --my-plugin-shadow: rgba(0, 0, 0, 0.3);
}

/* Component styles using variables */
.my-plugin {
  background-color: var(--my-plugin-bg);
  color: var(--my-plugin-text);
  border: 1px solid var(--my-plugin-border);
  border-radius: 8px;
  padding: 16px;
  transition: background-color 0.3s ease, color 0.3s ease, border-color 0.3s ease;
}

.my-plugin-header {
  background-color: var(--my-plugin-header-bg);
  padding: 12px;
  border-radius: 6px;
  margin-bottom: 16px;
  transition: background-color 0.3s ease;
}

.my-plugin-header h2 {
  margin: 0;
  color: var(--my-plugin-text);
}

.my-plugin-card {
  background-color: var(--my-plugin-card-bg);
  border: 1px solid var(--my-plugin-border);
  border-radius: 6px;
  padding: 16px;
  box-shadow: 0 2px 4px var(--my-plugin-shadow);
  transition: all 0.3s ease;
}

.my-plugin-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 8px var(--my-plugin-shadow);
}
```

---

## Common Color Variables Pattern

Use this as a starting template:

```css
:root {
  /* Backgrounds */
  --plugin-bg-primary: #ffffff;
  --plugin-bg-secondary: #f5f5f5;
  --plugin-bg-tertiary: #e0e0e0;

  /* Text */
  --plugin-text-primary: #333333;
  --plugin-text-secondary: #666666;
  --plugin-text-muted: #999999;

  /* Borders */
  --plugin-border-light: rgba(0, 0, 0, 0.1);
  --plugin-border-medium: rgba(0, 0, 0, 0.2);
  --plugin-border-heavy: rgba(0, 0, 0, 0.3);

  /* Interactive */
  --plugin-hover-bg: rgba(0, 0, 0, 0.05);
  --plugin-active-bg: rgba(0, 0, 0, 0.1);

  /* Shadows */
  --plugin-shadow-sm: rgba(0, 0, 0, 0.1);
  --plugin-shadow-md: rgba(0, 0, 0, 0.15);
  --plugin-shadow-lg: rgba(0, 0, 0, 0.2);
}

.dark-theme {
  /* Backgrounds */
  --plugin-bg-primary: #121a28;
  --plugin-bg-secondary: #1e1e1e;
  --plugin-bg-tertiary: #2a2a2a;

  /* Text */
  --plugin-text-primary: #e0e0e0;
  --plugin-text-secondary: #b0b0b0;
  --plugin-text-muted: #808080;

  /* Borders */
  --plugin-border-light: rgba(255, 255, 255, 0.1);
  --plugin-border-medium: rgba(255, 255, 255, 0.2);
  --plugin-border-heavy: rgba(255, 255, 255, 0.3);

  /* Interactive */
  --plugin-hover-bg: rgba(255, 255, 255, 0.1);
  --plugin-active-bg: rgba(255, 255, 255, 0.15);

  /* Shadows */
  --plugin-shadow-sm: rgba(0, 0, 0, 0.3);
  --plugin-shadow-md: rgba(0, 0, 0, 0.4);
  --plugin-shadow-lg: rgba(0, 0, 0, 0.5);
}
```

---

## Testing Your Theme Implementation

### Checklist ✅

- [ ] Light mode displays correctly with good contrast
- [ ] Dark mode displays correctly with good contrast
- [ ] Theme switches instantly when user toggles
- [ ] No flash of unstyled content (FOUC)
- [ ] Smooth transitions between themes
- [ ] All text is readable in both modes
- [ ] Borders and shadows are visible in both modes
- [ ] Hover states work in both modes
- [ ] No console errors about theme service
- [ ] Theme persists across page reloads

---

## Reference Examples

**Working Plugins Using This Pattern:**

1. **BrainDriveChat v1.0.11**
   - File: `backend/plugins/shared/BrainDriveChat/v1.0.11/src/BrainDriveChat.css`
   - Pattern: CSS variables + class-based theming

2. **BrainDriveSettings v1.0.2**
   - File: `backend/plugins/shared/BrainDriveSettings/v1.0.2/src/ComponentTheme.tsx`
   - Pattern: React hook + theme service integration

3. **CollectionViewer v1.0.0** (New)
   - File: `backend/plugins/shared/CollectionViewer/v1.0.0/src/CollectionViewer.css`
   - Pattern: CSS variables with functional component

---

## Summary

### For Plugin AI Coding Agents:

**You MUST use CSS Custom Properties** with the `.dark-theme` class selector. This is not optional - it's the only supported method in BrainDrive.

**Do NOT attempt to:**
- Install Tailwind CSS
- Use Tailwind's `dark:` variant
- Configure PostCSS for Tailwind
- Import Material-UI components

**The host system controls theming:**
- You don't set themes
- You **react** to theme changes
- Subscribe to theme service events
- Apply `.dark-theme` class based on current theme

This ensures your plugins are fully compliant with BrainDrive's theme system and will work seamlessly with host-controlled theme switching.

---

## Need Help?

If you encounter issues:

1. Verify theme service is available: `console.log(services?.theme)`
2. Check if listener is being called: Add logs in theme change handler
3. Inspect DOM: Verify `.dark-theme` class is being applied
4. Check CSS specificity: Ensure variables are properly scoped
5. Review existing plugins for working examples

**File Locations:**
- Theme Service: `frontend/src/services/themeService.ts`
- Example CSS: `backend/plugins/shared/CollectionViewer/v1.0.0/src/CollectionViewer.css`
- Example Component: `backend/plugins/shared/CollectionViewer/v1.0.0/src/CollectionViewer.tsx`
