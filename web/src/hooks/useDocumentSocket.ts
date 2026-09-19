import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { createWsTicket, documentSocketUrl } from '../api/realtime';
import type {
  InitMessage,
  PeerPresence,
  ProseMirrorDoc,
  ServerMessage,
  WorkspaceRole,
} from '../types/api';

/**
 * `deleted` and `unavailable` are terminal: retrying cannot fix either, so the
 * hook stops rather than leaving the user watching a document that is gone
 * promise to reconnect forever.
 */
export type ConnectionState =
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'offline'
  | 'deleted'
  | 'unavailable';

/**
 * Why local state was thrown away. `rejected`: someone else's edit reached the
 * server first. `diverged`: a peer's edit could not be replayed on this copy --
 * either it landed while local work was still outstanding, or its steps did not
 * apply. Both need operational transform to do better than reload.
 */
export type ReloadReason = 'rejected' | 'diverged';

export interface DocumentSocketCallbacks {
  onInit: (init: InitMessage) => void;
  /** Replay a peer's steps. Return false if they do not apply, which forces a
   *  resync rather than leaving two copies quietly different. */
  onRemoteSteps: (steps: unknown[]) => boolean;
  onRemoteContent: (content: ProseMirrorDoc) => void;
  onRemoteTitle: (title: string) => void;
  /** Tell the user their text was replaced. Content follows, from `init` in the
   *  `diverged` case and from `onRemoteContent` in the `rejected` one. */
  onReloaded: (reason: ReloadReason) => void;
}

export interface DocumentSocket {
  state: ConnectionState;
  /** The version the next edit will be based on. */
  version: number;
  role: WorkspaceRole | null;
  self: InitMessage['you'] | null;
  peers: PeerPresence[];
  /** Hand over what changed locally; the hook decides when it goes out. */
  queueEdit: (steps: unknown[], doc: ProseMirrorDoc) => void;
  sendCursor: (anchor: number, head: number) => void;
}

const SEND_DEBOUNCE_MS = 250;
const HEARTBEAT_MS = 10_000;
// Three missed heartbeats, and shorter than the server's own idle timeout, so
// a merely slow socket is not torn down from both ends at once.
const SILENCE_LIMIT_MS = 35_000;
const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 15_000;
// After this many consecutive failures the connection is called offline rather
// than reconnecting: it is still retrying, but the user should stop waiting.
const OFFLINE_AFTER_ATTEMPTS = 3;

/** Half the delay plus up to half again, so a crowd that lost the same server
 *  does not come back in lockstep. */
function backoff(attempt: number): number {
  const ceiling = Math.min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** attempt);
  return ceiling * (0.5 + Math.random() * 0.5);
}

/**
 * Owns one WebSocket for one document.
 *
 * Outbound edits are batched and sent one at a time. The server accepts an edit
 * only against the exact version it holds, so a second one on the wire would be
 * based on a version the first is about to replace, and rejected on arrival.
 *
 * Inbound steps are replayed only when nothing local is outstanding. A peer's
 * steps carry positions computed against the version they were based on;
 * replaying them over unsent local changes would leave this copy agreeing with
 * neither the server nor the peers. When that happens the hook reconnects and
 * takes the server's document instead -- the same trade the server makes when
 * it rejects an edit, and the same cost: those keystrokes are gone.
 * Operational transform is what removes it.
 */
export function useDocumentSocket(
  documentId: string,
  callbacks: DocumentSocketCallbacks,
): DocumentSocket {
  const [state, setState] = useState<ConnectionState>('connecting');
  const [version, setVersion] = useState(0);
  const [self, setSelf] = useState<InitMessage['you'] | null>(null);
  const [peers, setPeers] = useState<PeerPresence[]>([]);

  // Everything the socket touches lives in a ref: the connection outlives any
  // one render, and re-running its effect would drop it.
  const handlers = useRef(callbacks);
  handlers.current = callbacks;

  const socket = useRef<WebSocket | null>(null);
  const versionRef = useRef(0);
  const pending = useRef<{ steps: unknown[]; doc: ProseMirrorDoc } | null>(null);
  const awaitingAck = useRef(false);
  const sendTimer = useRef<number | null>(null);
  const retryTimer = useRef<number | null>(null);
  const heartbeat = useRef<number | null>(null);
  const lastMessageAt = useRef(0);
  const attempts = useRef(0);
  const closed = useRef(false);
  // Bumped on every connect, so a reply from a socket that has been replaced
  // cannot move the state of the one that replaced it.
  const generation = useRef(0);
  const connect = useRef<() => void>(() => {});

  const send = useCallback((message: unknown) => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify(message));
    }
  }, []);

  const flush = useCallback(() => {
    if (sendTimer.current !== null) {
      window.clearTimeout(sendTimer.current);
      sendTimer.current = null;
    }
    if (awaitingAck.current || pending.current === null) return;
    if (socket.current?.readyState !== WebSocket.OPEN) return;

    const batch = pending.current;
    pending.current = null;
    awaitingAck.current = true;
    send({
      type: 'edit',
      base_version: versionRef.current,
      operation: { steps: batch.steps, doc: batch.doc },
    });
  }, [send]);

  const queueEdit = useCallback(
    (steps: unknown[], doc: ProseMirrorDoc) => {
      pending.current = {
        steps: [...(pending.current?.steps ?? []), ...steps],
        doc,
      };
      if (sendTimer.current === null) {
        sendTimer.current = window.setTimeout(flush, SEND_DEBOUNCE_MS);
      }
    },
    [flush],
  );

  const sendCursor = useCallback(
    (anchor: number, head: number) => send({ type: 'cursor', anchor, head }),
    [send],
  );

  const dropSocket = useCallback(() => {
    const live = socket.current;
    socket.current = null;
    if (live) {
      live.onclose = null;
      live.onmessage = null;
      live.onopen = null;
      live.close();
    }
    if (heartbeat.current !== null) {
      window.clearInterval(heartbeat.current);
      heartbeat.current = null;
    }
    awaitingAck.current = false;
  }, []);

  /** Give up for good. Nothing here is recoverable by waiting. */
  const stop = useCallback(
    (reason: 'deleted' | 'unavailable') => {
      closed.current = true;
      generation.current += 1;
      if (sendTimer.current !== null) window.clearTimeout(sendTimer.current);
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
      pending.current = null;
      dropSocket();
      setPeers([]);
      setState(reason);
    },
    [dropSocket],
  );

  const scheduleRetry = useCallback(() => {
    if (closed.current) return;
    attempts.current += 1;
    setState(
      attempts.current >= OFFLINE_AFTER_ATTEMPTS ? 'offline' : 'reconnecting',
    );
    retryTimer.current = window.setTimeout(
      () => connect.current(),
      backoff(attempts.current),
    );
  }, []);

  /** A resync is a reconnect: `init` is the one message carrying a whole
   *  document, so recovery has one path rather than two. */
  const resync = useCallback(() => {
    pending.current = null;
    dropSocket();
    setState('reconnecting');
    connect.current();
  }, [dropSocket]);

  const handle = useCallback(
    (message: ServerMessage) => {
      switch (message.type) {
        case 'init': {
          attempts.current = 0;
          versionRef.current = message.version;
          setVersion(message.version);
          setSelf(message.you);
          setPeers(message.peers);
          setState('live');
          // Nothing local is assumed to have survived the gap.
          pending.current = null;
          awaitingAck.current = false;
          handlers.current.onInit(message);
          return;
        }
        case 'edit_ack': {
          awaitingAck.current = false;
          versionRef.current = message.version;
          setVersion(message.version);
          flush(); // anything typed while that edit was on the wire
          return;
        }
        case 'edit': {
          versionRef.current = message.version;
          setVersion(message.version);

          if (message.operation.title !== undefined) {
            handlers.current.onRemoteTitle(message.operation.title);
          }
          if (message.operation.replace !== undefined) {
            handlers.current.onRemoteContent(message.operation.replace);
            return;
          }
          if (message.operation.steps === undefined) return;

          if (pending.current !== null || awaitingAck.current) {
            handlers.current.onReloaded('diverged');
            resync();
            return;
          }
          if (!handlers.current.onRemoteSteps(message.operation.steps)) {
            handlers.current.onReloaded('diverged');
            resync();
          }
          return;
        }
        case 'rejected': {
          awaitingAck.current = false;
          pending.current = null;
          versionRef.current = message.server_version;
          setVersion(message.server_version);
          handlers.current.onRemoteContent(message.content);
          handlers.current.onReloaded('rejected');
          return;
        }
        case 'presence': {
          const { type: _ignored, ...peer } = message;
          setPeers((current) => [
            ...current.filter((p) => p.user_id !== peer.user_id),
            peer,
          ]);
          return;
        }
        case 'peer_left':
          setPeers((current) =>
            current.filter((p) => p.user_id !== message.user_id),
          );
          return;
        case 'deleted':
          stop('deleted');
          return;
        case 'error':
          // Retrying fixes none of these: a viewer's edit, or a frame this
          // client should not have sent in the first place.
          console.warn('[synapse] the server refused a message', message);
          return;
        case 'pong':
          return;
      }
    },
    [flush, resync, stop],
  );

  connect.current = useCallback(() => {
    if (closed.current) return;
    dropSocket();
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }

    const attempt = (generation.current += 1);
    setState((current) => (current === 'live' ? 'reconnecting' : current));

    // A fresh ticket every time: the last one was spent on the socket that just
    // died, and would not be accepted twice.
    createWsTicket(documentId)
      .then((issued) => {
        if (closed.current || attempt !== generation.current) return;

        const ws = new WebSocket(documentSocketUrl(documentId, issued.ticket));
        socket.current = ws;
        lastMessageAt.current = Date.now();

        ws.onmessage = (event) => {
          if (attempt !== generation.current) return;
          lastMessageAt.current = Date.now();
          try {
            handle(JSON.parse(event.data as string) as ServerMessage);
          } catch {
            console.warn('[synapse] unreadable frame', event.data);
          }
        };

        ws.onopen = () => {
          heartbeat.current = window.setInterval(() => {
            if (Date.now() - lastMessageAt.current > SILENCE_LIMIT_MS) {
              // Dead without a close frame -- some networks never deliver one.
              dropSocket();
              scheduleRetry();
              return;
            }
            send({ type: 'ping' });
          }, HEARTBEAT_MS);
        };

        ws.onclose = () => {
          if (attempt !== generation.current) return;
          socket.current = null;
          setPeers([]);
          scheduleRetry();
        };
      })
      .catch((error: unknown) => {
        if (closed.current || attempt !== generation.current) return;
        // 404 covers both "no such document" and "you are not a member" -- the
        // API does not distinguish them on purpose. Either way, waiting will
        // not help.
        if (error instanceof ApiError && [403, 404].includes(error.status)) {
          stop('unavailable');
          return;
        }
        scheduleRetry();
      });
  }, [documentId, dropSocket, handle, scheduleRetry, send, stop]);

  useEffect(() => {
    closed.current = false;
    attempts.current = 0;
    setState('connecting');
    connect.current();

    // The browser knows the network is back before a heartbeat would.
    const onOnline = () => {
      attempts.current = 0;
      connect.current();
    };
    window.addEventListener('online', onOnline);

    return () => {
      closed.current = true;
      generation.current += 1;
      window.removeEventListener('online', onOnline);
      if (sendTimer.current !== null) window.clearTimeout(sendTimer.current);
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
      dropSocket();
    };
  }, [documentId, dropSocket]);

  return {
    state,
    version,
    role: self?.role ?? null,
    self,
    peers,
    queueEdit,
    sendCursor,
  };
}
