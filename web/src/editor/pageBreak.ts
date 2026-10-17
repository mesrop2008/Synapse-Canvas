import { mergeAttributes, Node } from '@tiptap/core';

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
      setPageBreak:
        () =>
        ({ chain }) =>
          chain().insertContent({ type: this.name }).run(),
    };
  },

  addKeyboardShortcuts() {
    return { 'Mod-Enter': () => this.editor.commands.setPageBreak() };
  },
});
