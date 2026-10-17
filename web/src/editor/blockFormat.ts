import { Extension, type CommandProps, type Editor } from '@tiptap/core';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    blockFormat: {
      /** Inside a list, nests the item instead. */
      indent: () => ReturnType;
      outdent: () => ReturnType;
      setLineHeight: (lineHeight: string) => ReturnType;
      unsetLineHeight: () => ReturnType;
    };
  }
}

export const MAX_INDENT = 8;
export const LINE_HEIGHTS = ['1', '1.15', '1.5', '2'] as const;

/** em per level; docx export converts with the same factor. */
const INDENT_EM = 2;

const BLOCKS = ['paragraph', 'heading'];
const LIST_ITEMS = ['listItem', 'taskItem'];

function listItemAt(editor: Editor): string | null {
  return LIST_ITEMS.find((name) => editor.isActive(name)) ?? null;
}

/**
 * Indent and line height as attributes of the paragraph, as Word keeps
 * them. Tiptap's own LineHeight sets a text-style mark on spans instead,
 * which docx has no way to say and CSS applies only to part of a line box.
 */
export const BlockFormat = Extension.create({
  name: 'blockFormat',

  addGlobalAttributes() {
    return [
      {
        types: BLOCKS,
        attributes: {
          indent: {
            default: 0,
            parseHTML: (element) => {
              const level = Number.parseInt(element.dataset.indent ?? '', 10);
              return Number.isFinite(level) ? Math.min(Math.max(level, 0), MAX_INDENT) : 0;
            },
            renderHTML: (attributes) =>
              attributes.indent
                ? {
                    'data-indent': String(attributes.indent),
                    style: `margin-left: ${attributes.indent * INDENT_EM}em`,
                  }
                : {},
          },
          lineHeight: {
            default: null,
            parseHTML: (element) => element.style.lineHeight || null,
            renderHTML: (attributes) =>
              attributes.lineHeight ? { style: `line-height: ${attributes.lineHeight}` } : {},
          },
        },
      },
    ];
  },

  addCommands() {
    const shift =
      (delta: number) =>
      () =>
      ({ editor, state, tr, dispatch, commands }: CommandProps) => {
        const item = listItemAt(editor);
        if (item) {
          return delta > 0 ? commands.sinkListItem(item) : commands.liftListItem(item);
        }
        let changed = false;
        const { from, to } = state.selection;
        state.doc.nodesBetween(from, to, (node, pos) => {
          if (!BLOCKS.includes(node.type.name)) return true;
          const current = Number(node.attrs.indent) || 0;
          const next = Math.min(Math.max(current + delta, 0), MAX_INDENT);
          if (next !== current) {
            if (dispatch) tr.setNodeMarkup(pos, undefined, { ...node.attrs, indent: next });
            changed = true;
          }
          return false;
        });
        return changed;
      };

    return {
      indent: shift(1),
      outdent: shift(-1),
      setLineHeight:
        (lineHeight) =>
        ({ commands }) =>
          BLOCKS.map((type) => commands.updateAttributes(type, { lineHeight })).some(Boolean),
      unsetLineHeight:
        () =>
        ({ commands }) =>
          BLOCKS.map((type) => commands.resetAttributes(type, 'lineHeight')).some(Boolean),
    };
  },

  addKeyboardShortcuts() {
    return {
      'Mod-]': () => this.editor.commands.indent(),
      'Mod-[': () => this.editor.commands.outdent(),
    };
  },
});
