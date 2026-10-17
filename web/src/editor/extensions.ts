import type { Extensions } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Highlight from '@tiptap/extension-highlight';
import Subscript from '@tiptap/extension-subscript';
import Superscript from '@tiptap/extension-superscript';
import TextAlign from '@tiptap/extension-text-align';
import { TaskItem, TaskList } from '@tiptap/extension-list';
import { TableKit } from '@tiptap/extension-table';
import { Color, FontFamily, FontSize, TextStyle } from '@tiptap/extension-text-style';

import { BlockFormat } from './blockFormat';
import { PageBreak } from './pageBreak';
import manifest from './schema.json';

/** Bumped whenever a node, mark or attribute changes. The server sends its
 *  own in `init`, and a mismatched client stops before applying anything. */
export const SCHEMA_VERSION: number = manifest.version;

export const ALIGNMENTS = ['left', 'center', 'right', 'justify'] as const;

/** Only http(s) and mailto open from a document; Link already refuses
 *  `javascript:` and friends, this narrows it further. */
const LINK_PROTOCOL = /^(https?:|mailto:)/i;

export interface SchemaOptions {
  pageBreakLabel?: string;
}

/**
 * Everything that shapes the ProseMirror schema, and nothing else: the page
 * adds its UI-only extensions on top, and the tests build the schema from
 * this list alone. Changing it means updating `schema.json` and the version.
 */
export function schemaExtensions(options: SchemaOptions = {}): Extensions {
  return [
    StarterKit.configure({
      // Undo must not revert peers' edits.
      undoRedo: false,
      link: {
        openOnClick: false,
        autolink: true,
        defaultProtocol: 'https',
        isAllowedUri: (url, context) =>
          LINK_PROTOCOL.test(url) && context.defaultValidate(url),
        HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: '_blank' },
      },
    }),
    TextStyle,
    Color,
    FontFamily,
    FontSize,
    Highlight.configure({ multicolor: true }),
    Subscript,
    Superscript,
    TextAlign.configure({ types: ['heading', 'paragraph'], alignments: [...ALIGNMENTS] }),
    BlockFormat,
    TaskList,
    TaskItem.configure({ nested: true }),
    TableKit.configure({ table: { resizable: false } }),
    PageBreak.configure({
      HTMLAttributes: options.pageBreakLabel ? { 'aria-label': options.pageBreakLabel } : {},
    }),
  ];
}
