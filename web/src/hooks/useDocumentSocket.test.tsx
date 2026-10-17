// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SCHEMA_VERSION } from '../editor/schemaVersion';
import type { InitMessage } from '../types/api';
import { useDocumentSocket, type DocumentSocketCallbacks } from './useDocumentSocket';

vi.mock('../api/realtime', async (original) => ({
  ...(await original<typeof import('../api/realtime')>()),
  createWsTicket: vi.fn(async () => ({ ticket: 'ticket', expires_in: 30 })),
}));

class FakeSocket {
  static readonly OPEN = 1;
  static opened: FakeSocket[] = [];

  readyState = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.opened.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  deliver(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  closeFromServer(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

const init = (overrides: Partial<InitMessage> = {}): InitMessage => ({
  type: 'init',
  schema_version: SCHEMA_VERSION,
  version: 4,
  content: { type: 'doc', content: [{ type: 'paragraph' }] },
  title: 'Doc',
  peers: [],
  you: { user_id: 'u1', name: 'Ann', color: '#000000', role: 'editor' },
  ...overrides,
});

const peerEdit = {
  type: 'edit',
  version: 5,
  user_id: 'u2',
  operation: { steps: [{ stepType: 'replace', from: 1, to: 1, slice: { content: [{ type: 'pageBreak' }] } }] },
};

function callbacks(): DocumentSocketCallbacks & Record<string, ReturnType<typeof vi.fn>> {
  return {
    onInit: vi.fn(),
    onRemoteSteps: vi.fn(() => true),
    onRemoteContent: vi.fn(),
    onRemoteTitle: vi.fn(),
    onReloaded: vi.fn(),
  };
}

async function connect(handlers: DocumentSocketCallbacks) {
  const hook = renderHook(() => useDocumentSocket('doc-1', handlers));
  await waitFor(() => expect(FakeSocket.opened).toHaveLength(1));
  const socket = FakeSocket.opened[0]!;
  act(() => socket.open());
  return { hook, socket };
}

beforeEach(() => {
  FakeSocket.opened = [];
  vi.stubGlobal('WebSocket', FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('the editor schema version', () => {
  it('is declared on the socket URL', async () => {
    const { socket } = await connect(callbacks());

    expect(new URL(socket.url).searchParams.get('schema')).toBe(String(SCHEMA_VERSION));
  });

  it('matching, lets the document load and peers steps apply', async () => {
    const handlers = callbacks();
    const { hook, socket } = await connect(handlers);

    act(() => socket.deliver(init()));
    act(() => socket.deliver(peerEdit));

    expect(hook.result.current.state).toBe('live');
    expect(handlers.onInit).toHaveBeenCalledOnce();
    expect(handlers.onRemoteSteps).toHaveBeenCalledWith(peerEdit.operation.steps);
  });

  it.each([
    ['another version', init({ schema_version: SCHEMA_VERSION + 1 })],
    ['none, from a server that predates versioning', init({ schema_version: undefined })],
  ])('in init, %s: stops before applying anything', async (_case, message) => {
    const handlers = callbacks();
    const { hook, socket } = await connect(handlers);

    act(() => socket.deliver(message));
    act(() => socket.deliver(peerEdit));

    expect(hook.result.current.state).toBe('outdated');
    expect(handlers.onInit).not.toHaveBeenCalled();
    expect(handlers.onRemoteSteps).not.toHaveBeenCalled();
    expect(handlers.onRemoteContent).not.toHaveBeenCalled();
  });

  it('refused by the server: stops for good instead of reconnecting', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const handlers = callbacks();
    const { hook, socket } = await connect(handlers);

    act(() => {
      socket.deliver({ type: 'schema_mismatch', schema_version: SCHEMA_VERSION + 1 });
      socket.closeFromServer(4426);
    });
    await act(() => vi.advanceTimersByTimeAsync(60_000));

    expect(hook.result.current.state).toBe('outdated');
    expect(FakeSocket.opened).toHaveLength(1);
    expect(handlers.onInit).not.toHaveBeenCalled();
  });

  it('closed with 4426 alone: still stops', async () => {
    const { hook, socket } = await connect(callbacks());

    act(() => socket.closeFromServer(4426));

    expect(hook.result.current.state).toBe('outdated');
  });

  it('never lets an outdated client send an edit', async () => {
    const { hook, socket } = await connect(callbacks());

    act(() => socket.deliver(init({ schema_version: SCHEMA_VERSION + 1 })));
    act(() => hook.result.current.queueEdit([{ stepType: 'replace' }], { type: 'doc', content: [] }));
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(socket.sent.filter((message) => (message as { type: string }).type === 'edit')).toEqual([]);
  });
});
