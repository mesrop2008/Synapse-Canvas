import { useI18n } from '../hooks/useI18n';
import type { PeerPresence } from '../types/api';

/** First letter of each of the first two words, so "Ada Lovelace" reads AL and
 *  a one-word name still gets something. */
function initials(name: string): string {
  const letters = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => [...word][0] ?? '')
    .join('');
  return letters.toUpperCase() || '?';
}

export function PeerList({ peers }: { peers: PeerPresence[] }) {
  const { t } = useI18n();
  if (peers.length === 0) return null;

  return (
    <div
      className="peer-list"
      aria-label={t('document.peers.label', { count: peers.length })}
    >
      {peers.map((peer) => (
        <span
          key={peer.user_id}
          className="peer-chip"
          style={{ '--peer-color': peer.color } as React.CSSProperties}
          title={peer.name}
        >
          {initials(peer.name)}
        </span>
      ))}
    </div>
  );
}
