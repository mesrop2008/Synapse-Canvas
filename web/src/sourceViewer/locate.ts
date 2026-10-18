import type { Citation, SourceTextChunk, SourceText } from '../types/api';

/** What the viewer scrolls to and highlights. */
export interface Target {
  /** Null when the chunk is no longer among the source's chunks. */
  chunkId: string | null;
  start: number;
  end: number;
  /** For a paged format: where the chunk starts. */
  page: number | null;
  /** The chunk itself, for formats matched by text rather than offset. */
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

export function chunkTarget(text: SourceText, chunk: SourceTextChunk): Target {
  return {
    chunkId: chunk.id,
    start: chunk.char_start,
    end: chunk.char_end,
    page: chunk.page_number ?? pageAt(text, chunk.char_start),
    excerpt: text.text.slice(chunk.char_start, chunk.char_end),
  };
}

/**
 * Where a citation points. The chunk it was resolved to, by id, while the
 * source still has it; otherwise its stored page and offsets -- still right
 * unless the source was read again with different text -- or null when they
 * no longer fall inside the text.
 */
export function citationTarget(text: SourceText, citation: Citation): Target | null {
  const chunk = text.chunks.find((candidate) => candidate.id === citation.chunk_id);
  if (chunk) return chunkTarget(text, chunk);

  const { char_start: start, char_end: end } = citation;
  if (start < 0 || end <= start || end > text.text.length) return null;
  return {
    chunkId: null,
    start,
    end,
    page: citation.page_number ?? pageAt(text, start),
    excerpt: text.text.slice(start, end),
  };
}
