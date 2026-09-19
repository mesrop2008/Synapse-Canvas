import { Extension } from '@tiptap/react';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { EditorView } from '@tiptap/pm/view';

import type { PeerPresence } from '../types/api';

const key = new PluginKey<DecorationSet>('remoteCursors');

/** Peers arrive through a transaction rather than a React prop: decorations are
 *  ProseMirror state, and the view is the only thing that can change it. */
const PEERS = 'remoteCursors:peers';

function caret(peer: PeerPresence): HTMLElement {
  const wrapper = document.createElement('span');
  wrapper.className = 'remote-cursor';
  wrapper.style.setProperty('--peer-color', peer.color);

  const label = document.createElement('span');
  label.className = 'remote-cursor-label';
  label.textContent = peer.name;
  wrapper.appendChild(label);
  return wrapper;
}

function decorationsFor(
  peers: PeerPresence[],
  docSize: number,
): Decoration[] {
  const decorations: Decoration[] = [];

  for (const peer of peers) {
    if (peer.anchor === null || peer.head === null) continue;

    // Positions were computed against the peer's copy of the document, which
    // may be a version ahead or behind this one. Clamping keeps a stale cursor
    // from throwing rather than just sitting in the wrong place for a moment.
    const anchor = Math.min(Math.max(peer.anchor, 0), docSize);
    const head = Math.min(Math.max(peer.head, 0), docSize);

    if (anchor !== head) {
      decorations.push(
        Decoration.inline(Math.min(anchor, head), Math.max(anchor, head), {
          class: 'remote-selection',
          style: `--peer-color: ${peer.color}`,
        }),
      );
    }
    decorations.push(
      Decoration.widget(head, () => caret(peer), {
        side: 10, // after the local caret, so the two do not swap places
        key: `peer-${peer.user_id}-${head}`,
      }),
    );
  }
  return decorations;
}

export function setRemotePeers(view: EditorView, peers: PeerPresence[]): void {
  view.dispatch(view.state.tr.setMeta(PEERS, peers));
}

export const RemoteCursors = Extension.create({
  name: 'remoteCursors',

  addProseMirrorPlugins() {
    return [
      new Plugin<DecorationSet>({
        key,
        state: {
          init: () => DecorationSet.empty,
          apply(transaction, current, _old, state) {
            const peers = transaction.getMeta(PEERS) as
              | PeerPresence[]
              | undefined;
            if (peers) {
              return DecorationSet.create(
                state.doc,
                decorationsFor(peers, state.doc.content.size),
              );
            }
            // Not a peer update: carry the decorations through whatever the
            // document did, so a cursor does not lag a keystroke behind.
            return current.map(transaction.mapping, transaction.doc);
          },
        },
        props: {
          decorations: (state) => key.getState(state),
        },
      }),
    ];
  },
});
