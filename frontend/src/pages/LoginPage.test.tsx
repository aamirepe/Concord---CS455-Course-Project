import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "./LoginPage";
import { api, getToken } from "../lib/api";
import { ApiError } from "../lib/errors";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

describe("LoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
    navigateMock.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("disables the submit button until both fields are filled", () => {
    render(<LoginPage />, { wrapper: MemoryRouter });
    expect(screen.getByRole("button", { name: /log in/i })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    expect(screen.getByRole("button", { name: /log in/i })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "secret" } });
    expect(screen.getByRole("button", { name: /log in/i })).toBeEnabled();
  });

  it("on success, stores the token and navigates to /", async () => {
    const token = { access_token: "tok-1", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } };
    vi.spyOn(api, "login").mockResolvedValue(token);

    render(<LoginPage />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /log in/i }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/"));
    expect(api.login).toHaveBeenCalledWith({ username: "alice", password: "secret" });
    expect(getToken()).toBe("tok-1");
  });

  it("on failure, renders the error and does not navigate", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new Error("401 Unauthorized: bad credentials"));

    render(<LoginPage />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /log in/i }));

    await waitFor(() => expect(screen.getByText(/bad credentials/)).toBeInTheDocument());
    expect(navigateMock).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it("says the session expired when sent here by an expired token", () => {
    render(
      <MemoryRouter initialEntries={["/login?expired=1"]}>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/your session expired/i)).toBeInTheDocument();
  });

  it("says the password is wrong rather than showing the raw error", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, "Unauthorized", '{"detail":"Invalid credentials"}'));
    render(<LoginPage />, { wrapper: MemoryRouter });
    fireEvent.change(screen.getByPlaceholderText("username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByPlaceholderText("password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: /log in/i }));
    expect(await screen.findByText("Wrong username or password.")).toBeInTheDocument();
  });
});
