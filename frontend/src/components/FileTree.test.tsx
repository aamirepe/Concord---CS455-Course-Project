import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import FileTree from "./FileTree";
import type { FileMeta, FolderMeta } from "../lib/api";

function folder(overrides: Partial<FolderMeta>): FolderMeta {
  return { id: 1, project_id: 1, parent_folder_id: null, name: "folder", created_at: "now", ...overrides };
}

function file(overrides: Partial<FileMeta>): FileMeta {
  return { id: 1, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now", ...overrides };
}

const noop = () => {};

describe("FileTree", () => {
  it("renders root-level files and folders", () => {
    render(
      <FileTree
        files={[file({ id: 1, path: "main.py", folder_id: null })]}
        folders={[folder({ id: 10, name: "src", parent_folder_id: null })]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={noop}
        presence={{}}
      />
    );

    expect(screen.getByText(/main\.py/)).toBeInTheDocument();
    expect(screen.getByText(/src/)).toBeInTheDocument();
  });

  it("nests files under their folder_id and folders under their parent_folder_id", () => {
    render(
      <FileTree
        files={[file({ id: 1, path: "nested.py", folder_id: 10 })]}
        folders={[folder({ id: 10, name: "src", parent_folder_id: null })]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={noop}
        presence={{}}
      />
    );

    // The folder starts open (isOpen defaults to true), so the nested file is visible.
    expect(screen.getByText(/nested\.py/)).toBeInTheDocument();
  });

  it("renders a cycle-detected fallback instead of recursing infinitely", () => {
    // folder 1's parent is folder 2, and folder 2's parent is folder 1.
    // The visited-set guard only fires when the SAME folder id is encountered
    // twice within one root-to-leaf descent (e.g. a duplicate entry from a
    // state-sync race) — a pure two-folder mutual-parent pair with no path
    // from an actual root (parent_folder_id === null) never enters the
    // recursion at all, so it's a different (currently unguarded) gap, not
    // this one. Reproduce the guarded case: id 1 appears once as a real root
    // and again nested two levels below itself.
    const folders = [
      folder({ id: 1, name: "root", parent_folder_id: null }),
      folder({ id: 2, name: "child", parent_folder_id: 1 }),
      folder({ id: 1, name: "root-dup", parent_folder_id: 2 }),
    ];

    render(
      <FileTree
        files={[]}
        folders={folders}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={noop}
        presence={{}}
      />
    );

    expect(screen.getByText(/Cycle detected/)).toBeInTheDocument();
  });

  it("renders orphan folders (parent_folder_id pointing at a nonexistent folder) at the root instead of hiding them", () => {
    const folders = [folder({ id: 5, name: "orphan", parent_folder_id: 999 })];

    render(
      <FileTree
        files={[]}
        folders={folders}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={noop}
        presence={{}}
      />
    );

    expect(screen.getByText(/orphan/)).toBeInTheDocument();
  });

  it("calls onSelectFile when a file is clicked", () => {
    const onSelectFile = vi.fn();
    const f = file({ id: 1, path: "main.py", folder_id: null });

    render(
      <FileTree
        files={[f]}
        folders={[]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={onSelectFile}
        onSelectFolder={noop}
        onRename={noop}
        presence={{}}
      />
    );

    fireEvent.click(screen.getByText(/main\.py/));
    expect(onSelectFile).toHaveBeenCalledWith(f);
  });

  it("calls onRename with ('file', item) on double-click", () => {
    const onRename = vi.fn();
    const f = file({ id: 1, path: "main.py", folder_id: null });

    render(
      <FileTree
        files={[f]}
        folders={[]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={onRename}
        presence={{}}
      />
    );

    fireEvent.doubleClick(screen.getByText(/main\.py/));
    expect(onRename).toHaveBeenCalledWith("file", f);
  });

  it("calls onSelectFolder when a folder header is clicked", () => {
    const onSelectFolder = vi.fn();
    const fo = folder({ id: 10, name: "src", parent_folder_id: null });

    render(
      <FileTree
        files={[]}
        folders={[fo]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={onSelectFolder}
        onRename={noop}
        presence={{}}
      />
    );

    fireEvent.click(screen.getByText(/src/));
    expect(onSelectFolder).toHaveBeenCalledWith(fo);
  });

  it("shows who else is in a file, by name", () => {
    const f = file({ id: 1, path: "main.py", folder_id: null });

    render(
      <FileTree
        files={[f]}
        folders={[]}
        activeFileId={null}
        activeFolderId={null}
        onSelectFile={noop}
        onSelectFolder={noop}
        onRename={noop}
        presence={{ 7: 1, 3: 1 }}
        names={{ 7: "bob", 3: "alice" }}
        me={3}
      />
    );

    expect(screen.getByLabelText("Here: bob")).toBeInTheDocument();
    expect(screen.getByText("B")).toBeInTheDocument();
  });

  it("shows a project-root row that is highlighted when no folder is selected", () => {
    const onSelectRoot = vi.fn();
    const fo = folder({ id: 10, name: "src", parent_folder_id: null });
    const { rerender } = render(
      <FileTree files={[]} folders={[fo]} activeFileId={null} activeFolderId={null}
        onSelectFile={noop} onSelectFolder={noop} onRename={noop} presence={{}} onSelectRoot={onSelectRoot} />
    );
    const root = screen.getByText(/project root/);
    expect(root).toHaveClass("active");
    fireEvent.click(root);
    expect(onSelectRoot).toHaveBeenCalled();

    rerender(
      <FileTree files={[]} folders={[fo]} activeFileId={null} activeFolderId={10}
        onSelectFile={noop} onSelectFolder={noop} onRename={noop} presence={{}} onSelectRoot={onSelectRoot} />
    );
    expect(screen.getByText(/project root/)).not.toHaveClass("active");
  });
});
