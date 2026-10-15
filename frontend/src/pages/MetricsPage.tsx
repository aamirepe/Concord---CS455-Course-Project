import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function MetricsPage() {
  const [summary, setSummary] = useState<any>(null);
  const [recent, setRecent] = useState<any[]>([]);

  useEffect(() => {
    api.metricsSummary().then(setSummary).catch(console.error);
    api.metricsRecent().then((r) => setRecent(r.events)).catch(console.error);
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <h1>Metrics</h1>
      <h3>Execution summary</h3>
      {summary ? (
        <pre>{JSON.stringify(summary, null, 2)}</pre>
      ) : (
        <p>loading…</p>
      )}
      <h3>Recent events</h3>
      {recent.length === 0 ? <p>none yet</p> : (
        <table style={{ borderCollapse: "collapse" }}>
          <thead><tr><th align="left">id</th><th align="left">event</th><th align="left">at</th></tr></thead>
          <tbody>
            {recent.map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td>{e.event_type}</td>
                <td>{e.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
