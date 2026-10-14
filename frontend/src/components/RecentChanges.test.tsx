import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RecentChanges from "./RecentChanges";
import { timeAgo } from "../lib/time";

describe("timeAgo", () => {
  const now = Date.parse("2026-09-27T12:00:00Z");
  it("rounds to the largest unit", () => {
    expect(timeAgo("2026-09-27T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-09-27T11:55:00Z", now)).toBe("5m ago");
    expect(timeAgo("2026-09-27T09:00:00+00:00", now)).toBe("3h ago");
    expect(timeAgo("2026-09-25T12:00:00Z", now)).toBe("2d ago");
  });
  it("reads a timestamp without a zone as UTC", () => {
    expect(timeAgo("2026-09-27T11:50:00", now)).toBe("10m ago");
  });
});

describe("RecentChanges", () => {
  it("says so when nothing changed yet", () => {
    render(<RecentChanges activity={[]} onOpen={vi.fn()} />);
    expect(screen.getByText("No edits yet.")).toBeInTheDocument();
  });

  it("shows one entry per file with a bullet per author", () => {
    const onOpen = vi.fn();
    render(
      <RecentChanges
        now={Date.parse("2026-09-27T12:00:00Z")}
        onOpen={onOpen}
        activity={[{
          file_id: 3, path: "src/app.js", lines_added: 4, lines_removed: 1, last_changed_at: "2026-09-27T11:58:00Z",
          authors: [{ user_id: 1, username: "alice", lines_added: 4, lines_removed: 1, last_changed_at: "2026-09-27T11:58:00Z" }],
        }]}
      />
    );
    expect(screen.getByText("2m ago")).toBeInTheDocument();
    expect(screen.getByText("alice", { exact: false })).toHaveTextContent("alice +4 −1");
    fireEvent.click(screen.getByRole("button", { name: "app.js" }));
    expect(onOpen).toHaveBeenCalledWith(3);
  });
});
