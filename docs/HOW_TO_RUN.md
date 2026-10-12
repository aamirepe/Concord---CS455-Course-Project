# How to Run Concord on Your Desktop — A Beginner's Guide

This walks you through getting the whole thing running from a fresh machine.
No prior experience with Docker is required — but if you've never used the
terminal before, this is a good day to learn. Every step is a single
command and I'll explain what it does.

Total time: ~15 minutes if you have nothing installed.

## 0. What you need to install first

You need three things on your computer before we start. Skip any you
already have.

### 0.1 A terminal

On macOS: press `Cmd+Space`, type "Terminal", press Enter. That's it.
On Linux: you already know where it is.
On Windows: install [Windows Terminal](https://aka.ms/terminal) and
[WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) — WSL gives
you a real Linux inside Windows. Inside WSL, follow the Linux instructions.

### 0.2 Docker Desktop

This is the program that runs all of Concord's services in isolated
containers. Download it from <https://www.docker.com/products/docker-desktop/>.
Install it, start it, and make sure the little whale icon in your menu bar
is not showing any errors.

To check it works, run in your terminal:

```bash
docker --version
docker compose version
```

You should see two version numbers. If you see "command not found",
restart your terminal after installing Docker.

### 0.3 Git

On macOS, install [Xcode Command Line Tools](https://developer.apple.com/xcode/):
```bash
xcode-select --install
```

On Linux:
```bash
sudo apt update && sudo apt install -y git
```

Verify:
```bash
git --version
```

That's it for prerequisites. You don't need to install Python, Node, or
Postgres — those run inside Docker.

## 1. Get the code

Pick a folder where you want the project to live. In your terminal:

```bash
cd ~/Desktop           # or wherever you want
git clone <repo-url> concord
cd concord
```

If you already have a local checkout, start from its repository root and
skip the clone step.

## 2. Create the environment file

This is a small file that holds secrets and configuration. We provide a
template:

```bash
cp .env.example .env
```

Open `.env` in any text editor. The only thing you should change right now
is the `SECRET_KEY` value — generate a real one with:

```bash
openssl rand -hex 32
```

Copy the output and paste it in place of `change-me-in-prod-32-bytes-minimum`.
Save the file.

Everything else can stay as-is for local development.

**Optional — AI assistant.** Create a free API key at
<https://console.mistral.ai> and paste it after `MISTRAL_API_KEY=`. With it, every
project gets the assistant; without it, each project owner can paste their own
key in the AI panel. To check the key works (no Docker needed):

```bash
PYTHONPATH=backend python scripts/agent_smoke.py
```

## 3. Start everything

This is the one command that brings up the whole stack:

```bash
docker compose up -d --build
```

What's happening:
- `--build` tells Docker to build the backend, worker, and frontend images
  (the first time, this takes 2-3 minutes; subsequent times it's instant)
- `up -d` starts the services in the background

You should see something like:
```
[+] Running 6/6
 ✔ Network concord_default         Created
 ✔ Container concord-postgres-1    Started
 ✔ Container concord-redis-1       Started
 ✔ Container concord-backend-1     Started
 ✔ Container concord-worker-1      Started
 ✔ concord-worker-2                Started
 ✔ Container concord-frontend-1    Started
```

To follow what's happening, you can tail the logs:
```bash
docker compose logs -f
```

Press `Ctrl+C` to stop tailing. The services keep running.

## 4. Open the app

Three URLs matter:

| What | URL |
|---|---|
| The web app | <http://localhost:5173> |
| The API docs (Swagger UI) | <http://localhost:8000/docs> |
| The API health check | <http://localhost:8000/healthz> |

Open <http://localhost:5173> in your browser.

## 5. Create your first account

1. Click **Sign up** in the top right.
2. Enter a username (3+ chars), email, and a password (8+ chars).
3. You should be redirected to the dashboard.

## 6. Create a project and a session

1. In the left sidebar, type a name like "My first project" and click
   **Create**.
2. Click on the project name in the file tree.
3. You should see an empty editor and an empty file tree.

## 7. Add a file and write some code

1. In the left sidebar under "Files", click **+ New file**.
2. When prompted, type `main.py` and press Enter.
3. Type some Python in the editor:
   ```python
   def greet(name):
       return f"Hello, {name}!"

   print(greet("world"))
   ```

## 8. Run the code

1. Click **Run** in the sidebar.
2. The console at the bottom should show:
   - `queued` (the job is waiting in Redis)
   - `starting` (a worker picked it up)
   - `running` (the container is launching)
   - `output: Hello, world!`
   - `completed`

The whole thing should take 1-2 seconds.

## 9. Open a second tab and watch live editing

1. Open a private/incognito window in your browser.
2. Go to <http://localhost:5173>.
3. Sign up with a *different* username and email.
4. In the dashboard sidebar, under "Join session", type the session ID from
   the URL of your first tab (the number after `/sessions/`). Click **Join**.
5. You should now be in the same session, as a viewer by default. Open the
   same file. Type in the first tab. The text appears in the second tab in
   real time, and you see a colored cursor dot for the other user.

To actually edit in the second tab, the owner (you) needs to promote them:

In the first tab, the API allows updating roles. From your terminal:

```bash
# Get your session ID from the URL
SID=1
# Get the other user's user ID by listing members
TOKEN="<paste-your-jwt-here>"

curl -s http://localhost:8000/sessions/$SID/members \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

You'll see a list of `{user_id, role, ...}`. Pick the new user and update:

```bash
curl -s -X PATCH http://localhost:8000/sessions/$SID/members/2 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
```

Now the second user can edit too.

## 10. Try the debugger

1. Click just left of a line number to set a breakpoint (a red dot appears).
   You need the debugger role (or owner).
2. Set a few:
   ```python
   def greet(name):
       return f"Hello, {name}!"    # breakpoint here

   print(greet("world"))
   ```
3. Click **Debug** (under **Run**). The program runs under `debugpy` in the
   sandbox and pauses on your breakpoints. The **Debug** tab shows the call
   stack and variables and has the step buttons (F5, F10, F11, Shift+F11).
   The debugger is on by default (`ENABLE_DEBUGGER=false` turns it off).

The full debug flow is described in `docs/user-guide.md`.

## 11. Save a snapshot

1. In the editor, click the snapshot button in the sidebar (when wired in
   the UI) or use the API:
   ```bash
   curl -s -X POST http://localhost:8000/sessions/$SID/snapshots \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"label": "works on first try"}'
   ```
2. Make more edits to the file.
3. Restore the snapshot:
   ```bash
   # Find the snapshot ID
   curl -s http://localhost:8000/sessions/$SID/snapshots \
     -H "Authorization: Bearer $TOKEN"
   ```
   ```bash
   # Restore it
   curl -s -X POST http://localhost:8000/sessions/$SID/snapshots/1/restore \
     -H "Authorization: Bearer $TOKEN"
   ```
4. The editor in every connected browser reverts to the snapshot state
   immediately.

## 12. Stop everything

When you're done:

```bash
docker compose down
```

This stops and removes the containers. Your data is preserved in Docker
volumes. To remove the data too (a true clean slate):

```bash
docker compose down -v
```

To start again tomorrow:

```bash
docker compose up -d
```

## Common problems

### "docker: command not found"
Docker isn't installed or your terminal hasn't been restarted since the
install. Restart the terminal.

### "port 5432 is already in use"
You have a Postgres running locally on the same port Docker wants to use.
Either stop your local Postgres, or change the port mapping in
`docker-compose.yml` (change `"5432:5432"` to `"5432:5433"` etc.).

### "permission denied while trying to connect to the Docker daemon"
On Linux, your user isn't in the `docker` group. Run:
```bash
sudo usermod -aG docker $USER
```
Then log out and back in.

### The frontend shows a blank page
Check the backend logs:
```bash
docker compose logs backend
```
If you see an error about the database, wait a few seconds — Postgres
might still be initializing. Refresh the page.

### "Image not found" when running code
The worker needs the sandbox image built:
```bash
docker compose build sandbox
docker compose exec worker python -c "from app.services.sandbox import sandbox; print(sandbox.healthcheck())"
```

### Things feel slow
You're running 5+ Docker containers on one machine. That's heavy. On a
laptop with limited RAM, expect some swapping. You can comment out the
second worker replica in `docker-compose.yml` if needed:
```yaml
worker:
  ...
  # deploy:
  #   replicas: 2
```

## Where to look when something is wrong

```bash
# Logs from everything
docker compose logs -f

# Just the backend
docker compose logs -f backend

# Status of all services
docker compose ps

# Open a shell inside the backend container (for debugging)
docker compose exec backend bash

# Reset everything
docker compose down -v
docker compose up -d --build
```

## What's next?

- Read `docs/HOW_IT_WORKS.md` to understand what each piece does
- Read `docs/api.md` to see every endpoint
- Run the tests: `docker compose exec backend pytest`
- Run the load test: `docker compose exec backend python scripts/load_test.py --clients 1 5 10`

Have fun.
