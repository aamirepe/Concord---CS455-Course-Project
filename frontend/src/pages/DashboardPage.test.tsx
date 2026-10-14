import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DashboardPage from "./DashboardPage";
import { api, setToken } from "../lib/api";
import { ApiError } from "../lib/errors";

const navigateMock = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

const someToken = { access_token: "tok-1", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } };

describe("DashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    navigateMock.mockClear();
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("redirects to /login when no token is present", async () => {
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/login"));
  });

  it("loads and renders projects when a token is present", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([
      { id: 1, owner_id: 1, name: "demo-project", created_at: "now" },
    ]);

    render(<DashboardPage />, { wrapper: MemoryRouter });
    expect(await screen.findByText("demo-project")).toBeInTheDocument();
  });

  it("creates a project and appends it to the list, clearing the input", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    vi.spyOn(api, "createProject").mockResolvedValue({ id: 2, owner_id: 1, name: "new-project", created_at: "now" });

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled());

    const input = screen.getByPlaceholderText("Project name...") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "new-project" } });
    fireEvent.click(screen.getByRole("button", { name: /create project/i }));

    await waitFor(() => expect(screen.getByText("new-project")).toBeInTheDocument());
    expect(input.value).toBe("");
  });

  it("joinById calls api.joinProject with a numeric id and navigates on success", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    vi.spyOn(api, "joinProject").mockResolvedValue({ status: "success", role: "viewer" });

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Project ID"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: /join project/i }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/projects/42"));
    expect(api.joinProject).toHaveBeenCalledWith(42);
  });

  it("joinById shows the error and does not navigate on failure", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    vi.spyOn(api, "joinProject").mockRejectedValue(new ApiError(404, "Not Found", '{"detail":"Project not found"}'));

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Project ID"), { target: { value: "999" } });
    fireEvent.click(screen.getByRole("button", { name: /join project/i }));

    expect(await screen.findByText(/Project not found\. It may have been deleted/)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalledWith("/projects/999");
  });

  it("a 403 on join offers Request access instead of an alert, and sends the request", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    vi.spyOn(api, "joinProject").mockRejectedValue(new ApiError(403, "Forbidden", "not a member"));
    vi.spyOn(api, "requestJoin").mockResolvedValue({
      id: 1, project_id: 7, user: someToken.user, status: "pending", created_at: "now", decided_at: null,
    });

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Project ID"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /join project/i }));

    fireEvent.click(await screen.findByRole("button", { name: /request access/i }));
    await waitFor(() => expect(api.requestJoin).toHaveBeenCalledWith(7));
    expect(await screen.findByRole("status")).toHaveTextContent(/access requested for project #7/i);
    expect(window.alert).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalledWith("/projects/7");
  });

  it("a duplicate access request (409) tells the user it is already pending", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([]);
    vi.spyOn(api, "joinProject").mockRejectedValue(new ApiError(403, "Forbidden", "not a member"));
    vi.spyOn(api, "requestJoin").mockRejectedValue(new ApiError(409, "Conflict", "already pending"));

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Project ID"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /join project/i }));
    fireEvent.click(await screen.findByRole("button", { name: /request access/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/already have a pending request/i);
  });

  it("addMember calls api.addProjectMember with a numeric user id and the selected role", async () => {
    setToken(someToken);
    vi.spyOn(api, "listProjects").mockResolvedValue([{ id: 1, owner_id: 1, name: "demo-project", created_at: "now" }]);
    vi.spyOn(api, "addProjectMember").mockResolvedValue({ message: "added" });

    render(<DashboardPage />, { wrapper: MemoryRouter });
    await screen.findByText("demo-project");

    fireEvent.change(screen.getByPlaceholderText("User ID"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(api.addProjectMember).toHaveBeenCalledWith(1, 7, "VIEWER"));
  });
});
