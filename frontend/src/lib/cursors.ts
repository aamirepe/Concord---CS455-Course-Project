type PeerState = { username?: unknown; color?: unknown } | null | undefined;

/**
 * CSS giving each other person's cursor their colour and a name tag. Names
 * and colours come from other clients, so only safe characters get through.
 */
export function cursorStyles(states: Map<number, PeerState>, ownClientId: number): string {
  const rules: string[] = [];
  states.forEach((state, clientId) => {
    if (clientId === ownClientId || !state?.username || !Number.isInteger(clientId)) return;
    const color = typeof state.color === "string" && /^#[0-9a-fA-F]{6}$/.test(state.color) ? state.color : "#58a6ff";
    const name = String(state.username).replace(/[^A-Za-z0-9 _.-]/g, "").slice(0, 24) || "someone";
    rules.push(
      `.yRemoteSelection-${clientId} { background-color: ${color}40; }`,
      `.yRemoteSelectionHead-${clientId} { border-left-color: ${color}; }`,
      `.yRemoteSelectionHead-${clientId}::after { content: "${name}"; background: ${color}; }`,
    );
  });
  return rules.join("\n");
}
