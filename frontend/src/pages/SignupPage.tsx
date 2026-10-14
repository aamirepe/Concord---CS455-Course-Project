import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../lib/api";
import { friendlyError } from "../lib/errors";

export default function SignupPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setLoading(true);
    try {
      const tok = await api.signup({ username, email, password });
      setToken(tok);
      navigate("/");
    } catch (e) {
      setErr(friendlyError(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "10vh auto", textAlign: "center" }}>
      <h2 style={{ marginBottom: 24 }}>Create an account</h2>
      <form onSubmit={onSubmit} style={{ display: "grid", gap: 8 }}>
        <input
          placeholder="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{ padding: "8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg)" }}
        />
        <input
          type="email"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{ padding: "8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg)" }}
        />
        <input
          type="password"
          placeholder="password (min 8)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ padding: "8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg)" }}
        />
        <button type="submit" disabled={loading || !username || !email || !password}>
          {loading ? "Signing up..." : "Sign up"}
        </button>
        {err && <div style={{ color: "var(--bad)", marginTop: 8, fontSize: 14 }}>{err}</div>}
      </form>
      <p style={{ marginTop: 16 }}>Already have an account? <a href="/login">Log in</a></p>
    </div>
  );
}
