FROM python:3.12-slim

# No git, no curl, no extra packages — minimal attack surface.
# debugpy is the only third-party module needed (for the debug runs).
RUN pip install --no-cache-dir debugpy==1.8.7

# Create a non-root user. The container is still network-disabled and read-only,
# but defense in depth: a non-root user means even if a process escapes its
# intended scope it can't write to root-owned paths.
# Fixed uid: the worker chowns each run's workspace to it (SANDBOX_UID in
# backend/app/services/sandbox.py).
RUN useradd --uid 10001 --create-home --shell /bin/bash runner
USER runner
WORKDIR /workspace

# A trivial entrypoint. The actual file to run is provided at /workspace/main.py
# by the worker (mounted read-only).
ENTRYPOINT ["python", "/workspace/main.py"]
