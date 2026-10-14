import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SignupPage from "./SignupPage";
import { api, getToken } from "../lib/api";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

describe("SignupPage", () => {
  beforeEach(() => {
    localStorage.clear();
    navigateMock.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("disables the submit button until username, email, and password are all filled", () => {
    render(<SignupPage />, { wrapper: MemoryRouter });
    const button = screen.getByRole("button", { name: /sign up/i });
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("email"), { target: { value: "a@example.com" } });
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/password/), { target: { value: "password1" } });
    expect(button).toBeEnabled();
  });

  it("on success, stores the token and navigates to /", async () => {
    const token = { access_token: "tok-1", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } };
    vi.spyOn(api, "signup").mockResolvedValue(token);

    render(<SignupPage />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByPlaceholderText(/password/), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/"));
    expect(api.signup).toHaveBeenCalledWith({ username: "alice", email: "a@example.com", password: "password1" });
    expect(getToken()).toBe("tok-1");
  });

  it("on failure, renders the error and does not navigate", async () => {
    vi.spyOn(api, "signup").mockRejectedValue(new Error("409 Conflict: username taken"));

    render(<SignupPage />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("email"), { target: { value: "a@example.com" } });
    fireEvent.change(screen.getByPlaceholderText(/password/), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => expect(screen.getByText(/username taken/)).toBeInTheDocument());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
