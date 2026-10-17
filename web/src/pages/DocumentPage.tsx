import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useBlocker, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { EditorContent, useEditor, type EditorEvents } from '@tiptap/react';
import { Placeholder } from '@tiptap/extensions';
import { Step } from '@tiptap/pm/transform';

import { exportDocument } from '../api/documents';
import { describeFailure, errorMessage, failed, type FailedRequest } from '../api/errors';
import { AIPanel } from '../components/AIPanel';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ConnectionIndicator } from '../components/ConnectionIndicator';
import { EditorToolbar } from '../components/EditorToolbar';
import { ImportedNotice, droppedFrom } from '../components/ImportedNotice';
import { LinkDialog } from '../components/LinkDialog';
import { PeerList } from '../components/PeerList';
import { ShortcutsDialog } from '../components/ShortcutsDialog';
import { ChevronRightIcon, DownloadIcon } from '../components/icons';
import { saveFile } from '../download';
import { DialogShortcuts } from '../editor/dialogShortcuts';
import { schemaExtensions } from '../editor/extensions';
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

  // Mounted once content exists, so loading it is not mistaken for an edit.
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
// Long enough for the last keystrokes to reach the server before export.
const SETTLE_WAIT_MS = 3_000;

async function settled(isSettled: () => boolean): Promise<void> {
  const deadline = Date.now() + SETTLE_WAIT_MS;
  while (!isSettled() && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 100));
  }
}

function DocumentEditor({
  workspaceId,
  loaded,
  writable,
  workspaceName,
}: DocumentEditorProps) {
  const { t, locale } = useI18n();
  const [title, setTitle] = useState(loaded.title);
  const [reloaded, setReloaded] = useState<ReloadReason | null>(null);
  const [dialog, setDialog] = useState<'link' | 'shortcuts' | null>(null);
  const closeDialog = useCallback(() => setDialog(null), []);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<FailedRequest | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const dropped = droppedFrom(location.state);

  // Set while applying a peer's edit, so it is not sent back out.
  const applyingRemote = useRef(false);
  const lastCursorSentAt = useRef(0);
  // onTransaction fires during construction, before `socket` below exists.
  const onTransactionRef = useRef<
    (payload: EditorEvents['transaction']) => void
  >(() => {});

  const editor = useEditor(
    {
      extensions: [
        ...schemaExtensions({ pageBreakLabel: t('document.toolbar.pageBreak') }),
        Placeholder.configure({ placeholder: t('document.placeholder') }),
        RemoteCursors,
        DialogShortcuts.configure({
          onLink: () => setDialog('link'),
          onHelp: () => setDialog('shortcuts'),
        }),
      ],
      content: loaded.content,
      editable: false, // until the socket says it is live
      immediatelyRender: true,
      editorProps: {
        attributes: { class: 'tiptap', 'aria-label': t('document.bodyLabel') },
      },
      onTransaction: (payload) => onTransactionRef.current(payload),
    },
    // Rebuilt on language change: Tiptap builds the placeholder once.
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
  const outdated = socket.state === 'outdated';

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

    if (transaction.selectionSet) {
      const now = Date.now();
      if (now - lastCursorSentAt.current >= CURSOR_THROTTLE_MS) {
        lastCursorSentAt.current = now;
        const { anchor, head } = transaction.selection;
        socket.sendCursor(anchor, head);
      }
    }
  };

  // Blocked, not buffered, while offline: buffered edits could not be rebased.
  useEffect(() => {
    editor?.setEditable(writable && live);
  }, [editor, writable, live]);

  useEffect(() => {
    if (editor) setRemotePeers(editor.view, socket.peers);
  }, [editor, socket.peers]);

  // `exact`: refetching the deleted document would 404 over the notice.
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

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      await settled(socket.isSettled);
      const file = await exportDocument(workspaceId, loaded.id);
      saveFile(file.blob, file.filename ?? `${loaded.id}.docx`);
    } catch (cause) {
      setExportError(failed(cause, 'document.export.failed'));
    } finally {
      setExporting(false);
    }
  }

  function handleTitleChange(next: string) {
    setTitle(next);
    if (writable && live) titleSave.schedule();
  }

  return (
    <main className={writable ? 'container doc-layout' : 'container container-reading'}>
      <div className="doc-main">
        <nav className="crumbs">
          <Link to="/workspaces">{t('layout.workspaces')}</Link>
          <ChevronRightIcon size={14} />
          <Link to={`/workspaces/${workspaceId}`}>{workspaceName}</Link>
        </nav>

        {dropped.length > 0 && (
          <ImportedNotice
            dropped={dropped}
            // Cleared from history, so a reload does not repeat it.
            onDismiss={() => navigate(location.pathname, { replace: true, state: null })}
          />
        )}

        {exportError && (
          <Alert onDismiss={() => setExportError(null)}>{describeFailure(exportError)}</Alert>
        )}

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

        {outdated && (
          <Alert kind="warn">
            {t('document.outdated.message')}{' '}
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => window.location.reload()}
            >
              {t('document.outdated.reload')}
            </button>
          </Alert>
        )}

        {writable && !live && !gone && !outdated && (
          <Alert kind="warn">
            {t(
              socket.state === 'offline'
                ? 'document.offline.stalled'
                : 'document.offline.connecting',
            )}
          </Alert>
        )}

        {titleSave.error && (
          <Alert onDismiss={titleSave.clearError}>
            {describeFailure(titleSave.error)}
          </Alert>
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
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={exporting}
              onClick={handleExport}
            >
              <DownloadIcon />
              {exporting ? t('document.export.exporting') : t('document.export.button')}
            </button>
            <ConnectionIndicator state={socket.state} version={socket.version} />
          </div>
        </div>

        {writable && editor && (
          <EditorToolbar
            editor={editor}
            disabled={!live}
            onLink={() => setDialog('link')}
            onHelp={() => setDialog('shortcuts')}
          />
        )}

        <div className="paper">
          <EditorContent editor={editor} />
        </div>
      </div>

      {writable && editor && (
        <AIPanel
          editor={editor}
          documentId={loaded.id}
          workspaceId={workspaceId}
          socket={{
            live,
            isSettled: socket.isSettled,
            currentVersion: socket.currentVersion,
          }}
        />
      )}

      {dialog === 'link' && editor && live && (
        <LinkDialog editor={editor} onClose={closeDialog} />
      )}
      {dialog === 'shortcuts' && <ShortcutsDialog onClose={closeDialog} />}

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
