import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getToken, getUser, setToken } from "./api";
import { ApiError, UNAUTHORIZED_EVENT } from "./errors";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status === 200 ? "OK" : "Error",
    headers: { "Content-Type": "application/json" },
  });
}

function emptyResponse(status: number): Response {
  return new Response(null, { status, statusText: "No Content" });
}

describe("api.ts", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("token/user storage", () => {
    it("getToken/getUser return null when nothing stored", () => {
      expect(getToken()).toBeNull();
      expect(getUser()).toBeNull();
    });

    it("setToken stores the access token and user, getToken/getUser read them back", () => {
      const token = { access_token: "abc123", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } };
      setToken(token);
      expect(getToken()).toBe("abc123");
      expect(getUser()).toEqual(token.user);
    });

    it("setToken(null) clears both stored keys", () => {
      setToken({ access_token: "abc123", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } });
      setToken(null);
      expect(getToken()).toBeNull();
      expect(getUser()).toBeNull();
    });
  });

  describe("req() request construction", () => {
    it("sends Authorization header when a token is present", async () => {
      setToken({ access_token: "tok-1", token_type: "bearer", user: { id: 1, username: "a", email: "a@example.com" } });
      fetchMock.mockResolvedValueOnce(jsonResponse([]));

      await api.listProjects();

      const [, init] = fetchMock.mock.calls[0];
      const headers = init.headers as Headers;
      expect(headers.get("Authorization")).toBe("Bearer tok-1");
    });

    it("omits Authorization header when no token is present", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse([]));

      await api.listProjects();

      const [, init] = fetchMock.mock.calls[0];
      const headers = init.headers as Headers;
      expect(headers.get("Authorization")).toBeNull();
    });

    it("throws with status and body text on a non-2xx response", async () => {
      fetchMock.mockResolvedValueOnce(new Response("bad credentials", { status: 401, statusText: "Unauthorized" }));

      await expect(api.login({ username: "x", password: "y" })).rejects.toThrow("401 Unauthorized: bad credentials");
    });

    it("logs out and announces an expired session on a 401 from a protected endpoint", async () => {
      setToken({ access_token: "old", token_type: "bearer", user: { id: 1, username: "a", email: "a@example.com" } });
      const expired = vi.fn();
      window.addEventListener(UNAUTHORIZED_EVENT, expired);
      fetchMock.mockResolvedValueOnce(new Response('{"detail":"Could not validate credentials"}', { status: 401, statusText: "Unauthorized" }));

      const err = await api.listProjects().catch((e) => e);
      window.removeEventListener(UNAUTHORIZED_EVENT, expired);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(401);
      expect(getToken()).toBeNull();
      expect(expired).toHaveBeenCalledTimes(1);
    });

    it("passes on the server's detail and Retry-After", async () => {
      fetchMock.mockResolvedValueOnce(new Response('{"detail":"slow down"}', { status: 429, statusText: "Too Many Requests", headers: { "Retry-After": "7" } }));
      const err = await api.listProjects().catch((e) => e);
      expect(err.detail).toBe("slow down");
      expect(err.retryAfter).toBe(7);
    });

    it("reports an unreachable server as status 0", async () => {
      fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
      const err = await api.listProjects().catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(0);
    });

    it("returns undefined for a 204 No Content response", async () => {
      fetchMock.mockResolvedValueOnce(emptyResponse(204));

      const result = await api.removeProjectMember(1, 2);
      expect(result).toBeUndefined();
    });

    it("parses and returns the JSON body on success", async () => {
      const project = { id: 1, owner_id: 1, name: "demo", created_at: "2026-01-01T00:00:00Z" };
      fetchMock.mockResolvedValueOnce(jsonResponse(project));

      const result = await api.getProject(1);
      expect(result).toEqual(project);
    });
  });

  describe("endpoint coverage", () => {
    it("signup posts to /auth/signup with the payload", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ access_token: "t", token_type: "bearer", user: { id: 1, username: "a", email: "a@example.com" } }));
      await api.signup({ username: "a", email: "a@example.com", password: "pw" });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/auth/signup");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ username: "a", email: "a@example.com", password: "pw" });
    });

    it("login posts to /auth/login with the payload", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ access_token: "t", token_type: "bearer", user: { id: 1, username: "a", email: "a@example.com" } }));
      await api.login({ username: "a", password: "pw" });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/auth/login");
      expect(JSON.parse(init.body)).toEqual({ username: "a", password: "pw" });
    });

    it("createProject posts the project name", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, owner_id: 1, name: "demo", created_at: "now" }));
      await api.createProject("demo");
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ name: "demo" });
    });

    it("joinProject returns {status, role} shape (regression: no longer a phantom 'session' field)", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ status: "success", role: "viewer" }));
      const result = await api.joinProject(5);
      expect(result).toEqual({ status: "success", role: "viewer" });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/5/join");
      expect(init.method).toBe("POST");
    });

    it("assistant methods hit /assist, /assist/status and /assist/config", async () => {
      fetchMock.mockImplementation(async () => jsonResponse({}));
      await api.requestAssistant(4, {
        prompt: "fix it", mode: "fix", file_id: 7, history: [{ role: "user", text: "hi" }],
      });
      await api.getAssistStatus(4);
      await api.saveAssistConfig(4, { provider: "mistral", api_key: "sk-1234567890" });

      const calls = fetchMock.mock.calls.map(([url, init]) => [url.replace(/^https?:\/\/[^/]+/, ""), init.method ?? "GET"]);
      expect(calls).toEqual([
        ["/projects/4/assist", "POST"],
        ["/projects/4/assist/status", "GET"],
        ["/projects/4/assist/config", "POST"],
      ]);
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        prompt: "fix it", mode: "fix", file_id: 7, history: [{ role: "user", text: "hi" }],
      });
      expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ provider: "mistral", api_key: "sk-1234567890" });
    });

    it("join-request methods hit the request/list/approve/reject routes", async () => {
      fetchMock.mockImplementation(async () => jsonResponse({}));
      await api.requestJoin(3);
      await api.listJoinRequests(3);
      await api.approveJoinRequest(3, 9, "editor");
      await api.rejectJoinRequest(3, 10);

      const calls = fetchMock.mock.calls.map(([url, init]) => [url.replace(/^https?:\/\/[^/]+/, ""), init.method ?? "GET"]);
      expect(calls).toEqual([
        ["/projects/3/join-requests", "POST"],
        ["/projects/3/join-requests", "GET"],
        ["/projects/3/join-requests/9/approve", "POST"],
        ["/projects/3/join-requests/10/reject", "POST"],
      ]);
      expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ role: "editor" });
    });

    it("addProjectMember sends a numeric user_id and role", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ message: "added" }));
      await api.addProjectMember(1, 42, "editor");
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/1/members");
      expect(JSON.parse(init.body)).toEqual({ user_id: 42, role: "editor" });
    });

    it("listProjectMembers GETs the members endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse([]));
      await api.listProjectMembers(1);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/members");
    });

    it("removeProjectMember issues a DELETE", async () => {
      fetchMock.mockResolvedValueOnce(emptyResponse(204));
      await api.removeProjectMember(1, 2);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/1/members/2");
      expect(init.method).toBe("DELETE");
    });

    it("listFiles/listFolders GET the right paths", async () => {
      fetchMock.mockImplementation(async () => jsonResponse([]));
      await api.listFiles(1);
      await api.listFolders(1);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/files");
      expect(fetchMock.mock.calls[1][0]).toContain("/projects/1/folders");
    });

    it("createFolder posts name and parent_id", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, project_id: 1, parent_folder_id: null, name: "src", created_at: "now" }));
      await api.createFolder(1, "src", null);
      const [, init] = fetchMock.mock.calls[0];
      expect(JSON.parse(init.body)).toEqual({ name: "src", parent_id: null });
    });

    it("updateFolder PATCHes the payload", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 2, project_id: 1, parent_folder_id: null, name: "renamed", created_at: "now" }));
      await api.updateFolder(1, 2, { name: "renamed" });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/1/folders/2");
      expect(init.method).toBe("PATCH");
    });

    it("createFile posts path and folder_id", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" }));
      await api.createFile(1, "main.py", null);
      const [, init] = fetchMock.mock.calls[0];
      expect(JSON.parse(init.body)).toEqual({ path: "main.py", folder_id: null });
    });

    it("getFileContent GETs the content endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ file_id: 1, text: "print(1)", version: 3 }));
      const result = await api.getFileContent(1, 1);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/files/1/content");
      expect(result.version).toBe(3);
    });

    it("updateFile PATCHes the payload", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, project_id: 1, folder_id: null, path: "renamed.py", language: "python", created_at: "now" }));
      await api.updateFile(1, 1, { path: "renamed.py" });
      const [, init] = fetchMock.mock.calls[0];
      expect(init.method).toBe("PATCH");
    });

    it("run posts file_id to the executions endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, project_id: 1, file_id: 1, status: "queued", exit_code: null, stdout: "", stderr: "", queued_at: "now", started_at: null, finished_at: null, worker_id: null }));
      await api.run(1, 1);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/1/executions");
      expect(JSON.parse(init.body)).toEqual({ file_id: 1 });
    });

    it("listExecutions GETs the executions endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse([]));
      await api.listExecutions(1);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/executions");
    });

    it("cancelExecution posts to the cancel endpoint", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1, project_id: 1, file_id: 1, status: "cancelled", exit_code: null, stdout: "", stderr: "", queued_at: "now", started_at: null, finished_at: null, worker_id: null }));
      await api.cancelExecution(1, 9);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/executions/9/cancel");
    });

    it("listBreakpoints GETs with a file_id query param", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse([]));
      await api.listBreakpoints(1, 3);
      expect(fetchMock.mock.calls[0][0]).toContain("/projects/1/breakpoints?file_id=3");
    });

    it("setBreakpoints PUTs a file's lines", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse([]));
      await api.setBreakpoints(1, 3, [10, 12]);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toContain("/projects/1/files/3/breakpoints");
      expect(init.method).toBe("PUT");
      expect(JSON.parse(init.body)).toEqual({ lines: [10, 12] });
    });

    it("debug, debugState and debugCommand call the debugger endpoints", async () => {
      fetchMock.mockImplementation(async () => jsonResponse({}));
      await api.debug(1, 3);
      await api.debugState(1);
      await api.debugCommand(1, 9, "variables", { ref: 4 });
      const [runUrl, runInit] = fetchMock.mock.calls[0];
      expect(runUrl).toContain("/projects/1/executions");
      expect(JSON.parse(runInit.body)).toEqual({ file_id: 3, debug: true });
      expect(fetchMock.mock.calls[1][0]).toContain("/projects/1/debug");
      const [cmdUrl, cmdInit] = fetchMock.mock.calls[2];
      expect(cmdUrl).toContain("/projects/1/debug/9/command");
      expect(JSON.parse(cmdInit.body)).toEqual({ command: "variables", ref: 4 });
    });

    it("metricsSummary/metricsRecent GET their endpoints", async () => {
      fetchMock.mockImplementation(async () => jsonResponse({}));
      await api.metricsSummary();
      await api.metricsRecent();
      expect(fetchMock.mock.calls[0][0]).toContain("/metrics/executions/summary");
      expect(fetchMock.mock.calls[1][0]).toContain("/metrics/events/recent");
    });
  });

  describe("removed dead 'session' endpoints (regression guard)", () => {
    it("createSession and joinSession no longer exist on the api object", () => {
      expect((api as unknown as Record<string, unknown>).createSession).toBeUndefined();
      expect((api as unknown as Record<string, unknown>).joinSession).toBeUndefined();
    });
  });
});
