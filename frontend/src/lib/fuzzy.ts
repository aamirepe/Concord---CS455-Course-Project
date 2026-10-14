// Quick-open matching: the query's characters must appear in order in the
// path. Matches in the file name, at word starts and in a row score higher.

export type FuzzyMatch = { score: number; positions: number[] };

export function fuzzyMatch(query: string, path: string): FuzzyMatch | null {
  const q = query.replace(/\s+/g, "").toLowerCase();
  if (!q) return { score: 0, positions: [] };
  const p = path.toLowerCase();
  const nameStart = path.lastIndexOf("/") + 1;

  // Prefer a match inside the file name: try matching there first.
  const inName = matchFrom(q, p, nameStart);
  const positions = inName ?? matchFrom(q, p, 0);
  if (!positions) return null;

  let score = 0;
  positions.forEach((pos, i) => {
    score += 1;
    if (pos >= nameStart) score += 2;
    if (pos === 0 || "/._-".includes(path[pos - 1]) || (path[pos] !== p[pos] && path[pos - 1] === p[pos - 1])) score += 3;
    if (i > 0 && positions[i - 1] === pos - 1) score += 4;
  });
  if (p.slice(nameStart).startsWith(q)) score += 10;
  score -= path.length * 0.01; // shorter paths first when otherwise equal
  return { score, positions };
}

function matchFrom(q: string, p: string, start: number): number[] | null {
  const out: number[] = [];
  let j = start;
  for (const ch of q) {
    j = p.indexOf(ch, j);
    if (j === -1) return null;
    out.push(j);
    j += 1;
  }
  return out;
}

/** Files matching ``query``, best first; with no query, ``recent`` first then by path. */
export function rankFiles<T extends { id: number; path: string }>(files: T[], query: string, recent: number[] = []): { file: T; positions: number[] }[] {
  if (!query.trim()) {
    const order = (f: T) => {
      const i = recent.indexOf(f.id);
      return i === -1 ? recent.length : i;
    };
    return [...files]
      .sort((a, b) => order(a) - order(b) || a.path.localeCompare(b.path))
      .map((file) => ({ file, positions: [] }));
  }
  return files
    .map((file) => ({ file, m: fuzzyMatch(query, file.path) }))
    .filter((x): x is { file: T; m: FuzzyMatch } => x.m !== null)
    .sort((a, b) => b.m.score - a.m.score || a.file.path.localeCompare(b.file.path))
    .map(({ file, m }) => ({ file, positions: m.positions }));
}
