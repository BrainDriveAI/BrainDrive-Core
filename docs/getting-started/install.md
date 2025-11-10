# 🧠 BrainDrive Local Installation
 
> Works on Windows, macOS, and Linux. Follow the steps in order to configure BrainDrive for local development.
 
---
 
## 1. Before You Begin
 
- Keep two terminal windows or sessions available: one for the backend API, one for the frontend UI.
- A dedicated Conda environment keeps Python, Node.js, and Git versions consistent across platforms. If you prefer system-managed tools, ensure compatible versions are available globally.
 
### Required Software
 
| Tool | Suggested Version | Download | Verify Installation |
| --- | --- | --- | --- |
| Conda (Miniconda or Anaconda) | Latest, with Python 3.11 support | [Miniconda](https://docs.conda.io/en/latest/miniconda.html) · [Anaconda](https://www.anaconda.com/products/distribution) | `conda --version`
| Git | 2.40+ | [git-scm.com](https://git-scm.com/downloads) | `git --version`
| Node.js & npm | Node 18 LTS (or newer compatible with Vite 4) | [nodejs.org](https://nodejs.org/en/download/) | `node -v` · `npm -v`

> If Git or Node.js are missing on your system, the Conda environment in Step 2 installs both for you.
 
---
 
## 2. Prepare Your Workspace
 
### 2.1 Create the Conda Development Environment (Recommended)
 
```bash
conda create -n BrainDriveDev -c conda-forge python=3.11 nodejs git -y
conda activate BrainDriveDev
```
 
> Activate `BrainDriveDev` in every terminal each time you work on BrainDrive.
 
### 2.2 Clone the Repository
 
```bash
git clone https://github.com/BrainDriveAI/BrainDrive-Core.git
cd BrainDrive-Core
```
 
---
 
## 3. Backend Setup (Terminal 1)
 
### 3.1 Install Python Dependencies
 
```bash
cd backend
conda activate BrainDriveDev  # ensure the environment is active
pip install -r requirements.txt
```
 
### 3.2 Configure Backend Environment Variables
 
Create `backend/.env` using one of the following options:
 
**Option A: Copy the template**
 
```bash
cp .env-dev .env       # macOS/Linux
copy .env-dev .env     # Windows PowerShell or CMD
```
 
**Option B: Customize manually**
 
Open `backend/.env-dev` and copy values into a new `backend/.env`, adjusting secrets, database location, and feature flags as needed. Update `SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, and any provider credentials before deploying beyond local development.
 
### 3.3 Run the FastAPI Development Server
 
```bash
uvicorn main:app --reload --host localhost --port 8005
```
 
Leave this process running; it serves BrainDrive's API and documentation.
 
---
 
## 4. Frontend Setup (Terminal 2)
 
### 4.1 Install Node Dependencies
 
```bash
cd BrainDrive-Core/frontend
conda activate BrainDriveDev  # or ensure Node.js 18+ is active
npm install
```
 
### 4.2 Configure Frontend Environment Variables
 
Create `frontend/.env`:
 
**Option A: Copy the example file**
 
```bash
cp .env.example .env       # macOS/Linux
copy .env.example .env     # Windows PowerShell or CMD
```
 
**Option B: Customize manually**
 
Reference `frontend/.env.example` and tailor values. Ensure `VITE_API_URL` matches the backend host/port. Remove temporary auto-login credentials (`VITE_DEV_EMAIL`, `VITE_DEV_PASSWORD`) before sharing builds.
 
### 4.3 Run the Vite Development Server
 
```bash
npm run dev
```
 
Keep this process running to serve the BrainDrive UI.

---
 
## 5. Access and Verify
 
| Component | URL | What to Expect |
| --- | --- | --- |
| Backend API docs | [http://localhost:8005/docs](http://localhost:8005/docs) | FastAPI interactive documentation loads without errors. |
| Frontend UI | [http://localhost:5173](http://localhost:5173) | BrainDrive web app loads and can reach the backend. |
 
If the frontend cannot reach the backend, confirm both servers are running and that CORS settings in `backend/.env` include `http://localhost:5173`.
 
---
 
## 6. Restarting After a Break
 
1. Open two terminal windows.
2. In each window run `conda activate BrainDriveDev`.
3. Terminal 1: `cd backend` then `uvicorn main:app --reload --host localhost --port 8005`.
4. Terminal 2: `cd frontend` then `npm run dev`.
5. Visit [http://localhost:5173](http://localhost:5173).

---

## 7. Next Steps

* **Review** your BrainDrive's [Owners Manual](https://docs.braindrive.ai/core/OWNER_USER_GUIDE)
* **Use** the [plugin developer quickstart guide](https://docs.braindrive.ai/core/PLUGIN_DEVELOPER_QUICKSTART) to build your first plugin.
* **View** the [BrainDrive Roadmap](https://docs.braindrive.ai/core/ROADMAP)
* **Join** the [BrainDrive Community](https://community.braindrive.ai/)

---

## 8. Troubleshooting & Tips

* **Port already in use:** Stop any process occupying ports 8005 or 5173, or update the port in your `.env` files and start commands.
* **Python packages fail to install:** Make sure `BrainDriveDev` is active and run `pip install --upgrade pip`. For system Python, install dependencies inside a virtual environment.
* **Node modules issues:** Delete `frontend/node_modules` and `frontend/package-lock.json`, then rerun `npm install`.
* **Environment variable changes not applied:** Restart the corresponding server after edits to `.env` files.
* **Security reminder:** Example secrets in the provided templates are for local development only. Replace them with strong values before deploying BrainDrive anywhere public.
* **Directory awareness:** Always confirm you are in the correct directory (`backend` or `frontend`) before running commands. Different operating systems and terminals don’t always behave the same with relative paths, so being explicit with `cd` helps prevent confusion and errors.

---

## 9. Support

Visit the support forum a [community.braindrive.ai](https://community.braindrive.ai). 

We're here to build the future of user-owned AI together. 
