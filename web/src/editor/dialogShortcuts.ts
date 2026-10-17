import { Extension } from '@tiptap/core';

import { SHORTCUTS } from './shortcuts';

export interface DialogShortcutsOptions {
  onLink: () => void;
  onHelp: () => void;
}

/** Keys that open the page's dialogs rather than change the document, so
 *  they are not part of the schema's extensions. */
export const DialogShortcuts = Extension.create<DialogShortcutsOptions>({
  name: 'dialogShortcuts',

  addOptions() {
    return { onLink: () => {}, onHelp: () => {} };
  },

  addKeyboardShortcuts() {
    return {
      [SHORTCUTS.link]: () => {
        if (!this.editor.isEditable) return false;
        this.options.onLink();
        return true;
      },
      [SHORTCUTS.shortcuts]: () => {
        this.options.onHelp();
        return true;
      },
    };
  },
});
