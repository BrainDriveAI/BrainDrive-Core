# Troubleshooting Guide

Quick solutions to common BrainDrive issues. Each section lists symptoms, causes, and resolutions.

## Installation Issues

### Port Already in Use

**Symptoms:**
- Backend fails to start with "Address already in use" error
- Frontend dev server won't start

**Causes:**
- Another process is using port 8005 (backend) or 5173 (frontend)
- Previous BrainDrive instance didn't shut down cleanly

**Resolution:**

```bash
# Find what's using the port (Linux/macOS)
lsof -i :8005
lsof -i :5173

# Kill the process
kill -9 <PID>

# Or use different ports via .env files
```

---

### Python Packages Fail to Install

**Symptoms:**
- `pip install -r requirements.txt` fails
- Import errors when starting backend

**Causes:**
- Virtual environment not activated
- Outdated pip version
- Missing system dependencies

**Resolution:**

```bash
# Ensure virtual environment is active
source .venv/bin/activate  # or: conda activate BrainDriveDev

# Upgrade pip first
pip install --upgrade pip

# Retry installation
pip install -r requirements.txt
```

For persistent issues, try creating a fresh environment:
```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

### Node Modules Issues

**Symptoms:**
- Frontend build fails
- Missing module errors
- Version conflicts

**Causes:**
- Corrupted node_modules
- Stale package-lock.json
- Node version mismatch

**Resolution:**

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
```

Check your Node version matches requirements (16.x or higher):
```bash
node --version
```

---

### Environment Variables Not Applied

**Symptoms:**
- Configuration changes don't take effect
- API URLs pointing to wrong locations

**Causes:**
- Server not restarted after `.env` changes
- Wrong `.env` file edited

**Resolution:**

1. Stop both backend and frontend servers
2. Verify you edited the correct `.env` file:
   - `backend/.env` for backend config
   - `frontend/.env` for frontend config
3. Restart both servers

---

## Plugin Issues

### Plugin Won't Install

**Symptoms:**
- Install button shows error
- Plugin doesn't appear in Plugin Manager after install

**Causes:**
- Invalid GitHub URL
- Repository missing required files
- Network/authentication issues

**Resolution:**

1. Verify the GitHub URL is correct and publicly accessible
2. Check the repository has:
   - `package.json` with correct metadata
   - `lifecycle_manager.py` for backend integration
   - Built `dist/remoteEntry.js` (or build instructions)
3. Check backend console for specific error messages
4. Try installing from a release tag: `https://github.com/org/repo/releases/tag/v1.0.0`

---

### Components Don't Appear in Page Builder

**Symptoms:**
- Plugin installed but components missing from Page Builder
- Empty component list for a plugin

**Causes:**
- Plugin not enabled
- Module Federation not loading
- Build output missing

**Resolution:**

1. **Verify plugin is enabled:** Plugin Manager → find plugin → ensure toggle is ON
2. **Check browser console** for Module Federation errors (F12 → Console)
3. **Verify build exists:** The plugin's `dist/remoteEntry.js` must exist
4. **Hard refresh:** Ctrl+Shift+R (Cmd+Shift+R on Mac)
5. **Check lifecycle_manager.py:** Ensure `module_data` defines components correctly

---

### Plugin Changes Not Showing

**Symptoms:**
- Code changes don't appear after rebuild
- Old version still loading

**Causes:**
- Browser cache serving stale bundle
- Build output going to wrong directory
- Dev server not watching files

**Resolution:**

1. **Disable browser cache:** DevTools → Network tab → check "Disable cache"
2. **Hard refresh:** Ctrl+Shift+R
3. **Verify build output path:** Check webpack.config.js `output.path` points to correct location
4. **Restart dev server:** Stop and restart `npm run dev`

---

### Service Bridge Errors

**Symptoms:**
- "Cannot read property of undefined" when calling services
- API calls failing from plugin components

**Causes:**
- Services accessed incorrectly
- Async calls not awaited
- Service method doesn't exist

**Resolution:**

**For functional components:**
```jsx
function MyComponent({ services }) {
  const handleClick = async () => {
    const result = await services.api.get('/endpoint');
  };
}
```

**For class components:**
```jsx
class MyComponent extends React.Component {
  handleClick = async () => {
    const result = await this.props.services.api.get('/endpoint');
  };
}
```

Check the [Service Bridges API Reference](/core/reference/service-bridges-api) for available methods.

---

## Development Issues

### Build Fails

**Symptoms:**
- Webpack errors during `npm run build`
- TypeScript compilation errors

**Causes:**
- Type mismatches
- Missing imports
- Invalid JSX syntax

**Resolution:**

1. Read the error message carefully - it usually points to the exact file and line
2. For TypeScript errors, check that all types are imported
3. For Module Federation errors, verify `exposes` config matches actual file paths
4. Run `npm run build` in verbose mode for more details

---

### Backend API Returns 401 Unauthorized

**Symptoms:**
- API calls return 401 status
- "Not authenticated" errors

**Causes:**
- JWT token expired or missing
- User not logged in
- Token not sent with request

**Resolution:**

1. **Log in again** to refresh your JWT token
2. **Check API Bridge usage:** Use `services.api` instead of raw `fetch` - it handles auth automatically
3. **Verify backend is running:** Check http://localhost:8005/docs loads

---

### Database Errors

**Symptoms:**
- Backend crashes with SQLite errors
- "Database is locked" messages

**Causes:**
- Corrupted database file
- Multiple processes accessing database
- Migration issues

**Resolution:**

For development, you can reset the database:
```bash
cd backend
rm braindrive.db  # or your configured database file
# Restart backend - it will create a fresh database
```

For "database locked" errors, ensure only one backend instance is running.

---

## Getting More Help

If these solutions don't resolve your issue:

1. **Search the forum:** [community.braindrive.ai](https://community.braindrive.ai) - someone may have solved it already
2. **Check GitHub Issues:** [BrainDrive-Core issues](https://github.com/BrainDriveAI/BrainDrive-Core/issues)
3. **Post for help:** Create a new topic with:
   - What you're trying to do
   - What's happening instead
   - Error messages (from browser console and/or backend terminal)
   - Your OS and Node/Python versions

The community is here to help.
