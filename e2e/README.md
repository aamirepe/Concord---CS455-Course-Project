# Browser end-to-end tests

Two real browsers (Chromium via Playwright) against a running stack with the
pilot's demo accounts: co-editing, Save/history/restore, tabs/quick open/search,
the console with program input, and debugging together.

```bash
scripts/pilot.sh up          # or any stack with `pilot.py seed` run
docker run --rm --network host --ipc=host -v "$PWD/e2e:/e2e" -w /e2e \
  mcr.microsoft.com/playwright/python:v1.60.0-noble \
  sh -c "pip install -q --break-system-packages -r requirements.txt && pytest -q"
```

Screenshots of every test go to `e2e/out/` (kept by CI when a test fails).
Set `CONCORD_APP` / `CONCORD_API` to test another stack. Each test works in its
own files (`e2e_*.py`) of the "Pilot demo" project, so it doesn't disturb the
demo files and can be re-run.
