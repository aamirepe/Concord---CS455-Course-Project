import { FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, setToken } from "../lib/api";
import { ApiError, friendlyError } from "../lib/errors";

export default function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const expired = params.get("expired") === "1";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setLoading(true);
    try {
      const tok = await api.login({ username, password });
      setToken(tok);
      navigate("/");
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 401 ? "Wrong username or password." : friendlyError(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "10vh auto", textAlign: "center" }}>
      <h2 style={{ marginBottom: 24 }}>Log in to Concord</h2>
      {expired && !err && <div className="notice-banner" style={{ marginBottom: 12 }}>Your session expired. Please log in again.</div>}
      <form onSubmit={onSubmit} style={{ display: "grid", gap: 8 }}>
        <input
          placeholder="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{ padding: "8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg)" }}
        />
        <input
          type="password"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ padding: "8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg)" }}
        />
        <button type="submit" disabled={loading || !username || !password}>
          {loading ? "Logging in..." : "Log in"}
        </button>
        {err && <div style={{ color: "var(--bad)", marginTop: 8, fontSize: 14 }}>{err}</div>}
      </form>
      <p style={{ marginTop: 16 }}>No account? <a href="/signup">Sign up</a></p>
    </div>
  );
}
