import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

import { useI18n } from '../../hooks/useI18n';
import { Haystack, markedHtml, type Span } from '../../sourceViewer/match';
import type { SourceText } from '../../types/api';
import type { ViewState } from './DomView';

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

interface PdfViewProps extends ViewState {
  file: Blob;
  /** Null while the source is unread: the pages still show, unmarked. */
  text: SourceText | null;
  page: number;
  onPage: (page: number) => void;
  onPageCount: (count: number) => void;
  onError: () => void;
}

type Mark = Span & { className: string };

interface PageText {
  text: string;
  /** Where each text item starts in `text`, by item index. */
  offsets: number[];
}

async function pageText(pdf: PDFDocumentProxy, number: number): Promise<PageText> {
  const content = await (await pdf.getPage(number)).getTextContent();
  const offsets: number[] = [];
  let text = '';
  for (const item of content.items) {
    offsets.push(text.length);
    if ('str' in item) text += item.str;
  }
  return { text, offsets };
}

/** The cited chunk on each page it covers: found on its first page, and
 *  whatever of it was not, at the top of the pages after. */
function citedSpans(pages: PageText[], first: number, excerpt: string): Map<number, Span> {
  const spans = new Map<number, Span>();
  let rest = excerpt;
  for (let number = first; number <= pages.length && rest.trim(); number += 1) {
    const found = new Haystack(pages[number - 1]!.text).findChunk(rest, { minimum: 12 });
    if (!found) break;
    spans.set(number, found);
    rest = found.rest;
  }
  return spans;
}

export default function PdfView({
  file,
  text,
  target,
  query,
  current,
  onMatches,
  showChunks,
  zoom,
  page,
  onPage,
  onPageCount,
  onError,
}: PdfViewProps) {
  const { t } = useI18n();
  const body = useRef<HTMLDivElement | null>(null);
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pages, setPages] = useState<PageText[] | null>(null);

  const attach = useCallback((element: HTMLDivElement | null) => {
    body.current = element;
    setScroller(element);
  }, []);

  // Measured as soon as the element exists, not only when it next resizes.
  useEffect(() => {
    if (!scroller) return;
    setWidth(scroller.clientWidth);
    const observer = new ResizeObserver(() => setWidth(scroller.clientWidth));
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [scroller]);

  // Every page's text, once: search spans the whole file, and a citation
  // may run from one page onto the next.
  useEffect(() => {
    if (!pdf) return;
    let cancelled = false;
    void Promise.all(
      Array.from({ length: pdf.numPages }, (_, index) => pageText(pdf, index + 1)),
    ).then((loaded) => {
      if (!cancelled) setPages(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [pdf]);

  const matches = useMemo(() => {
    if (!pages || !query.trim()) return [];
    return pages.flatMap((entry, index) =>
      new Haystack(entry.text).findAll(query).map((span) => ({ page: index + 1, span })),
    );
  }, [pages, query]);

  useEffect(() => onMatches(matches.length), [matches, onMatches]);

  const active = matches.length ? matches[Math.min(current, matches.length - 1)]! : null;
  // Turns to a match when it becomes the current one, and only then, so
  // paging by hand still works while a search is open.
  const followed = useRef<typeof active>(null);
  useEffect(() => {
    if (!active || active === followed.current) return;
    followed.current = active;
    if (active.page !== page) onPage(active.page);
  });

  const cited = useMemo(
    () => (pages && target?.page ? citedSpans(pages, target.page, target.excerpt) : null),
    [pages, target],
  );

  const marks = useMemo(() => {
    if (!pages?.[page - 1]) return [];
    const found: Mark[] = [];
    if (showChunks && text) {
      const haystack = new Haystack(pages[page - 1]!.text);
      for (const chunk of text.chunks) {
        if (chunk.page_number !== page) continue;
        const span = haystack.findChunk(text.text.slice(chunk.char_start, chunk.char_end), {
          minimum: 12,
        });
        if (span) {
          const parity = chunk.chunk_index % 2 ? 'odd' : 'even';
          found.push({ ...span, className: `viewer-chunk-${parity}` });
        }
      }
    }
    const citedHere = cited?.get(page);
    if (citedHere) found.push({ ...citedHere, className: 'viewer-cited' });
    for (const match of matches) {
      if (match.page !== page) continue;
      const className = match === active ? 'viewer-match viewer-match-current' : 'viewer-match';
      found.push({ ...match.span, className });
    }
    return found;
  }, [pages, page, showChunks, text, cited, matches, active]);

  const renderText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) =>
      marks.length ? markedHtml(str, pages?.[page - 1]?.offsets[itemIndex] ?? 0, marks) : str,
    [marks, pages, page],
  );

  const reveal = useCallback(() => {
    const mark = body.current?.querySelector('mark.viewer-match-current, mark.viewer-cited');
    mark?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, []);

  return (
    <div className="viewer-scroll viewer-pdf" ref={attach}>
      <Document
        file={file}
        onLoadSuccess={(loaded) => {
          setPdf(loaded);
          onPageCount(loaded.numPages);
        }}
        onLoadError={onError}
        loading={<p className="placeholder">{t('sources.viewer.loading')}</p>}
        error={<span />}
        noData={<span />}
      >
        {width > 0 && (
          <Page
            pageNumber={page}
            width={Math.max(width - 32, 200) * zoom}
            customTextRenderer={renderText}
            onRenderTextLayerSuccess={reveal}
            loading={<p className="placeholder">{t('sources.viewer.loading')}</p>}
          />
        )}
      </Document>
    </div>
  );
}
