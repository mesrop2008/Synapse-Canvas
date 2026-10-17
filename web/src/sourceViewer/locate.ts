import type { Citation, SourcePassage, SourceText } from '../types/api';

/** What the viewer scrolls to and highlights. */
export interface Target {
  /** Null when the passage is no longer among the source's passages. */
  passageId: string | null;
  start: number;
  end: number;
  /** For a paged format: where the passage starts. */
  page: number | null;
  /** The passage itself, for formats matched by text rather than offset. */
  excerpt: string;
}

/** The page holding offset `at`, or null for a format without pages. */
export function pageAt(text: SourceText, at: number): number | null {
  let page: number | null = null;
  for (const entry of text.pages) {
    if (entry.start > at) break;
    page = entry.number;
  }
  return page;
}

export function passageTarget(text: SourceText, passage: SourcePassage): Target {
  return {
    passageId: passage.id,
    start: passage.char_start,
    end: passage.char_end,
    page: passage.page_number ?? pageAt(text, passage.char_start),
    excerpt: text.text.slice(passage.char_start, passage.char_end),
  };
}

/**
 * Where a citation points. The passage it was resolved to, by id, while the
 * source still has it; otherwise its stored page and offsets -- still right
 * unless the source was read again with different text -- or null when they
 * no longer fall inside the text.
 */
export function citationTarget(text: SourceText, citation: Citation): Target | null {
  const passage = text.passages.find((candidate) => candidate.id === citation.chunk_id);
  if (passage) return passageTarget(text, passage);

  const { char_start: start, char_end: end } = citation;
  if (start < 0 || end <= start || end > text.text.length) return null;
  return {
    passageId: null,
    start,
    end,
    page: citation.page_number ?? pageAt(text, start),
    excerpt: text.text.slice(start, end),
  };
}
