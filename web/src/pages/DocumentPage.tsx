import { useCallback, useEffect, useRef, useState } from 'react';
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
import { useWorkspace } from '../hooks/useWorkspaces';
import { canEdit } from '../types/api';
import type { DocumentDetail, DocumentVersionConflict, ProseMirrorDoc } from '../types/api';

export function DocumentPage() {
  const { workspaceId = '', documentId = '' } = useParams();
  const workspace = useWorkspace(workspaceId);
  const document = useDocument(workspaceId, documentId);

  if (document.error) {
    return (
      <main className="container">
        <Alert>{errorMessage(document.error)}</Alert>
        <Link to={`/workspaces/${workspaceId}`}>Back to the workspace</Link>
      </main>
    );
  }

  if (!document.data || !workspace.data) {
    return <p className="placeholder">Loading document…</p>;
  }

  // Mounting the editor only once content exists avoids loading into it after
  // the fact, which would mean distinguishing that write from a user edit.
  // The key remounts cleanly when the route moves to another document.
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
  const [title, setTitle] = useState(loaded.title);
  const [reloadedFromServer, setReloadedFromServer] = useState(false);

  // onUpdate fires while the editor is being constructed, before `autosave`
  // below exists, so it reaches the scheduler through a ref.
  const scheduleRef = useRef<() => void>(() => {});
  const titleRef = useRef(title);
  titleRef.current = title;

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: 'Start writing…' }),
    ],
    content: loaded.content,
    editable: writable,
    immediatelyRender: true,
    editorProps: { attributes: { class: 'tiptap', 'aria-label': 'Document body' } },
    onUpdate: () => scheduleRef.current(),
  });

  const getSnapshot = useCallback(
    (): DocumentSnapshot => ({
      title: titleRef.current.trim() || 'Untitled',
      content: (editor?.getJSON() ?? loaded.content) as ProseMirrorDoc,
    }),
    [editor, loaded.content],
  );

  const handleConflict = useCallback(
    (conflict: DocumentVersionConflict, replaced: DocumentSnapshot) => {
      // Part 3 merges instead. Until then the server wins, so the text that
      // lost goes somewhere recoverable rather than straight in the bin.
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

      // emitUpdate: false, or this write would look like an edit and schedule a
      // save of the server's own content back to it.
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

  // In-app navigation. Needs the data router, which is why App.tsx uses
  // createBrowserRouter.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      autosave.isDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  // Closing the tab or reloading. The browser shows its own wording; nothing
  // here can change it.
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
        <Link to="/workspaces">Workspaces</Link>
        <ChevronRightIcon size={13} />
        <Link to={`/workspaces/${workspaceId}`}>{workspaceName}</Link>
      </nav>

      {reloadedFromServer && (
        <Alert kind="warn" onDismiss={() => setReloadedFromServer(false)}>
          This document was changed elsewhere and has been reloaded. Your
          unsaved text was replaced — it is in the browser console, logged as a
          warning, so you can copy it back.
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
            Try again
          </button>
        </Alert>
      )}

      <div className="doc-head">
        <input
          className="doc-title"
          aria-label="Document title"
          value={title}
          maxLength={255}
          readOnly={!writable}
          onChange={(event) => handleTitleChange(event.target.value)}
        />
        <div className="doc-status">
          {writable ? (
            <SaveIndicator status={autosave.status} version={autosave.version} />
          ) : (
            <span className="badge">read-only</span>
          )}
        </div>
      </div>

      {writable && editor && <Toolbar editor={editor} />}

      <div className="paper">
        <EditorContent editor={editor} />
      </div>

      {blocker.state === 'blocked' && (
        <ConfirmDialog
          title="Leave with unsaved changes?"
          message="This document has edits that have not reached the server yet. Leaving now loses them."
          confirmLabel="Leave anyway"
          cancelLabel="Keep editing"
          destructive
          onConfirm={() => blocker.proceed()}
          onCancel={() => blocker.reset()}
        />
      )}
    </main>
  );
}

type TiptapEditor = NonNullable<ReturnType<typeof useEditor>>;

/** Minimal formatting controls, enough to exercise what StarterKit provides. */
function Toolbar({ editor }: { editor: TiptapEditor }) {
  const actions: Array<{ label: string; isActive: boolean; run: () => void }> = [
    {
      label: 'Bold',
      isActive: editor.isActive('bold'),
      run: () => editor.chain().focus().toggleBold().run(),
    },
    {
      label: 'Italic',
      isActive: editor.isActive('italic'),
      run: () => editor.chain().focus().toggleItalic().run(),
    },
    {
      label: 'H1',
      isActive: editor.isActive('heading', { level: 1 }),
      run: () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
    },
    {
      label: 'H2',
      isActive: editor.isActive('heading', { level: 2 }),
      run: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
    },
    {
      label: 'Bullets',
      isActive: editor.isActive('bulletList'),
      run: () => editor.chain().focus().toggleBulletList().run(),
    },
    {
      label: 'Numbered',
      isActive: editor.isActive('orderedList'),
      run: () => editor.chain().focus().toggleOrderedList().run(),
    },
    {
      label: 'Quote',
      isActive: editor.isActive('blockquote'),
      run: () => editor.chain().focus().toggleBlockquote().run(),
    },
    {
      label: 'Code',
      isActive: editor.isActive('codeBlock'),
      run: () => editor.chain().focus().toggleCodeBlock().run(),
    },
  ];

  return (
    <div className="toolbar">
      {actions.map((action) => (
        <button
          key={action.label}
          type="button"
          aria-pressed={action.isActive}
          onClick={action.run}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
