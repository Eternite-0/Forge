const names = ["yellow", "pink", "green"] as const;

/** Keep an agent's flower identity stable across polling, state changes and new runs. */
export function agentAppearance(identity: string) {
  let hash = 0;
  for (let index = 0; index < identity.length; index++) hash = (Math.imul(31, hash) + identity.charCodeAt(index)) | 0;
  const shape = ((hash % names.length) + names.length) % names.length;
  return { name: names[shape], shape };
}

/** Use the native flower assets, separate from the changing execution status. */
export default function AgentAvatar({ identity, className = "", size = 16 }: { identity: string; className?: string; size?: number }) {
  const { name, shape } = agentAppearance(identity);
  return <picture className={`agent-avatar ${className}`.trim()} style={{ width: size, height: size }} aria-hidden="true" data-agent-shape={shape}>
    <source media="(prefers-color-scheme: dark)" srcSet={`/agent-avatars/${name}-dark.svg`} />
    <img src={`/agent-avatars/${name}-light.svg`} width="100%" height="100%" alt="" aria-hidden="true" draggable={false} />
  </picture>;
}
