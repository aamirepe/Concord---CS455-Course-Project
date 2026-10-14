import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Toasts from "./Toasts";
import AuthRedirect from "./AuthRedirect";
import { showError, toast, UNAUTHORIZED_EVENT, ApiError } from "../lib/errors";

describe("Toasts", () => {
  it("renders errors as alerts and can be dismissed", () => {
    render(<Toasts />);
    act(() => { showError(new ApiError(403, "Forbidden", "")); });
    expect(screen.getByRole("alert")).toHaveTextContent("You don't have permission to do that.");
    act(() => { toast("Copied", "success"); });
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
    fireEvent.click(screen.getAllByRole("button", { name: /dismiss/i })[0]);
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("AuthRedirect", () => {
  it("goes to the login page when the session expires", () => {
    render(
      <MemoryRouter initialEntries={["/projects/1"]}>
        <AuthRedirect />
        <Routes>
          <Route path="/projects/1" element={<p>project</p>} />
          <Route path="/login" element={<p>login page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("project")).toBeInTheDocument();
    act(() => { window.dispatchEvent(new Event(UNAUTHORIZED_EVENT)); });
    expect(screen.getByText("login page")).toBeInTheDocument();
  });
});
