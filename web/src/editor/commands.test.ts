// @vitest-environment jsdom
import { Editor, generateHTML, generateJSON, type JSONContent } from '@tiptap/core';
import { afterEach, describe, expect, it } from 'vitest';

import { MAX_INDENT } from './blockFormat';
import { schemaExtensions } from './extensions';
import { linkText, normalizeHref } from './links';

const editors: Editor[] = [];

function editorWith(content: JSONContent): Editor {
  const editor = new Editor({ extensions: schemaExtensions(), content });
  editors.push(editor);
  return editor;
}

afterEach(() => {
  while (editors.length) editors.pop()!.destroy();
});

const paragraph = (value?: string): JSONContent =>
  value ? { type: 'paragraph', content: [{ type: 'text', text: value }] } : { type: 'paragraph' };
const types = (editor: Editor) => editor.getJSON().content!.map((node) => node.type);

describe('page break', () => {
  it('survives HTML both ways, as it does a copy and paste', () => {
    const json = { type: 'doc', content: [paragraph('before'), { type: 'pageBreak' }, paragraph('after')] };
    const html = generateHTML(json, schemaExtensions());

    expect(html).toContain('data-type="page-break"');
    expect(generateJSON(html, schemaExtensions()).content.map((n: JSONContent) => n.type)).toEqual([
      'paragraph',
      'pageBreak',
      'paragraph',
    ]);
  });

  it('splits the paragraph it is inserted into', () => {
    const editor = editorWith({ type: 'doc', content: [paragraph('beforeafter')] });
    editor.commands.setTextSelection(7);

    expect(editor.commands.setPageBreak()).toBe(true);
    expect(editor.getText({ blockSeparator: '|' })).toBe('before||after');
    expect(types(editor)).toEqual(['paragraph', 'pageBreak', 'paragraph']);
  });

  it('leaves the cursor after itself, so the next insert does not replace it', () => {
    const editor = editorWith({ type: 'doc', content: [paragraph('text')] });
    editor.commands.focus('end');

    editor.commands.setPageBreak();
    expect(editor.state.selection.$from.parent.type.name).toBe('paragraph');
    editor.commands.insertTable({ rows: 2, cols: 2 });

    expect(types(editor)).toEqual(['paragraph', 'pageBreak', 'table', 'paragraph']);
  });

  it('is refused inside a table', () => {
    const editor = editorWith({ type: 'doc', content: [paragraph('x')] });
    editor.commands.insertTable({ rows: 2, cols: 2 });

    expect(editor.isActive('table')).toBe(true);
    expect(editor.commands.setPageBreak()).toBe(false);
  });
});

describe('indent and outdent', () => {
  it('step a paragraph in and out, within bounds', () => {
    const editor = editorWith({ type: 'doc', content: [paragraph('text')] });
    editor.commands.setTextSelection(2);
    const indent = () => editor.getJSON().content![0]!.attrs!.indent;

    editor.commands.indent();
    editor.commands.indent();
    expect(indent()).toBe(2);

    editor.commands.outdent();
    editor.commands.outdent();
    expect(editor.commands.outdent()).toBe(false);
    expect(indent()).toBe(0);

    for (let i = 0; i < MAX_INDENT + 3; i += 1) editor.commands.indent();
    expect(indent()).toBe(MAX_INDENT);
  });

  it('nest a list item instead of indenting its paragraph', () => {
    const item = (value: string) => ({ type: 'listItem', content: [paragraph(value)] });
    const editor = editorWith({
      type: 'doc',
      content: [{ type: 'bulletList', content: [item('one'), item('two')] }],
    });
    editor.commands.setTextSelection(10);

    expect(editor.commands.indent()).toBe(true);
    const list = editor.getJSON().content![0] as JSONContent;
    const first = list.content![0] as JSONContent;
    expect(list.content).toHaveLength(1);
    expect(first.content![1]!.type).toBe('bulletList');
  });
});

describe('line height', () => {
  it('is set on the paragraph, not as a mark on its text', () => {
    const editor = editorWith({ type: 'doc', content: [paragraph('text')] });
    editor.commands.setTextSelection(2);
    editor.commands.setLineHeight('1.5');

    const block = editor.getJSON().content![0]!;
    expect(block.attrs!.lineHeight).toBe('1.5');
    expect(block.content![0]!.marks).toBeUndefined();
  });
});

describe('link addresses', () => {
  it.each([
    ['example.com', 'https://example.com/'],
    ['http://example.com/a?b=c', 'http://example.com/a?b=c'],
    ['  https://example.com/x  ', 'https://example.com/x'],
    ['name@example.com', 'mailto:name@example.com'],
    ['mailto:name@example.com', 'mailto:name@example.com'],
    ['localhost:5173', null],
    ['http://localhost:5173/', 'http://localhost:5173/'],
  ])('%s becomes %s', (typed, expected) => {
    expect(normalizeHref(typed)).toBe(expected);
  });

  it.each(['', 'javascript:alert(1)', 'data:text/html,hi', 'ftp://example.com', 'notahost', 'mailto:nobody'])(
    'refuses %j',
    (typed) => {
      expect(normalizeHref(typed)).toBeNull();
    },
  );

  it('shows a mail address without its scheme', () => {
    expect(linkText('mailto:name@example.com')).toBe('name@example.com');
    expect(linkText('https://example.com/')).toBe('https://example.com/');
  });
});
