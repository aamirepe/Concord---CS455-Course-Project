import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../lib/api";

const diffProps: { current: { original?: string; modified?: string } | null } = { current: null };
vi.mock("@monaco-editor/react", () => ({
  DiffEditor: (props: { original: string; modified: string }) => {
    diffProps.current = props;
    return <div data-testid="diff" />;
  },
}));
const { default: HistoryModal } = await import("./HistoryModal");

const file = { id: 2, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };
const versions = [
  { version: 3, created_at: "2026-09-27T10:02:00Z", saved_by: { id: 2, username: "bob" } },
  { version: 2, created_at: "2026-09-27T10:01:00Z", saved_by: null },
];

describe("HistoryModal", () => {
  beforeEach(() => {
    vi.spyOn(api, "listVersions").mockResolvedValue(versions);
    vi.spyOn(api, "getVersion").mockImplementation(async (_p, _f, n) => ({ version: n, created_at: "x", text: `text v${n}\n` }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  const show = (canRestore = true, onClose = vi.fn()) =>
    render(<HistoryModal projectId={1} file={file} currentText={() => "now\n"} canRestore={canRestore} onClose={onClose} />);

  it("lists versions, marks saves, and diffs the newest against the current text", async () => {
    show();
    expect(await screen.findByText(/Saved by bob/)).toBeInTheDocument();
    expect(screen.getByText(/autosave/)).toBeInTheDocument();
    await waitFor(() => expect(diffProps.current).toEqual(expect.objectContaining({ original: "text v3\n", modified: "now\n" })));

    fireEvent.click(screen.getByText("v2"));
    await waitFor(() => expect(diffProps.current?.original).toBe("text v2\n"));
  });

  it("a slow earlier list can't replace the saves-only list", async () => {
    let releaseAll!: (v: typeof versions) => void;
    vi.mocked(api.listVersions)
      .mockImplementationOnce(() => new Promise((r) => { releaseAll = r; }))
      .mockResolvedValueOnce([versions[0]]);
    show();
    fireEvent.click(screen.getByLabelText(/Saves only/));
    expect(await screen.findByText(/Saved by bob/)).toBeInTheDocument();
    releaseAll(versions); // the first (all versions) request finishes last
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/autosave/)).not.toBeInTheDocument();
  });

  it("filters to saves only", async () => {
    show();
    await screen.findByText(/Saved by bob/);
    fireEvent.click(screen.getByLabelText(/Saves only/));
    await waitFor(() => expect(api.listVersions).toHaveBeenLastCalledWith(1, 2, true));
  });

  it("restores the selected version after confirming, then closes", async () => {
    const restore = vi.spyOn(api, "restoreVersion").mockResolvedValue({ restored_from: 3, version: 4, changed: true });
    const onClose = vi.fn();
    show(true, onClose);
    await waitFor(() => expect(diffProps.current?.original).toBe("text v3\n"));
    fireEvent.click(screen.getByRole("button", { name: "Restore this version" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(restore).toHaveBeenCalledWith(1, 2, 3);
  });

  it("has no restore button for viewers, and says when a version equals the current text", async () => {
    vi.mocked(api.getVersion).mockResolvedValue({ version: 3, created_at: "x", text: "now\n" });
    show(false);
    expect(await screen.findByText("Same as now")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restore this version" })).not.toBeInTheDocument();
  });

  it("shows an error instead of failing silently", async () => {
    vi.mocked(api.listVersions).mockRejectedValue(new Error("500 boom"));
    show();
    expect(await screen.findByText(/500 boom/)).toBeInTheDocument();
  });
});
