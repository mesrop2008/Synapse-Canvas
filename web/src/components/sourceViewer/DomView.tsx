import { useEffect, useLayoutEffect, useRef } from 'react';

import { clearAll, paint, rangesFor, scrollToRange } from '../../sourceViewer/highlights';
import type { Target } from '../../sourceViewer/locate';
import { Haystack, type Span } from '../../sourceViewer/match';
import type { SourceText } from '../../types/api';

export interface ViewState {
  target: Target | null;
  query: string;
  /** The search match to show, of those found. */
  current: number;
  onMatches: (count: number) => void;
  showChunks: boolean;
  zoom: number;
}

interface DomViewProps extends ViewState {
  text: SourceText;
  /** A Word file's rendering. Without it the extracted text is shown, in
   *  which chunks' offsets point exactly. */
  html: string | null;
}

/** Where the text between two offsets into the extracted text is shown.
 *  Exact for the text itself; for a Word file's rendering, found by its
 *  words, near where its offset falls in proportion. */
function locate(
  haystack: Haystack | null,
  text: SourceText,
  exact: boolean,
  start: number,
  end: number,
): Span | null {
  if (exact) return { start, end };
  if (!haystack) return null;
  const near = Math.round((start / Math.max(text.text.length, 1)) * haystack.text.length);
  return haystack.findChunk(text.text.slice(start, end), { near });
}

/** Text, Markdown and Word files: the text in the page, highlighted. */
export function DomView({ text, html, target, query, current, onMatches, showChunks, zoom }: DomViewProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const haystack = useRef<Haystack | null>(null);
  const exact = html === null;

  useLayoutEffect(() => {
    haystack.current = new Haystack(exact ? text.text : (root.current?.textContent ?? ''));
  }, [exact, text, html]);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const chunks = showChunks
      ? text.chunks.map((chunk) => ({
          chunk,
          span: locate(haystack.current, text, exact, chunk.char_start, chunk.char_end),
        }))
      : [];
    const parity = (even: boolean) =>
      chunks
        .filter(({ chunk, span }) => span && chunk.chunk_index % 2 === (even ? 0 : 1))
        .map(({ span }) => span!);
    paint('source-chunk-even', rangesFor(element, parity(true)));
    paint('source-chunk-odd', rangesFor(element, parity(false)));
  }, [showChunks, text, exact, html]);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const span = target ? locate(haystack.current, text, exact, target.start, target.end) : null;
    const [range] = span ? rangesFor(element, [span]) : [];
    paint('source-cited', range ? [range] : []);
    if (range && scroller.current) scrollToRange(scroller.current, range);
  }, [target, text, exact, html]);

  useLayoutEffect(() => {
    const element = root.current;
    if (!element || !haystack.current) return;
    const matches = query.trim() ? haystack.current.findAll(query) : [];
    onMatches(matches.length);
    paint('source-match', rangesFor(element, matches));
    const active = matches[Math.min(current, matches.length - 1)];
    const [range] = active ? rangesFor(element, [active]) : [];
    paint('source-match-current', range ? [range] : []);
    if (range && scroller.current) scrollToRange(scroller.current, range);
  }, [query, current, onMatches, text, html]);

  useEffect(() => clearAll, []);

  return (
    <div className="viewer-scroll" ref={scroller}>
      {exact ? (
        <div ref={root} className="viewer-text" style={{ fontSize: `${zoom}em` }}>
          {text.text}
        </div>
      ) : (
        <div
          ref={root}
          className="viewer-html"
          style={{ fontSize: `${zoom}em` }}
          // Built by the server from the file's import, every text escaped:
          // api/services/prosemirror_html.py.
          dangerouslySetInnerHTML={{ __html: html }}
        />
      )}
    </div>
  );
}
