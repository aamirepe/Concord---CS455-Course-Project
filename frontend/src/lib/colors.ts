const PALETTE = ["#58a6ff", "#3fb950", "#d29922", "#a371f7", "#ff7b72", "#ffdf5d", "#79c0ff", "#56d4dd"];

export function userColor(userId: number): string {
  return PALETTE[Math.abs(userId) % PALETTE.length];
}
