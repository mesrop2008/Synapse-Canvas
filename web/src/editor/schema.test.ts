import { getSchema, type JSONContent } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { Node } from '@tiptap/pm/model';
import { Step } from '@tiptap/pm/transform';
import { describe, expect, it } from 'vitest';

import { schemaExtensions } from './extensions';
import every from './fixtures/every-node.json';
import v1Document from './fixtures/v1-document.json';
import v2Edit from './fixtures/v2-edit.json';
import manifest from './schema.json';
import { SCHEMA_VERSION } from './schemaVersion';

const schema = getSchema(schemaExtensions());

function roundTrip(json: JSONContent): Node {
  const node = Node.fromJSON(schema, json);
  node.check();
  const again = Node.fromJSON(schema, node.toJSON());
  expect(again.eq(node)).toBe(true);
  return again;
}

const doc = (...content: JSONContent[]): JSONContent => ({ type: 'doc', content });
const paragraph = (...content: JSONContent[]): JSONContent => ({ type: 'paragraph', content });
type MarkJSON = NonNullable<JSONContent['marks']>[number];
const text = (value: string, ...marks: MarkJSON[]): JSONContent =>
  marks.length ? { type: 'text', text: value, marks } : { type: 'text', text: value };
const cell = (type: 'tableCell' | 'tableHeader', value: string, attrs = {}): JSONContent => ({
  type,
  attrs,
  content: [paragraph(text(value))],
});

describe('the manifest', () => {
  it('lists exactly the nodes, marks and attributes the extensions define', () => {
    const nodes = Object.fromEntries(
      Object.entries(schema.nodes).map(([name, type]) => [
        name,
        { attrs: Object.keys(type.spec.attrs ?? {}).sort(), leaf: type.isLeaf },
      ]),
    );
    const marks = Object.fromEntries(
      Object.entries(schema.marks).map(([name, type]) => [
        name,
        Object.keys(type.spec.attrs ?? {}).sort(),
      ]),
    );

    // Failing here means the schema changed: update schema.json and bump
    // its version, and SCHEMA_VERSION in api/services/prosemirror.py.
    expect({ nodes, marks }).toEqual({ nodes: manifest.nodes, marks: manifest.marks });
  });

  it('is version 2', () => {
    expect(SCHEMA_VERSION).toBe(2);
  });
});

describe('each new mark survives ProseMirror JSON', () => {
  const marks: Array<[string, Record<string, unknown> | null]> = [
    ['underline', null],
    ['strike', null],
    ['code', null],
    ['superscript', null],
    ['subscript', null],
    ['textStyle', { color: '#c0392b', fontFamily: 'Georgia', fontSize: '14pt' }],
    ['highlight', { color: '#fde68a' }],
    ['link', { href: 'https://example.com/a?b=c' }],
  ];

  it.each(marks)('%s', (type, attrs) => {
    const node = roundTrip(doc(paragraph(text('marked', attrs ? { type, attrs } : { type }))));
    const mark = node.firstChild!.firstChild!.marks[0]!;
    expect(mark.type.name).toBe(type);
    expect(mark.attrs).toMatchObject(attrs ?? {});
  });
});

describe('each new node and block attribute survives ProseMirror JSON', () => {
  const nodes: Array<[string, JSONContent]> = [
    ['pageBreak', { type: 'pageBreak' }],
    ['horizontalRule', { type: 'horizontalRule' }],
    ['blockquote', { type: 'blockquote', content: [paragraph(text('quoted'))] }],
    [
      'taskList',
      {
        type: 'taskList',
        content: [
          { type: 'taskItem', attrs: { checked: true }, content: [paragraph(text('done'))] },
          {
            type: 'taskItem',
            attrs: { checked: false },
            content: [
              paragraph(text('open')),
              {
                type: 'taskList',
                content: [{ type: 'taskItem', content: [paragraph(text('nested'))] }],
              },
            ],
          },
        ],
      },
    ],
    [
      'nested lists',
      {
        type: 'orderedList',
        attrs: { start: 4 },
        content: [
          {
            type: 'listItem',
            content: [
              paragraph(text('outer')),
              {
                type: 'bulletList',
                content: [{ type: 'listItem', content: [paragraph(text('inner'))] }],
              },
            ],
          },
        ],
      },
    ],
    [
      'table with a merged cell',
      {
        type: 'table',
        content: [
          { type: 'tableRow', content: [cell('tableHeader', 'a'), cell('tableHeader', 'b')] },
          { type: 'tableRow', content: [cell('tableCell', 'merged', { colspan: 2 })] },
        ],
      },
    ],
    [
      'alignment, indent and line height',
      {
        type: 'paragraph',
        attrs: { textAlign: 'justify', indent: 3, lineHeight: '1.5' },
        content: [text('formatted')],
      },
    ],
    [
      'an aligned heading',
      { type: 'heading', attrs: { level: 2, textAlign: 'right', indent: 1 }, content: [text('h')] },
    ],
  ];

  it.each(nodes)('%s', (_name, node) => {
    const parsed = roundTrip(doc(node));
    const back = parsed.toJSON().content[0];
    expect(back).toMatchObject(node);
  });
});

describe('the shared fixtures', () => {
  it('every-node.json is a valid, normalised version 2 document', () => {
    const node = Node.fromJSON(schema, every);
    node.check();
    expect(node.toJSON()).toEqual(every);
  });

  it('a document written before version 2 still parses', () => {
    const node = Node.fromJSON(schema, v1Document);
    node.check();
    expect(node.childCount).toBe(v1Document.content.length);
  });

  it('a version 2 edit applies to a document written before it', () => {
    let current = Node.fromJSON(schema, v1Document);
    for (const json of v2Edit.steps) {
      const result = Step.fromJSON(schema, json).apply(current);
      expect(result.failed).toBeNull();
      current = result.doc!;
    }
    current.check();
    expect(current.toJSON()).toEqual(v2Edit.doc);
    // The server counts positions itself; tests/test_editor_schema.py
    // checks its count against this one.
    expect(current.content.size).toBe(v2Edit.size);
    expect(current.toJSON().content.map((node: JSONContent) => node.type)).toContain('pageBreak');
  });

  it('the same edit cannot be read by a version 1 client, which is why versions are checked', () => {
    const v1Schema = getSchema([StarterKit.configure({ undoRedo: false })]);
    expect(() => v2Edit.steps.map((json) => Step.fromJSON(v1Schema, json))).toThrow();
  });
});
