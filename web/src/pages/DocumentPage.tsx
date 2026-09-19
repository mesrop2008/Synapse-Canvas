import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useBlocker, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { EditorContent, useEditor, type EditorEvents } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Placeholder } from '@tiptap/extensions';
import { Step } from '@tiptap/pm/transform';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ConnectionIndicator } from '../components/ConnectionIndicator';
import { PeerList } from '../components/PeerList';
import { ChevronRightIcon } from '../components/icons';
import { RemoteCursors, setRemotePeers } from '../editor/remoteCursors';
import { documentKeys, useDocument } from '../hooks/useDocuments';
import {
  useDocumentSocket,
  type DocumentSocketCallbacks,
  type ReloadReason,
} from '../hooks/useDocumentSocket';
import { useI18n } from '../hooks/useI18n';
import { useTitleSave } from '../hooks/useTitleSave';
import { useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { MessageKey } from '../../i18n';
import type { DocumentDetail, ProseMirrorDoc } from '../types/api';

export function DocumentPage() {
  const { workspaceId = '', documentId = '' } = useParams();
  const { t } = useI18n();
  const workspace = useWorkspace(workspaceId);
  const document = useDocument(workspaceId, documentId);

  if (document.error) {
    return (
      <main className="container">
        <Alert>{errorMessage(document.error)}</Alert>
        <Link to={`/workspaces/${workspaceId}`}>
          {t('document.backToWorkspace')}
        </Link>
      </main>
    );
  }

  if (!document.data || !workspace.data) {
    return <p className="placeholder">{t('document.loading')}</p>;
  }

  // Mounting only once content exists avoids loading into a live editor, which
  // would mean telling that write apart from a user edit. The key remounts it
  // when the route moves to another document.
  return (
    <DocumentEditor
      key={document.data.id}
      workspaceId={workspaceId}
      loaded={document.data}
      writable={canEdit(workspace.data.role)}
      workspaceName={workspace.data.name}
    />
  );
}

interface DocumentEditorProps {
  workspaceId: string;
  loaded: DocumentDetail;
  writable: boolean;
  workspaceName: string;
}

const CURSOR_THROTTLE_MS = 150;

function DocumentEditor({
  workspaceId,
  loaded,
  writable,
  workspaceName,
}: DocumentEditorProps) {
  const { t, locale } = useI18n();
  const [title, setTitle] = useState(loaded.title);
  const [reloaded, setReloaded] = useState<ReloadReason | null>(null);

  // Set while a peer's work is being written into the editor, so the resulting
  // transaction is not mistaken for something the user typed and sent straight
  // back out.
  const applyingRemote = useRef(false);
  const lastCursorSentAt = useRef(0);
  // onTransaction fires during construction, before `socket` below exists.
  const onTransactionRef = useRef<
    (payload: EditorEvents['transaction']) => void
  >(() => {});

  const editor = useEditor(
    {
      extensions: [
        StarterKit.configure({
          // The log is the history now, and undoing a peer's edit out from
          // under them is not what the shortcut should do.
          undoRedo: false,
        }),
        Placeholder.configure({ placeholder: t('document.placeholder') }),
        RemoteCursors,
      ],
      content: loaded.content,
      editable: false, // until the socket says it is live
      immediatelyRender: true,
      editorProps: {
        attributes: { class: 'tiptap', 'aria-label': t('document.bodyLabel') },
      },
      onTransaction: (payload) => onTransactionRef.current(payload),
    },
    // Tiptap builds the schema once, so a language change needs a rebuild to
    // reach the placeholder.
    [locale],
  );

  const applyRemote = useCallback((change: () => void) => {
    applyingRemote.current = true;
    try {
      change();
    } finally {
      applyingRemote.current = false;
    }
  }, []);

  const setContent = useCallback(
    (content: ProseMirrorDoc) => {
      applyRemote(() =>
        editor?.commands.setContent(content, { emitUpdate: false }),
      );
    },
    [editor, applyRemote],
  );

  const callbacks: DocumentSocketCallbacks = {
    onInit: (init) => {
      setContent(init.content);
      setTitle(init.title);
    },

    onRemoteSteps: (steps) => {
      if (!editor) return false;
      const { state, dispatch } = editor.view;
      try {
        const transaction = state.tr;
        for (const raw of steps) {
          transaction.step(Step.fromJSON(state.schema, raw));
        }
        applyRemote(() => dispatch(transaction));
        return true;
      } catch {
        // A step that will not apply means this copy is not where the peer
        // thought it was. The hook resyncs rather than carrying on.
        return false;
      }
    },

    onRemoteContent: setContent,
    onRemoteTitle: setTitle,
    onReloaded: setReloaded,
  };

  const socket = useDocumentSocket(loaded.id, callbacks);
  const live = socket.state === 'live';
  const gone =
    socket.state === 'deleted' || socket.state === 'unavailable';

  const titleSave = useTitleSave({
    workspaceId,
    documentId: loaded.id,
    currentVersion: () => socket.version,
    getTitle: () => title.trim() || t('document.untitled'),
  });

  onTransactionRef.current = ({ transaction }) => {
    if (applyingRemote.current) return;

    if (transaction.docChanged && editor) {
      socket.queueEdit(
        transaction.steps.map((step) => step.toJSON()),
        editor.getJSON() as ProseMirrorDoc,
      );
    }

    // Throttled: a cursor is worth a frame of latency and not worth a message
    // per arrow key.
    if (transaction.selectionSet) {
      const now = Date.now();
      if (now - lastCursorSentAt.current >= CURSOR_THROTTLE_MS) {
        lastCursorSentAt.current = now;
        const { anchor, head } = transaction.selection;
        socket.sendCursor(anchor, head);
      }
    }
  };

  // Editing is blocked rather than buffered while the socket is down. Buffered
  // edits would have to be rebased on reconnect, and reject-and-rebase has no
  // rebase -- they would be collected, shown as progress, then thrown away.
  // Refusing them up front loses the same keystrokes without pretending.
  useEffect(() => {
    editor?.setEditable(writable && live);
  }, [editor, writable, live]);

  useEffect(() => {
    if (editor) setRemotePeers(editor.view, socket.peers);
  }, [editor, socket.peers]);

  // The list the user lands on after following the link out has to be right.
  // `exact`, because the detail key sits under the list key: without it this
  // refetches the document too, gets a 404, and replaces the notice below with
  // a generic error that says none of what happened.
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!gone) return;
    void queryClient.invalidateQueries({
      queryKey: documentKeys.list(workspaceId),
      exact: true,
    });
  }, [gone, queryClient, workspaceId]);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      titleSave.isDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  function handleTitleChange(next: string) {
    setTitle(next);
    if (writable && live) titleSave.schedule();
  }

  return (
    <main className="container container-reading">
      <nav className="crumbs">
        <Link to="/workspaces">{t('layout.workspaces')}</Link>
        <ChevronRightIcon size={14} />
        <Link to={`/workspaces/${workspaceId}`}>{workspaceName}</Link>
      </nav>

      {reloaded && (
        <Alert kind="warn" onDismiss={() => setReloaded(null)}>
          {t(
            reloaded === 'rejected'
              ? 'document.reloaded.rejected'
              : 'document.reloaded.diverged',
          )}
        </Alert>
      )}

      {gone && (
        <Alert>
          {t(
            socket.state === 'deleted'
              ? 'document.gone.deleted'
              : 'document.gone.unavailable',
          )}{' '}
          <Link to={`/workspaces/${workspaceId}`}>
            {t('document.backToWorkspace')}
          </Link>
        </Alert>
      )}

      {writable && !live && !gone && (
        <Alert kind="warn">
          {t(
            socket.state === 'offline'
              ? 'document.offline.stalled'
              : 'document.offline.connecting',
          )}
        </Alert>
      )}

      {titleSave.error && (
        <Alert onDismiss={titleSave.clearError}>{titleSave.error}</Alert>
      )}

      <div className="doc-head">
        <input
          className="doc-title"
          aria-label={t('document.titleLabel')}
          value={title}
          maxLength={255}
          readOnly={!writable || !live}
          onChange={(event) => handleTitleChange(event.target.value)}
        />
        <div className="doc-status">
          <PeerList peers={socket.peers} />
          {!writable && <span className="badge">{t('document.readOnly')}</span>}
          <ConnectionIndicator state={socket.state} version={socket.version} />
        </div>
      </div>

      {writable && editor && <Toolbar editor={editor} disabled={!live} />}

      <div className="paper">
        <EditorContent editor={editor} />
      </div>

      {blocker.state === 'blocked' && (
        <ConfirmDialog
          title={t('document.leaveTitle')}
          message={t('document.leaveMessage')}
          confirmLabel={t('document.leaveConfirm')}
          cancelLabel={t('document.leaveCancel')}
          destructive
          onConfirm={() => blocker.proceed()}
          onCancel={() => blocker.reset()}
        />
      )}
    </main>
  );
}

type TiptapEditor = NonNullable<ReturnType<typeof useEditor>>;

/** Enough to exercise what StarterKit provides. */
function Toolbar({
  editor,
  disabled,
}: {
  editor: TiptapEditor;
  disabled: boolean;
}) {
  const { t } = useI18n();

  const actions: Array<{ key: string; label: MessageKey; isActive: boolean; run: () => void }> =
    useMemo(
      () => [
        {
          key: 'bold',
          label: 'document.toolbar.bold',
          isActive: editor.isActive('bold'),
          run: () => editor.chain().focus().toggleBold().run(),
        },
        {
          key: 'italic',
          label: 'document.toolbar.italic',
          isActive: editor.isActive('italic'),
          run: () => editor.chain().focus().toggleItalic().run(),
        },
        {
          key: 'h1',
          label: 'document.toolbar.h1',
          isActive: editor.isActive('heading', { level: 1 }),
          run: () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
        },
        {
          key: 'h2',
          label: 'document.toolbar.h2',
          isActive: editor.isActive('heading', { level: 2 }),
          run: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
        },
        {
          key: 'bullets',
          label: 'document.toolbar.bullets',
          isActive: editor.isActive('bulletList'),
          run: () => editor.chain().focus().toggleBulletList().run(),
        },
        {
          key: 'numbered',
          label: 'document.toolbar.numbered',
          isActive: editor.isActive('orderedList'),
          run: () => editor.chain().focus().toggleOrderedList().run(),
        },
        {
          key: 'quote',
          label: 'document.toolbar.quote',
          isActive: editor.isActive('blockquote'),
          run: () => editor.chain().focus().toggleBlockquote().run(),
        },
        {
          key: 'code',
          label: 'document.toolbar.code',
          isActive: editor.isActive('codeBlock'),
          run: () => editor.chain().focus().toggleCodeBlock().run(),
        },
      ],
      [editor],
    );

  return (
    <div className="toolbar">
      {actions.map((action) => (
        <button
          key={action.key}
          type="button"
          aria-pressed={action.isActive}
          disabled={disabled}
          onClick={action.run}
        >
          {t(action.label)}
        </button>
      ))}
    </div>
  );
}
