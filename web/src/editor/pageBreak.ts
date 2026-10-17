import { canInsertNode, isNodeSelection, mergeAttributes, Node } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    pageBreak: {
      setPageBreak: () => ReturnType;
    };
  }
}

export interface PageBreakOptions {
  HTMLAttributes: Record<string, string>;
}

/** A hard page break. Atomic, so the cursor steps over it as one unit and
 *  nothing can be typed inside; docx export writes `WD_BREAK.PAGE`. */
export const PageBreak = Node.create<PageBreakOptions>({
  name: 'pageBreak',
  // Ahead of hardBreak, which also binds Mod-Enter. Still well below
  // paragraph's 1000, so paragraph stays the block a document fills with.
  priority: 101,
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,

  addOptions() {
    return { HTMLAttributes: {} };
  },

  parseHTML() {
    return [{ tag: 'div[data-type="page-break"]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'page-break',
        class: 'page-break',
        role: 'separator',
        contenteditable: 'false',
      }),
    ];
  },

  addCommands() {
    return {
      // A page cannot break inside a table cell. The cursor always ends in
      // a paragraph after the break, made if need be: left on the break, the
      // next insert would replace it.
      setPageBreak:
        () =>
        ({ chain, state, editor }) => {
          if (editor.isActive('table') || !canInsertNode(state, this.type)) return false;
          const insert = isNodeSelection(state.selection)
            ? chain().insertContentAt(state.selection.to, { type: this.name })
            : chain().insertContent({ type: this.name });
          return insert
            .command(({ tr, dispatch }) => {
              if (!dispatch) return true;
              const { $to } = tr.selection;
              // In text already when the break split a paragraph.
              if (!$to.parent.isTextblock) {
                if (!$to.nodeAfter?.isTextblock) {
                  tr.insert($to.pos, state.schema.nodes.paragraph!.create());
                }
                tr.setSelection(TextSelection.create(tr.doc, $to.pos + 1));
              }
              tr.scrollIntoView();
              return true;
            })
            .run();
        },
    };
  },

  addKeyboardShortcuts() {
    return { 'Mod-Enter': () => this.editor.commands.setPageBreak() };
  },
});
