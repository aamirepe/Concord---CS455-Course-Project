# 🚀 Concord Workspace Masterplan: "Google Docs for Code"

This document is the **single source of truth** for the evolution of Concord. It describes the vision, the technical architecture, and tracks the progress of the transition from a "single-file snippet runner" to a "Collaborative Cloud Workspace."

---

## 🌟 The Vision
Concord should be a living, shared environment where a "Project" is more than just a collection of files—it's a collaborative space.
- **Independence**: 4 people can join a project; User A can work on `api.py` while User B works on `styles.css` without seeing each other's cursors.
- **Collaboration**: Two other people can work on `main.py` together—one writing the core logic, the other verifying and editing in real-time.
- **Shared Reality**: When anyone creates a new file or folder, it appears instantly for everyone else.
- **Professional Execution**: Running a file considers the entire project context (folders, imports, and dependencies).

---

## 🛠 Architectural Pillars

### 1. Hierarchical Workspaces
**From Flat Files $\rightarrow$ Project Folders.**
- **Database**: Move from a simple `File` list to a `Folder` $\rightarrow$ `File` hierarchy.
- **Storage**: Files are stored with relative paths (e.g., `src/utils/helpers.py`).
- **UI**: A recursive File Explorer (like VS Code) on the left sidebar.
- **Real-time Structure**: Using a **Workspace Event Channel** via WebSockets to broadcast `FILE_CREATED`, `FOLDER_DELETED`, or `FILE_MOVED` events so the sidebar updates for all users instantly.

### 2. Session Lifecycle & Ownership
**From "Join Only" $\rightarrow$ "Create & Own".**
- **Creation**: `POST /projects` API to start a new project.
- **Ownership (RBAC)**:
    - `OWNER`: Full project control (can delete, manage members).
    - `EDITOR`: Can create/edit files and execute code.
    - `VIEWER`: Read-only access.
- **Bootstrapping**: New projects start with default files (e.g., `main.py`, `README.md`) to avoid a blank state.

### 3. Workspace-Aware Execution
**From "Single File" $\rightarrow$ "Full Context".**
- **The "Context" Problem**: Currently, the worker only sees the active file. If that file has an `import`, it fails.
- **The Solution**:
    1. API sends the **entire project state** (all files and their paths) to the worker.
    2. Worker replicates the **entire folder structure** inside the Docker container's `/workspace`.
    3. Worker executes the entry-point file within this full context.

### 4. Team Presence & Coordination
**Knowing "Who is Where".**
- **Global Presence**: Indicators in the file tree showing which user is currently editing which file.
- **Multi-Track Flow**:
    - *Parallel Track*: Different users in different files $\rightarrow$ Separate CRDT rooms.
    - *Interleaved Track*: Multiple users in one file $\rightarrow$ Single CRDT room with Yjs merging.

---

## 🗺️ Implementation Roadmap & Progress Tracker

This section serves as the guide for any agent to know what is remaining.

### Phase 1: Project Foundation 🏗️
*Goal: Establish the ability to create and own projects.*
- [ ] **Rename/Refactor**: Rename `Session` $\rightarrow$ `Project` across DB and Code.
- [ ] **Project API**: Implement `POST /projects` (Creation + Ownership assignment).
- [ ] **RBAC Middleware**: Implement checks for `OWNER`/`EDITOR` roles on file modifications.
- [ ] **Bootstrapping**: Logic to create default starter files upon project creation.

### Phase 2: Hierarchical Workspace 📂
*Goal: Move from a flat list to a real file system.*
- [ ] **DB Migration**: Add `path` to `File` and create the `Folder` table.
- [ ] **Structural APIs**: Implement `create_folder`, `move_file`, `rename_file`.
- [ ] **Real-time Sidebar**: Build the WebSocket broadcast system for structural updates.
- [ ] **UI File Tree**: Implement the recursive File Explorer component.

### Phase 3: Workspace-Aware Execution ⚡
*Goal: Support multi-file codebases and imports.*
- [ ] **Payload Upgrade**: Update API to send all project files to the worker.
- [ ] **Worker Mounting**: Update `SandboxRunner` to recreate the directory structure.
- [ ] **Execution Verification**: Test multi-file imports (e.g., Python `import utils` from `utils.py`).

### Phase 4: Presence & Polishing ✨
*Goal: Make it feel like a living team environment.*
- [ ] **Global Presence**: Implement "User X is editing File Y" indicators in the sidebar.
- [ ] **Invitation System**: Signed invitation links for new members.
- [ ] **Project Settings**: UI to rename projects or manage members.
- [ ] **Final E2E Testing**: Load test with 4+ concurrent users.

---

## ✅ Completed Milestones
- [x] **C++ Execution Stage Separation**: Implemented Build $\rightarrow$ Run logic in `worker/app/worker.py`.
- [x] **Core CRDT Integration**: Per-file real-time editing is functional.
- [x] **Design Blueprinting**: Masterplan and detailed specs finalized.

---

## 🚩 Current Status
**Current Focus**: Entering **Phase 1 (Project Foundation)**.
**Immediate Next Task**: Implement the `POST /projects` API and the database migration to support Project Ownership.
