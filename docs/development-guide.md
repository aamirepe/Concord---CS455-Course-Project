# Concord Project Guide: Understanding the Codebase

Welcome to Concord! This document is designed for someone with limited development experience. It explains what this project is, how it's built, and exactly what you need to learn to understand and contribute to it.

## 🌟 The Big Picture
Concord is a **Collaborative Code Execution Platform**.
Imagine a mix of Google Docs and an Online IDE. Multiple users can join a "Project," see the same files, edit code together in real-time, and run that code on a server to see the output.

---

## 🏗️ 1. The Backend (The "Brain")
The backend handles the database, user accounts, file storage, and the logic for running code.

### What is done & How:
- **API Server:** Built with **FastAPI**. It provides "endpoints" (URLs) that the frontend calls to get data (e.g., `/projects` to see your projects).
- **Database:** Uses **PostgreSQL** to store users, projects, and files. It uses **SQLAlchemy** (an ORM) so we can talk to the database using Python instead of raw SQL.
- **Data Validation:** Uses **Pydantic**. This ensures that if the frontend sends a "Project Name," it's actually a string and not a number.
- **Migrations:** Uses **Alembic**. When we change the database structure (e.g., adding a "folders" table), Alembic keeps track of those changes so we don't lose data.
- **Code Execution:** A specialized "Worker" process that takes the code you wrote, puts it in a **Sandbox** (for security), runs it, and sends the output back.

### 📚 What you need to learn:
| Component | Concept to Search/Learn | Why? |
| :--- | :--- | :--- |
| **Language** | Python Basics | The entire backend is written in Python. |
| **Framework** | FastAPI | To understand how URLs map to Python functions. |
| **Database** | SQL & PostgreSQL | To understand how data is stored in tables. |
| **ORM** | SQLAlchemy (Async) | To understand how Python objects map to DB rows. |
| **Validation** | Pydantic | To understand how API requests/responses are shaped. |
| **Environment** | Virtual Environments (`venv`) | To understand how to install dependencies. |

---

## 🎨 2. The Frontend (The "Face")
The frontend is what the user sees and interacts with in the browser.

### What is done & How:
- **UI Framework:** Built with **React**. Everything is broken down into "Components" (e.g., `FileTree`, `Editor`, `Topbar`).
- **Language:** **TypeScript**. It's like JavaScript but with "Types" (it tells you if a variable is a string or a number), which prevents many bugs.
- **Build Tool:** **Vite**. It's the engine that bundles the code and lets you see changes instantly (Hot Module Replacement).
- **Code Editor:** Uses the **Monaco Editor** (the same engine that powers VS Code).
- **Styling:** Uses **CSS** with custom variables (`--bg`, `--accent`) for a dark-themed professional look.

### 📚 What you need to learn:
| Component | Concept to Search/Learn | Why? |
| :--- | :--- | :--- |
| **Language** | JavaScript (ES6+) $\rightarrow$ TypeScript | The foundation of all web development. |
| **Library** | React (Hooks: `useState`, `useEffect`) | To understand how the UI updates when data changes. |
| **Routing** | React Router | To understand how the app switches between the Dashboard and Session pages. |
| **Styling** | CSS Flexbox & Grid | To understand how the layout (sidebar, main area) is built. |
| **Networking** | Fetch API / HTTP Requests | To understand how the frontend asks the backend for data. |

---

## ⚡ 3. Real-time Collaboration (The "Magic")
This is the hardest part of the project. It allows two people to type in the same file without overwriting each other.

### What is done & How:
- **WebSockets:** Unlike a standard website that asks for data and closes the connection, WebSockets keep a "pipe" open. This allows the server to "push" updates to the user instantly.
- **CRDTs (Conflict-free Replicated Data Types):** We use a library called **Yjs**. It ensures that if User A and User B type at the same time, the final document is the same for both of them, regardless of network lag.
- **Awareness:** This tracks where other users' cursors are and what their usernames are.

### 📚 What you need to learn:
| Component | Concept to Search/Learn | Why? |
| :--- | :--- | :--- |
| **Protocol** | WebSockets (WS/WSS) | To understand bi-directional, real-time communication. |
| **Algorithm** | CRDTs (Conflict-free Replicated Data Types) | To understand how "collaborative editing" actually works. |
| **Library** | Yjs | The specific tool we use to implement CRDTs. |

---

## 🐳 4. Infrastructure & Deployment
How the app is packaged and run.

### What is done & How:
- **Docker:** We wrap the Backend, Frontend, and Worker into "Containers." This ensures the app runs exactly the same on your computer as it does on mine.
- **Docker Compose:** A single file (`docker-compose.yml`) that starts all the containers (DB, Backend, Frontend, Redis) with one command.

### 📚 What you need to learn:
| Component | Concept to Search/Learn | Why? |
| :--- | :--- | :--- |
| **Tool** | Docker Basics (Images, Containers) | To understand how to isolate the environment. |
| **Orchestration** | Docker Compose | To understand how multiple services talk to each other. |

---

## 🚀 Recommended Learning Path for You
If you are starting from zero, follow this order:
1. **Python Basics** $\rightarrow$ **FastAPI** $\rightarrow$ **SQL Basics**.
2. **JavaScript Basics** $\rightarrow$ **TypeScript** $\rightarrow$ **React Basics**.
3. **HTTP/WebSockets** $\rightarrow$ **Docker**.
4. **Advanced:** CRDTs and Yjs.

**Pro Tip:** Don't try to learn everything first. Pick one small feature (e.g., "Change the color of the Run button") and try to find the code that controls it. Learning by doing is the fastest way!
