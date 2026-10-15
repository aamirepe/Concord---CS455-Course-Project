import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import SearchPanel, { SearchPanelHandle } from "./SearchPanel";
import { api } from "../lib/api";
import { ApiError } from "../lib/errors";

const result = {
  query: "total",
  total: 2,
  truncated: false,
  files: [{ file_id: 10, path: "main.py", matches: [
    { line: 1, column: 5, length: 5, preview: "def total(xs):", preview_column: 4 },
    { line: 3, column: 12, length: 5, preview: "    return total", preview_column: 11 },
  ] }],
};

describe("SearchPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("searches after you stop typing and opens a result at its line", async () => {
    const search = vi.spyOn(api, "search").mockResolvedValue(result);
    const onOpen = vi.fn();
    const ref = createRef<SearchPanelHandle>();
    render(<SearchPanel ref={ref} projectId={1} onOpen={onOpen} />);
    act(() => ref.current!.focus());
    expect(screen.getByLabelText("Search all files")).toHaveFocus();

    fireEvent.change(screen.getByLabelText("Search all files"), { target: { value: "total" } });
    fireEvent.click(screen.getByTitle("Match case"));
    expect(await screen.findByText("2 results in 1 file")).toBeInTheDocument();
    expect(search).toHaveBeenLastCalledWith(1, "total", { case: true, word: false });
    expect(search).toHaveBeenCalledTimes(1); // debounced

    fireEvent.click(screen.getByTitle("Line 3"));
    expect(onOpen).toHaveBeenCalledWith(10, 3, 12, 5);
    expect(screen.getAllByText("total", { selector: "mark" })).toHaveLength(2);

    fireEvent.click(screen.getByTitle("main.py"));
    expect(screen.queryByTitle("Line 3")).toBeNull(); // collapsed
    fireEvent.click(screen.getByTitle("main.py"));
    expect(screen.getByTitle("Line 3")).toBeInTheDocument();
  });

  it("shows errors and clears results when the box is emptied", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(0, "", ""));
    render(<SearchPanel projectId={1} onOpen={vi.fn()} />);
    const box = screen.getByLabelText("Search all files");
    fireEvent.change(box, { target: { value: "x" } });
    expect(await screen.findByText(/can't reach the server/i)).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "" } });
    await waitFor(() => expect(screen.queryByText(/can't reach the server/i)).toBeNull());
  });

  it("stops saying Searching when the box is cleared while a request is in flight", async () => {
    let finish: (r: typeof result) => void = () => {};
    vi.spyOn(api, "search").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<SearchPanel projectId={1} onOpen={vi.fn()} />);
    const box = screen.getByLabelText("Search all files");
    fireEvent.change(box, { target: { value: "total" } });
    expect(await screen.findByText("Searching…")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "" } });
    await waitFor(() => expect(screen.queryByText("Searching…")).toBeNull());
    await act(async () => { finish(result); });
    expect(screen.queryByText("Searching…")).toBeNull();
    expect(screen.queryByText("2 results in 1 file")).toBeNull();
  });

  it("says when there are no or too many results", async () => {
    vi.spyOn(api, "search")
      .mockResolvedValueOnce({ query: "q", total: 0, truncated: false, files: [] })
      .mockResolvedValueOnce({ ...result, total: 500, truncated: true });
    render(<SearchPanel projectId={1} onOpen={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Search all files"), { target: { value: "q" } });
    expect(await screen.findByText("No results")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Whole word"));
    expect(await screen.findByText(/500\+ results in 1 file \(showing the first 500\)/)).toBeInTheDocument();
  });
});
