import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useBlocker, useParams } from 'react-router-dom';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Placeholder } from '@tiptap/extensions';

import { errorMessage } from '../api/errors';
import { Alert } from '../components/Alert';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { SaveIndicator } from '../components/SaveIndicator';
import { ChevronRightIcon } from '../components/icons';
import { useAutosave, type DocumentSnapshot } from '../hooks/useAutosave';
import { useDocument } from '../hooks/useDocuments';
import { useI18n } from '../hooks/useI18n';
import { useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { MessageKey } from '../../i18n';
import type { DocumentDetail, DocumentVersionConflict, ProseMirrorDoc } from '../types/api';

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

function DocumentEditor({
  workspaceId,
  loaded,
  writable,
  workspaceName,
}: DocumentEditorProps) {
  const { t, locale } = useI18n();
  const [title, setTitle] = useState(loaded.title);
  const [reloadedFromServer, setReloadedFromServer] = useState(false);

  // onUpdate fires during construction, before `autosave` below exists.
  const scheduleRef = useRef<() => void>(() => {});
  const titleRef = useRef(title);
  titleRef.current = title;
  // Read at save time, so a language switch mid-edit keeps the new one.
  const untitledRef = useRef(t('document.untitled'));
  untitledRef.current = t('document.untitled');

  const editor = useEditor(
    {
      extensions: [
        StarterKit,
        Placeholder.configure({ placeholder: t('document.placeholder') }),
      ],
      content: loaded.content,
      editable: writable,
      immediatelyRender: true,
      editorProps: {
        attributes: { class: 'tiptap', 'aria-label': t('document.bodyLabel') },
      },
      onUpdate: () => scheduleRef.current(),
    },
    // Tiptap builds the schema once, so a language change needs a rebuild to
    // reach the placeholder.
    [locale],
  );

  const getSnapshot = useCallback(
    (): DocumentSnapshot => ({
      title: titleRef.current.trim() || untitledRef.current,
      content: (editor?.getJSON() ?? loaded.content) as ProseMirrorDoc,
    }),
    [editor, loaded.content],
  );

  const handleConflict = useCallback(
    (conflict: DocumentVersionConflict, replaced: DocumentSnapshot) => {
      // Part 3 merges instead; until then the server wins, so the losing text
      // goes somewhere recoverable. English on purpose -- it is for developers.
      console.warn(
        '[synapse] Document changed elsewhere; the following local state was replaced. ' +
          'Copy anything you need from here.',
        {
          documentId: conflict.current.id,
          serverVersion: conflict.current.version,
          replacedTitle: replaced.title,
          replacedContent: replaced.content,
        },
      );

      // emitUpdate: false, or this looks like an edit and saves the server's
      // own content back to it.
      editor?.commands.setContent(conflict.current.content, { emitUpdate: false });
      setTitle(conflict.current.title);
      setReloadedFromServer(true);
    },
    [editor],
  );

  const autosave = useAutosave({
    workspaceId,
    documentId: loaded.id,
    initialVersion: loaded.version,
    getSnapshot,
    onConflict: handleConflict,
  });
  scheduleRef.current = autosave.schedule;

  // Needs the data router, which is why App.tsx uses createBrowserRouter.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      autosave.isDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  // Closing the tab. The browser picks the wording, not us.
  useEffect(() => {
    if (!autosave.isDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [autosave.isDirty]);

  function handleTitleChange(next: string) {
    setTitle(next);
    if (writable) autosave.schedule();
  }

  return (
    <main className="container container-reading">
      <nav className="crumbs">
        <Link to="/workspaces">{t('layout.workspaces')}</Link>
        <ChevronRightIcon size={14} />
        <Link to={`/workspaces/${workspaceId}`}>{workspaceName}</Link>
      </nav>

      {reloadedFromServer && (
        <Alert kind="warn" onDismiss={() => setReloadedFromServer(false)}>
          {t('document.conflict')}
        </Alert>
      )}

      {autosave.error && (
        <Alert onDismiss={autosave.clearError}>
          {autosave.error}{' '}
          <button
            type="button"
            className="btn-link"
            onClick={() => void autosave.flush()}
          >
            {t('common.tryAgain')}
          </button>
        </Alert>
      )}

      <div className="doc-head">
        <input
          className="doc-title"
          aria-label={t('document.titleLabel')}
          value={title}
          maxLength={255}
          readOnly={!writable}
          onChange={(event) => handleTitleChange(event.target.value)}
        />
        <div className="doc-status">
          {writable ? (
            <SaveIndicator status={autosave.status} version={autosave.version} />
          ) : (
            <span className="badge">{t('document.readOnly')}</span>
          )}
        </div>
      </div>

      {writable && editor && <Toolbar editor={editor} />}

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
function Toolbar({ editor }: { editor: TiptapEditor }) {
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
          onClick={action.run}
        >
          {t(action.label)}
        </button>
      ))}
    </div>
  );
}
