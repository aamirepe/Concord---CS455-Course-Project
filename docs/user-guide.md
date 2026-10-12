# Concord — User Guide

## Getting started

1. Open the app in your browser.
2. Sign up with a username, email, and password.
3. From the dashboard, click **Create** to make a new project, then click the
   project to open a collaboration session.
4. The session shows a file tree on the left, an editor in the middle, and
   a console at the bottom.

## Real-time collaboration

- Anyone you invite to the project edits the same file. You'll see their
  cursor in the editor with their name, and their initial in the bar above it.
- Edits are CRDT-merged — even if you both type at the same line, no edits
  are lost.
- The status line above the editor tells you where your edits stand:
  **✓ All changes synced** (everyone has them, and they're saved within a few
  seconds), **Syncing…**, or **Offline: reconnecting…**. While offline, keep
  typing: your edits stay in the tab and are sent when the connection is back.
  Don't close the tab while it says Offline (the browser will ask).
- **Save** (or **Ctrl+S** / **Cmd+S**) makes a version right away, e.g. before
  a risky change. Viewers don't have a Save button.
- **Recent changes** in the sidebar lists the five most recently changed files,
  with a bullet per person: `bob +12 −3` means bob added 12 lines and removed 3
  (an edited line counts as one of each). Click a file name to open it.
- A **●** next to a file in the tree means someone else changed it since you
  last opened it; hover it to see who. It clears when you open the file.

## Finding your way around

- **Tabs:** every file you open gets a tab above the editor. Click to switch,
  **×** or middle-click to close, **Ctrl+Alt+W** closes the current one. Your
  tabs come back when you reopen the project in the same browser.
- Small coloured initials on a tab or in the file tree show **who else is in
  that file** (hover for names). They go away when that person leaves the file.
- **Ctrl+P** (**Cmd+P**): type part of a file name and press Enter. With
  nothing typed, recently opened files come first.
- **Ctrl+Shift+F**, or **Search** at the top of the sidebar: finds text in
  every file, including edits nobody has saved yet. **Aa** matches case, **ab**
  matches whole words only. Click a result to jump to it. Search is for plain
  text (no regular expressions) and shows the first 500 results.
- Files that aren't code (`notes.txt`, `README.md`) can be created and edited
  together like any other file; only `.py`, `.js` and `.c/.cpp` files can be run.

## Running code

1. Click **Run** in the sidebar.
2. The console at the bottom shows one block per run: the file, who ran it
   and when, its status as it changes (`queued → starting → running →
   completed`, or `failed`/`timeout`/`cancelled`), the output (errors in red),
   and the exit code and duration. Everyone in the project sees everyone's runs.
3. **Input:** if your program reads input (`input()`, `cin`, `stdin`), click
   **Input** in the console and type it there, one value per line, before
   pressing Run. Without input, reading gets end-of-file instead of hanging.
4. A failed run has an **Ask AI to fix** button: it asks the assistant to find
   and fix the bug, using that run's error.
5. **Clear** empties your console (only yours). **Cancel** stops a long run.

## Debugging (Python)

1. Click just **left of a line number** to set a breakpoint (a red dot).
   Click it again to remove it. Everyone sees the same breakpoints.
2. Press **Debug** (under Run). The program runs until it reaches a breakpoint.
   The **Debug** tab below the editor opens, and the line it stopped on is
   highlighted in yellow. If it stops in another file, that file opens.
3. Step with the buttons or keys: **Continue** (F5), **Step over** (F10),
   **Step into** (F11), **Step out** (Shift+F11), **Pause** (F6),
   **Stop** (Shift+F5).
4. **Call stack**: click a frame to see that function's variables.
   **Variables**: click ▸ to look inside lists, dicts and objects.
5. The program's output appears in the **Console** tab as it runs.

It's collaborative: everyone in the project sees where it's paused, the
variables, and who stepped last ("last step by bob"). Anyone can look around;
stepping, setting breakpoints and starting the debugger need the **debugger**
role (or owner). There is one debug session per project at a time. It stops by
itself after 3 minutes paused with nobody stepping, or after 10 minutes.
Program input (stdin) isn't available while debugging.

## AI assistant

The panel on the right of the editor talks to a coding agent that can read
the project's files and the output of your last run.

1. Pick a mode:
   - **Ask** — any question about the project.
   - **Explain** — what the open file does.
   - **Fix last error** — run your code first; the agent reads the error,
     finds the cause and proposes a fix. You can leave the message empty.
   - **Generate** — describe code to write; it proposes where to put it.
   - **Review** — a prioritised list of bugs and risks. Never edits.
2. While the agent works you see each step as it happens ("read main.py",
   "checked last run of main.py"); the steps stay listed under the answer.
3. When the agent proposes a change, the editor shows it side by side
   (original / proposed). **Accept Change** applies it for everyone in the
   file, like a normal edit; **Reject** discards it. The proposal is never
   applied automatically.
4. Follow-up questions keep the conversation's context. **New chat** starts over.
5. **Ask about a selection:** select some lines in the editor; the panel shows
   "Selection: lines 4–9". Your next question is about those lines (click ✕
   to leave them out).

Answers are formatted: code comes in blocks with a **Copy** button. The agent
already has the open file (and, for "Fix last error", the last run's output)
when it starts, so it answers faster.

When another member is using the assistant, a line at the top of the panel
says so ("alice is asking the AI (fix, main.py)"). If they get a proposal for
the file you have open, you're told; if they accept it, the change arrives in
your editor like any other edit. You never see their question or the answer.

Viewers can use the assistant but can't accept changes. If the project has no
API key (and the server has none), the owner sees a field to add a Mistral
key; other members are asked to contact the owner.

## History and snapshots

- **History** (next to Save, above the editor) lists the file's versions:
  autosaves every few seconds of editing, and saves ("Saved by bob"). Tick
  **Saves only** to hide the autosaves. Pick one to see it side by side with
  the current text, and **Restore this version** to bring it back (editors).
  Everyone with the file open sees the change; the text before the restore
  stays in the history.
- **Snapshots** (left sidebar) keep every file of the project at once.
  **+ Snapshot**, give it a name like "v1 — works on small input". **Restore**
  puts every file back as it was, for everyone. A snapshot of the current
  state is taken first ("Before restoring …"), so you can undo a restore.
- Use them before risky edits; they cost nothing.

## Other people's cursors

Each person's cursor shows in their colour with their name above it, and
their selection is tinted.

## Roles

| Role | Can do |
|---|---|
| viewer | read code, watch runs and debug, no edits |
| editor | everything viewer can + edit + run + take snapshots |
| debugger | everything editor can + set breakpoints + step |
| owner | everything + manage members and change roles |

The session owner is the only one who can change roles. Default for new
joiners is viewer.
