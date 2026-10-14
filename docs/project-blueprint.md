# 📘 Implementation Blueprint: Concord Project Workspace Evolution

## 1. Project Overview
**Concord** is a collaborative code execution platform. Currently, it allows users to join a "session" and edit a single file using CRDTs (Yjs/pycrdt).

**The Goal**: Transform Concord from a "single-file snippet runner" into a **Collaborative Cloud Workspace**. A "Project" should feel like a shared living environment where multiple users can:
1.  **Work Independently**: User A edits `file_1.py` while User B edits `file_2.py`.
2.  **Work Collaboratively**: User C and User D edit `main.py` simultaneously in real-time.
3.  **Shared Structure**: Any file or folder created by one user is immediately visible to all other project members.
4.  **Contextual Execution**: Running a file should consider the entire project's files and folders (allowing imports and multi-file architectures).

---

## 2. Core Requirements (The "Zero-Context" Spec)

### R1: Project Lifecycle & RBAC
Users must be able to create and manage "Projects" (formerly Sessions).
- **Creation**: A user can create a new project with a name. The creator is automatically assigned the `OWNER` role.
- **Membership**: Projects have members with roles:
    - `OWNER`: Full control (delete project, manage members, edit files).
    - `EDITOR`: Can create/edit/delete files and run code.
    - `VIEWER`: Can see the file tree and code but cannot modify anything.
- **Invitation**: Users can join a project via a unique project ID or an invitation link.

### R2: Hierarchical Shared Workspace
The "Flat File" list must be replaced by a "Project File Tree."
- **Folder Support**: Ability to create nested folders.
- **Path-based Storage**: Files are stored with a relative path (e.g., `src/utils/db.py`) rather than just a name.
- **Real-time Structural Updates**: When a user creates, renames, or deletes a file/folder, all other active members of the project must see that change in their sidebar **instantly** without refreshing.

### R3: Multi-Track Collaboration
The system must support simultaneous activity across different files.
- **Per-File Rooms**: Each file in a project has its own unique CRDT synchronization room.
- **Seamless Switching**: A user can switch between files instantly; the system must attach/detach the user from the corresponding CRDT room.
- **Concurrent Editing**: Two users in the same file must experience seamless, conflict-free merging of their keystrokes.

### R4: Workspace-Aware Execution
Execution must move from "Single File" $\rightarrow$ "Project Context."
- **Full Mount**: When a user runs a file, the worker must fetch **every file** in that project.
- **Structure Replication**: The worker must recreate the project's folder hierarchy inside the Docker container's `/workspace` directory.
- **Dependency Resolution**: Compiled languages (C++) and interpreted languages (Python/Node) must be able to resolve imports/includes from other files in the project.

### R5: Global Project Presence
Users need to know who is working on what.
- **Presence Indicators**: In the file tree, an avatar or indicator should appear next to a file if another user is currently editing it.
- **User List**: A global list of "Who's Online" in the current project.

---

## 3. Technical Implementation Detail

### A. Database Schema Changes
| Table | Change | Purpose |
| :--- | :--- | :--- |
| `CollabSession` $\rightarrow$ `Project` | Rename table/model | Conceptual shift to "Projects" |
| `File` | Add `path` (String), `folder_id` (FK) | Support hierarchical structure |
| `Folder` | **New Table**: `id`, `project_id`, `parent_id`, `name` | Enable nested directories |
| `SessionMember` | Add `role` (Enum: OWNER, EDITOR, VIEWER) | Implement RBAC |

### B. WebSocket Event Protocol
To ensure the "Real-time Project" feel, we introduce a **Workspace Event Channel**:

| Event Type | Payload | Trigger |
| :--- | :--- | :--- |
| `WS_FILE_CREATED` | `{ file_id, path, name }` | User creates a new file |
| `WS_FILE_DELETED` | `{ file_id }` | User deletes a file |
| `WS_FOLDER_CREATED`| `{ folder_id, path, name }` | User creates a folder |
| `WS_FILE_MOVED` | `{ file_id, old_path, new_path }`| User drags file to new folder |
| `WS_PRESENCE_UPDATE`| `{ user_id, file_id, status }` | User opens/closes a file |

### C. Worker Execution Logic (Refactored)
The `_run_one` logic in `worker/app/worker.py` is updated:
1.  **Payload Upgrade**: The API now sends a list of all files in the project: `[{path: "...", content: "..."}, ...]`.
2.  **Sandbox Prep**:
    ```python
    for file in workspace_files:
        full_path = os.path.join(tmpdir, file.path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(file.content)
    ```
3.  **Run**: Execute the target "entry point" file. The Docker container now contains the full project state.

### D. Frontend Architecture
- **State Management**: Store the project file tree in a global state (e.g., Zustand or Redux).
- **Recursive Component**: Implement a `FileTreeItem` component that renders itself if it's a folder.
- **Tabbed Editor**: Allow multiple files to be open. Each tab manages its own `Y.Doc` connection.

---

## 4. Step-by-Step Execution Plan

### Phase 1: Project Foundation
- [ ] Rename `Session` to `Project` in DB and Code.
- [ ] Implement `POST /projects` (Creation + Ownership).
- [ ] Implement RBAC middleware to check if a user is an `OWNER` or `EDITOR` before allowing file modifications.

### Phase 2: The Hierarchical Workspace
- [ ] Create `Folder` table and update `File` table with `path`.
- [ ] Implement APIs for `create_folder`, `move_file`, `rename_file`.
- [ ] Build the WebSocket broadcast system for structural updates.
- [ ] Implement the Recursive File Tree UI.

### Phase 3: Workspace-Aware Execution
- [ ] Update API to send all project files to the worker.
- [ ] Update `SandboxRunner` to replicate the directory structure.
- [ ] Test multi-file imports (e.g., Python `import utils` from `utils.py`).

### Phase 4: Presence & Polish
- [ ] Implement Global Presence (Who is in which file).
- [ ] Add "Project Settings" (Rename project, Invite users).
- [ ] Final end-to-end testing with 4+ concurrent users.
