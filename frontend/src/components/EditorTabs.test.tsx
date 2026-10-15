import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import EditorTabs from "./EditorTabs";
import { FileMeta } from "../lib/api";

const file = (id: number, path: string): FileMeta => ({ id, project_id: 1, folder_id: null, path, language: "python", created_at: "now" });

describe("EditorTabs", () => {
  it("selects, closes (× or middle click) and shows changes and people", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    const tabs = [file(1, "main.py"), file(2, "lib/util.py")];
    render(
      <EditorTabs tabs={tabs} activeId={1} onSelect={onSelect} onClose={onClose}
        modified={{ 2: "bob" }} presence={{ 5: 2, 9: 1 }} names={{ 5: "bob", 9: "me" }} me={9} />,
    );
    expect(screen.getByRole("tab", { name: /main\.py/ })).toHaveAttribute("aria-selected", "true");
    const util = screen.getByRole("tab", { name: /util\.py/ });
    expect(util).toHaveAttribute("title", "lib/util.py");
    expect(util.querySelector(".modified-dot")).toBeTruthy();
    expect(screen.getByLabelText("Here: bob")).toBeInTheDocument();
    expect(screen.queryByLabelText("Here: me")).toBeNull();

    fireEvent.click(util);
    expect(onSelect).toHaveBeenCalledWith(tabs[1]);
    fireEvent.click(screen.getByLabelText("Close main.py"));
    expect(onClose).toHaveBeenCalledWith(tabs[0]);
    expect(onSelect).toHaveBeenCalledTimes(1);
    fireEvent(util, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    expect(onClose).toHaveBeenCalledWith(tabs[1]);
  });

  it("renders nothing without tabs", () => {
    const { container } = render(
      <EditorTabs tabs={[]} activeId={null} onSelect={vi.fn()} onClose={vi.fn()} modified={{}} presence={{}} names={{}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
