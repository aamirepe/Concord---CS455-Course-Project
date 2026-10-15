import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../lib/api";
import SnapshotsPanel from "./SnapshotsPanel";
import { ApiError } from "../lib/errors";

const snap = { id: 5, project_id: 1, label: "before refactor", created_at: new Date().toISOString(), created_by_username: "alice", file_count: 3 };

describe("SnapshotsPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "listSnapshots").mockResolvedValue([snap]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists snapshots with who, when and how many files", async () => {
    render(<SnapshotsPanel projectId={1} canEdit />);
    expect(await screen.findByText("before refactor")).toBeInTheDocument();
    expect(screen.getByText(/alice · just now · 3 files/)).toBeInTheDocument();
  });

  it("takes a named snapshot", async () => {
    const create = vi.spyOn(api, "createSnapshot").mockResolvedValue({ ...snap, id: 6, label: "v2" });
    render(<SnapshotsPanel projectId={1} canEdit />);
    fireEvent.click(await screen.findByText("+ Snapshot"));
    fireEvent.change(screen.getByLabelText("Snapshot name"), { target: { value: " v2 " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(1, "v2"));
    expect(await screen.findByText('Snapshot "v2" saved (3 files).')).toBeInTheDocument();
  });

  it("restores after confirming and reports it", async () => {
    const restore = vi.spyOn(api, "restoreSnapshot").mockResolvedValue({ snapshot_id: 5, restored_files: 2, safety_snapshot_id: 7 });
    render(<SnapshotsPanel projectId={1} canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore before refactor" }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("snapshot of the current state is taken first"));
    await waitFor(() => expect(restore).toHaveBeenCalledWith(1, 5));
    expect(await screen.findByText('Restored 2 files from "before refactor".')).toBeInTheDocument();
  });

  it("does nothing when the restore isn't confirmed", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const restore = vi.spyOn(api, "restoreSnapshot");
    render(<SnapshotsPanel projectId={1} canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore before refactor" }));
    expect(restore).not.toHaveBeenCalled();
  });

  it("viewers can see snapshots but not take or restore them", async () => {
    render(<SnapshotsPanel projectId={1} canEdit={false} />);
    await screen.findByText("before refactor");
    expect(screen.queryByText("+ Snapshot")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restore/ })).not.toBeInTheDocument();
  });

  it("reports a failed restore", async () => {
    vi.spyOn(api, "restoreSnapshot").mockRejectedValue(new ApiError(403, "Forbidden", '{"detail":"Editor role required"}'));
    render(<SnapshotsPanel projectId={1} canEdit />);
    fireEvent.click(await screen.findByRole("button", { name: "Restore before refactor" }));
    expect(await screen.findByText("Restore failed. You don't have permission to do that: editor role required")).toBeInTheDocument();
  });
});
