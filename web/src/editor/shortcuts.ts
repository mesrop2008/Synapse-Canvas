import type { MessageKey } from '../../i18n';

/** In ProseMirror's notation. Tiptap's extensions bind these themselves;
 *  this table only describes them, for the help panel and tooltips. */
export const SHORTCUTS = {
  bold: 'Mod-b',
  italic: 'Mod-i',
  underline: 'Mod-u',
  strike: 'Mod-Shift-s',
  code: 'Mod-e',
  superscript: 'Mod-.',
  subscript: 'Mod-,',
  highlight: 'Mod-Shift-h',
  link: 'Mod-k',
  alignLeft: 'Mod-Shift-l',
  alignCenter: 'Mod-Shift-e',
  alignRight: 'Mod-Shift-r',
  alignJustify: 'Mod-Shift-j',
  indent: 'Mod-]',
  outdent: 'Mod-[',
  bulletList: 'Mod-Shift-8',
  orderedList: 'Mod-Shift-7',
  taskList: 'Mod-Shift-9',
  blockquote: 'Mod-Shift-b',
  codeBlock: 'Mod-Alt-c',
  heading1: 'Mod-Alt-1',
  heading2: 'Mod-Alt-2',
  heading3: 'Mod-Alt-3',
  lineBreak: 'Shift-Enter',
  pageBreak: 'Mod-Enter',
  shortcuts: 'Mod-/',
} as const;

export type ShortcutAction = keyof typeof SHORTCUTS;

export const SHORTCUT_GROUPS: Array<{ label: MessageKey; actions: ShortcutAction[] }> = [
  {
    label: 'document.shortcuts.groups.text',
    actions: ['bold', 'italic', 'underline', 'strike', 'code', 'superscript', 'subscript', 'highlight', 'link'],
  },
  {
    label: 'document.shortcuts.groups.paragraph',
    actions: [
      'heading1',
      'heading2',
      'heading3',
      'alignLeft',
      'alignCenter',
      'alignRight',
      'alignJustify',
      'indent',
      'outdent',
    ],
  },
  {
    label: 'document.shortcuts.groups.blocks',
    actions: ['bulletList', 'orderedList', 'taskList', 'blockquote', 'codeBlock', 'lineBreak', 'pageBreak', 'shortcuts'],
  },
];

export function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
}

const MODIFIERS: Record<string, { mac: MessageKey; other: MessageKey }> = {
  Mod: { mac: 'document.shortcuts.keys.modMac', other: 'document.shortcuts.keys.mod' },
  Shift: { mac: 'document.shortcuts.keys.shift', other: 'document.shortcuts.keys.shift' },
  Alt: { mac: 'document.shortcuts.keys.altMac', other: 'document.shortcuts.keys.alt' },
  Enter: { mac: 'document.shortcuts.keys.enter', other: 'document.shortcuts.keys.enter' },
};

/** Each key as it reads on this platform's keyboard. */
export function shortcutKeys(
  action: ShortcutAction,
  translate: (key: MessageKey) => string,
  mac = isMac(),
): string[] {
  return SHORTCUTS[action].split(/-(?!$)/).map((part) => {
    const modifier = MODIFIERS[part];
    if (modifier) return translate(mac ? modifier.mac : modifier.other);
    return part.length === 1 ? part.toUpperCase() : part;
  });
}
