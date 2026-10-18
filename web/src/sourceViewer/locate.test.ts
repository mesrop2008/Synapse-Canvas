import { describe, expect, it } from 'vitest';

import type { Citation, SourceText } from '../types/api';
import { citationTarget, pageAt, chunkTarget } from './locate';
import { Haystack, markedHtml } from './match';

const PAGE_ONE = 'Field study of urban heat islands. The densest districts were warmer.';
const PAGE_TWO = 'Methods and limitations. Wind speed was not controlled for.';
const TEXT = `${PAGE_ONE}\n\n${PAGE_TWO}`;
const SECOND = TEXT.indexOf('Wind speed');

const text: SourceText = {
  source_id: 'source',
  format: 'pdf',
  text: TEXT,
  pages: [
    { number: 1, start: 0 },
    { number: 2, start: PAGE_ONE.length + 2 },
  ],
  chunks: [
    { id: 'p0', chunk_index: 0, char_start: 0, char_end: PAGE_ONE.length, page_number: 1, token_count: 12 },
    { id: 'p1', chunk_index: 1, char_start: SECOND, char_end: TEXT.length, page_number: 2, token_count: 8 },
  ],
  html: null,
};

const citation = (overrides: Partial<Citation> = {}): Citation => ({
  marker: 1,
  chunk_id: 'p1',
  source_id: 'source',
  filename: 'study.pdf',
  page_number: 2,
  char_start: SECOND,
  char_end: TEXT.length,
  ...overrides,
});

describe('a citation', () => {
  it('resolves to its chunk, page and offsets', () => {
    const target = citationTarget(text, citation());

    expect(target).toEqual({
      chunkId: 'p1',
      start: SECOND,
      end: TEXT.length,
      page: 2,
      excerpt: 'Wind speed was not controlled for.',
    });
  });

  it('whose chunk is gone falls back on its own page and offsets', () => {
    const target = citationTarget(text, citation({ chunk_id: 'reindexed', page_number: null }));

    expect(target?.chunkId).toBeNull();
    expect(target?.page).toBe(2);
    expect(target?.excerpt).toBe('Wind speed was not controlled for.');
  });

  it('pointing past the text resolves to nothing', () => {
    expect(
      citationTarget(text, citation({ chunk_id: 'reindexed', char_start: 10, char_end: 9_999 })),
    ).toBeNull();
  });
});

describe('pages', () => {
  it('hold the offsets between their starts', () => {
    expect(pageAt(text, 0)).toBe(1);
    expect(pageAt(text, PAGE_ONE.length)).toBe(1);
    expect(pageAt(text, SECOND)).toBe(2);
    expect(pageAt({ ...text, pages: [] }, 5)).toBeNull();
  });

  it('come from the chunk when it has one', () => {
    expect(chunkTarget(text, text.chunks[0]!).page).toBe(1);
  });
});

describe('matching a chunk in differently extracted text', () => {
  it('ignores spacing, line breaks, case and the bars between table cells', () => {
    const shown = 'DISTRICT\nComplaints  North\t41';
    const found = new Haystack(shown).findChunk('District | Complaints | North | 41');

    expect(found).toMatchObject({ start: 0, end: shown.length, found: 1, rest: '' });
  });

  it('finds as much of a chunk as a page holds, and says what is left', () => {
    const found = new Haystack(PAGE_ONE).findChunk(TEXT);

    expect(found?.end).toBe(PAGE_ONE.length);
    expect(found?.rest.trim()).toBe(PAGE_TWO);
  });

  it('picks the repeat nearest where the chunk is expected', () => {
    const repeated = 'Summary of findings. Body text. Summary of findings.';
    const near = new Haystack(repeated).findChunk('Summary of findings.', { near: 40 });

    expect(near?.start).toBe(repeated.lastIndexOf('Summary'));
  });

  it('finds nothing when too little of it is there', () => {
    expect(new Haystack(PAGE_ONE).findChunk('Entirely different words here')).toBeNull();
  });

  it('finds every match for a search', () => {
    expect(new Haystack(TEXT).findAll('the').length).toBe(1);
    expect(new Haystack(TEXT).findAll('  ')).toEqual([]);
  });
});

describe('marking a PDF text item', () => {
  it('wraps what falls inside each mark and escapes the rest', () => {
    const html = markedHtml('a <b> & c', 10, [{ start: 12, end: 15, className: 'viewer-cited' }]);

    expect(html).toBe('a <mark class="viewer-cited">&lt;b&gt;</mark> &amp; c');
  });
});
