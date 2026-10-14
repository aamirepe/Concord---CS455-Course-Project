// Open editor tabs, remembered per project in this browser (a convenience:
// losing them just means starting with one tab).

export type SavedTabs = { open: number[]; active: number | null };

const key = (projectId: number) => `concord:tabs:${projectId}`;

export function loadTabs(projectId: number): SavedTabs | null {
  try {
    const raw = localStorage.getItem(key(projectId));
    if (!raw) return null;
    const data = JSON.parse(raw) as SavedTabs;
    if (!Array.isArray(data.open)) return null;
    return { open: data.open.filter((n) => Number.isInteger(n)), active: Number.isInteger(data.active) ? data.active : null };
  } catch {
    return null;
  }
}

export function saveTabs(projectId: number, tabs: SavedTabs): void {
  try {
    localStorage.setItem(key(projectId), JSON.stringify(tabs));
  } catch {
    /* private mode or storage full: tabs just aren't remembered */
  }
}

/** The tab to show after closing ``closed``: the one to its right, else its left. */
export function neighbourAfterClose(open: number[], closed: number): number | null {
  const i = open.indexOf(closed);
  const rest = open.filter((id) => id !== closed);
  if (!rest.length) return null;
  return rest[Math.min(Math.max(i, 0), rest.length - 1)];
}
