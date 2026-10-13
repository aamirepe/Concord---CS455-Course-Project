# Concord local pilot: tester's guide

This guide is for someone trying Concord for the first time, on their own
machine. You don't need to know the code. Plan on about an hour: 15 minutes of
setup (mostly waiting for the first build) and 45 minutes of scenarios.

**What Concord is:** a browser-based code editor where several people edit the
same files at the same time (like Google Docs for code), run the code in a
safe sandbox, and ask an AI assistant to explain or fix it. Each person has a
role in a project: **owner**, **editor**, **debugger** or **viewer**.

---

## 1. One-time setup

You need Docker. On Ubuntu (including WSL2 with systemd):

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx
sudo systemctl enable --now docker
sudo usermod -aG docker $USER      # use Docker without sudo
```

You don't have to log out afterwards; the pilot script handles the new group.

**The AI assistant (optional):** put a Mistral API key in `.env` as
`MISTRAL_API_KEY=...` (free at https://console.mistral.ai). Without it,
everything else works, and the project owner is asked for a key inside the app.
Keep `MISTRAL_MODEL=codestral-latest`: free trial keys have no quota for some
other models.

## 2. Start

From the project folder:

```bash
scripts/pilot.sh up        # or: make pilot
```

The script:
1. Checks Docker and free ports (5432, 6379, 8000, 5173).
2. Creates `.env` if it's missing, with fresh secrets.
3. Builds and starts everything. The first time takes 5–10 minutes.
4. Creates the demo accounts and project.
5. Runs 16 automated checks: co-editing, Save, history and snapshots, permissions, running Python (with input), C++ and JavaScript, the sandbox limits, and the AI.

It ends with a summary like this:

```
16 passed, 0 failed, 0 skipped
Concord is running
  App:        http://localhost:5173
```

If a check **fails**, carry on with the pilot anyway, but note the failure in
the feedback form. Run `scripts/pilot.sh collect` and include the folder it
prints when you share results.

## 3. Accounts

Every account's password is `pilot-pass-123`.

| User | Role in "Pilot demo" | Use it to test |
|---|---|---|
| alice | owner | everything; managing members |
| bob | editor | editing and running code |
| carol | viewer | read-only access |
| dave | debugger | editing and running (debug tools are exploratory) |
| erin | not a member | asking to join a project |

**Being two people at once:** the login is remembered per browser window
profile. Use a normal window for one user and a **private/incognito** window for
another. For a third user, use a different browser (e.g. Chrome and Edge).

## 4. Scenarios

Tick Pass or Fail for each one and write notes in `pilot-results/feedback.md`
(created by the script). "Expected" is what should happen. Anything else is
worth reporting, including things that work but feel confusing or slow.

### S1: Sign in and find the project (alice)
1. Open http://localhost:5173 and sign in as **alice**.
2. Under *Your Active Projects* you see **Pilot demo** with its Project ID.
3. Click **Open Workspace**.

**Expected:** a workspace with a file list on the left (`main.py`, `hello.cpp`,
`greet.js`), an editor in the middle, a *Console* under it, and the
*AI Assistant* on the right. ☐ Pass ☐ Fail

### S2: Edit together in real time (alice + bob)
1. In a private window, sign in as **bob** and open **Pilot demo**.
2. Both of you open `main.py`.
3. alice types a comment on line 1. bob types on the last line at the same time.

**Expected:**
- Each person's typing appears in the other window within about a second.
- Nothing is duplicated or lost.
- Small coloured dots above the editor show who is in the file.
- The status line above the editor says **✓ All changes synced**.

4. bob switches to `greet.js` and back to `main.py`.

**Expected:** everything both of you typed is still there.

☐ Pass ☐ Fail

### S2b: Save and change history (alice + bob)
1. alice opens `hello.cpp`. bob opens `greet.js`, adds two lines and waits about 5 seconds.
2. bob clicks **Save** (or presses Ctrl+S).

**Expected:**
- bob sees "Saved as version N"; pressing Save again says "No changes since version N".
- In alice's file tree, `greet.js` gets a ● ("Changed by bob…"); it goes away when she opens the file.
- **Recent changes** in alice's sidebar lists `greet.js` with a bullet like `bob +2 −0`.

☐ Pass ☐ Fail

### S2c: History, restore and snapshots (alice + bob)
1. Both open `main.py`. alice clicks **+ Snapshot** in the sidebar and names it "start".
2. alice types a line and clicks **Save**, then breaks the code (e.g. deletes a bracket).
3. alice clicks **History**, ticks **Saves only**, picks her save and clicks **Restore this version**.
4. bob changes `greet.js`. alice restores the snapshot "start" from the sidebar.

**Expected:**
- History shows "Saved by alice" and a side-by-side diff with the broken text.
- After step 3, bob's editor shows the restored code without reloading.
- After step 4, `greet.js` is back as it was for both of you, and a snapshot
  "Before restoring 'start'" appeared (restore it to undo).

☐ Pass ☐ Fail

### S2d: Cursors with names (alice + bob)
Click around in the same file in both windows.

**Expected:** each of you sees the other's cursor in their colour with their
name above it, also on line 1. ☐ Pass ☐ Fail

### S2e: Tabs, quick open and search (alice + bob)
1. alice opens `main.py`, `greet.js` and `hello.cpp`; each gets a tab.
2. bob opens `greet.js`.
3. alice presses **Ctrl+P**, types `gre`, presses Enter.
4. alice presses **Ctrl+Shift+F** and searches for `names`, then clicks a result.
5. alice reloads the page.

**Expected:**
- In step 2, bob's initial appears on alice's `greet.js` tab and in her file
  tree, and disappears when bob opens another file.
- Step 3 switches to `greet.js`.
- Step 4 lists matches by file and line; clicking one opens the file with the
  match selected.
- After step 5, the same tabs are open. ☐ Pass ☐ Fail

### S3: Viewers can look but not touch (carol)
1. In a third browser, sign in as **carol** and open `main.py`.
2. Try to type.

**Expected:**
- The editor doesn't change, and a *Read-only: viewers can't edit* note shows above it.
- carol still sees alice's and bob's edits live.
- Clicking **Run** shows an error that runs need editor rights.

☐ Pass ☐ Fail

### S4: Run code (bob)
1. Open `main.py` and click **Run**.
   **Expected:** the Console shows a block "main.py · run #N by bob" that goes
   from `queued` to `failed`, with a Python error in red ending in
   `TypeError: object of type 'int' has no len()`, and an **Ask AI to fix**
   button. That's the deliberate bug for S5. alice's console shows bob's run
   too. ☐ Pass ☐ Fail
2. Open `hello.cpp` and click **Run**.
   **Expected:** after compiling, three lines of `Hello from C++`. ☐ Pass ☐ Fail
3. Open `greet.js` and click **Run**.
   **Expected:** `Hi alice!`, `Hi bob!`, `Hi carol!`. ☐ Pass ☐ Fail
4. Create `sum.py` with `a = int(input())`, `b = int(input())`, `print(a + b)`.
   Click **Input** in the console, type `20` and `22` on two lines, and click **Run**.
   **Expected:** the run is marked "with input" and prints `42`. Run it again
   with the input box empty: it fails with `EOFError` (no hanging). ☐ Pass ☐ Fail
5. Create a file `loop.py` (**+ New file**), type `while True: pass`, and click **Run**.
   **Expected:** the run is stopped after about 10 seconds with status
   `timeout`, and the app stays responsive. ☐ Pass ☐ Fail

### S4b: Debug together (dave drives, bob watches)
1. dave opens `main.py` and clicks just left of the line number **5**
   (`return total / len(x)`): a red dot appears. bob sees it too.
2. bob opens the **Debug** tab below the editor.
3. dave presses **Debug** (under Run).
4. dave presses **F10** (step over) once.
5. dave presses **Shift+F5** (or **■ Stop**), or **F5** to let it run to the end.

**Expected:**
- After step 3, both see line 5 highlighted, "Paused at main.py:5 (breakpoint)"
  and the variables: `xs` (click ▸ to see the four scores) and `total` = 311.
  The call stack shows `average` and `<module>`; clicking `<module>` shows the
  `scores` variable.
- bob's step buttons are greyed out (editors can watch, not step).
- After step 4, the program ends with `TypeError: object of type 'int' has no
  len()` in the Console (the bug S5 fixes: `len(x)` should be `len(xs)`), and
  the Debug tab says the program finished.
- carol (a viewer) opening the project while it's paused sees the same pause.

☐ Pass ☐ Fail

### S5: AI finds and fixes the bug (alice, with bob and carol watching)
1. After S4 step 1, open `main.py` as alice.
2. Click **Ask AI to fix** on the failed run in the console (or choose **Fix last error** in the AI panel and click **Send**).

**Expected:**
- The AI already has the file and the error, so it usually goes straight to "proposed an edit to main.py" in a few seconds.
- bob and carol see *"alice is asking the AI (fix, main.py)"* at the top of their AI panel.
- After a few seconds, an **AI Proposal** window shows the original and proposed code: `len(x)` → `len(xs)`.

☐ Pass ☐ Fail

3. Click **Accept Change**.

**Expected:**
- The fix appears in alice's editor, and in bob's and carol's within a second.
- Running `main.py` again prints `Average score: 77.75`.

☐ Pass ☐ Fail

4. Try **Explain** and **Review** modes on `hello.cpp`, then ask a follow-up question.

**Expected:** sensible answers, formatted with code blocks you can **Copy**,
and the follow-up uses the earlier context. Explain and Review never propose
edits. ☐ Pass ☐ Fail

5. Select a few lines in the editor (the panel shows "Selection: lines …") and ask "what does this do?".

**Expected:** the answer is about the selected lines. **New chat** clears the conversation. ☐ Pass ☐ Fail

### S6: A viewer can't accept AI changes (carol)
As carol, choose **Fix last error** on any file that has an error.

**Expected:** if a proposal appears, **Accept Change** is disabled with a note
explaining why. ☐ Pass ☐ Fail

### S7: Files and folders stay in sync (alice + bob)
alice creates a folder and a file inside it, then renames the file. She also
creates `notes.txt`.

**Expected:** bob's file list updates by itself, without refreshing. Both can
type in `notes.txt`; its **Run** button is greyed out. ☐ Pass ☐ Fail

### S8: Asking to join a project (erin → alice)
1. Sign in as **erin**. Under *Join Existing Project*, enter the Pilot demo's
   Project ID and click **Join Project**, then **Request access**.
2. As alice, reload the workspace. The **Members** button shows *(1 pending)*.
   Open it, pick a role for erin, and click **Approve**.
3. erin clicks **Refresh** on the dashboard.

**Expected:** Pilot demo now appears for erin and opens with the role alice
chose. ☐ Pass ☐ Fail

### S9: Nothing is lost (anyone)
1. Type something distinctive in a file and wait 10 seconds.
2. Reload the page.
3. In a terminal, run `docker compose restart backend` and wait about 20 seconds.
4. Reload again.

**Expected:** your text is still there after both reloads. ☐ Pass ☐ Fail

5. Now keep typing *while* the backend restarts: run `docker compose restart backend`
   and type a line straight away.

**Expected:** the status line says **Offline: reconnecting…**, then
**✓ All changes synced** again, and the line you typed reaches the other
person's window. ☐ Pass ☐ Fail

### Exploratory (no fixed expected result)
- **Debug button (dave).** The debugger is early work; note what happens.
- **Metrics page** (link on the dashboard).
- Anything you'd naturally try: large files, pasting code, many quick runs,
  closing a tab mid-edit.

## 5. Reporting

For each problem, write in `pilot-results/feedback.md`:
- the scenario
- what you did
- what you expected
- what happened
- a screenshot if possible
- the time

Then run:

```bash
scripts/pilot.sh collect
```

This saves the server logs to `pilot-results/logs-<time>/`, with any API keys
redacted. Share that folder with the feedback file.

## 6. Commands

| Command | What it does |
|---|---|
| `scripts/pilot.sh status` | what's running and the URLs |
| `scripts/pilot.sh check` | re-run the automated checks |
| `scripts/pilot.sh logs backend` | follow one service's logs (`backend`, `worker`, `frontend`, `postgres`, `redis`) |
| `scripts/pilot.sh seed` | re-create the demo accounts and project (never overwrites edited files) |
| `scripts/pilot.sh stop` | stop, keeping all data; `up` resumes |
| `scripts/pilot.sh reset` | **delete all pilot data** and start over |

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `No permission to use Docker` | `sudo usermod -aG docker $USER`, then re-run |
| `port 5432 already in use` | A local Postgres is running: `sudo systemctl stop postgresql` |
| Blank page at :5173 | `scripts/pilot.sh logs frontend`; the first start can take a minute after "ok" |
| Run stays on `queued` | `scripts/pilot.sh logs worker`; the worker needs `/var/run/docker.sock` |
| AI says "not configured" | add `MISTRAL_API_KEY` to `.env`, then `docker compose up -d backend` |
| AI says "Rate limited" | wait a minute; free keys allow a few requests per minute |
| Login fails for a pilot user | `scripts/pilot.sh seed` re-creates the accounts |
| From Windows, `localhost:5173` doesn't open | WSL2 forwards ports by default; check `localhostForwarding` in `.wslconfig` |
