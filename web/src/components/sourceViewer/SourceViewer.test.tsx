// @vitest-environment jsdom
import { useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MESSAGES } from '../../../i18n';
import { getSource, getSourceFile, getSourceText } from '../../api/sources';
import { saveFile } from '../../download';
import { I18nProvider } from '../../hooks/useI18n';
import type { Source, SourceText } from '../../types/api';
import { SourceViewerProvider, useSourceViewer, type ViewerRequest } from './SourceViewer';

vi.mock('../../api/sources', () => ({
  getSource: vi.fn(),
  getSourceText: vi.fn(),
  getSourceFile: vi.fn(),
}));
vi.mock('../../download', () => ({ saveFile: vi.fn() }));

const source = (contentType: string): Source => ({
  id: 'source',
  workspace_id: 'workspace',
  uploaded_by: null,
  filename: 'notes.txt',
  content_type: contentType,
  size_bytes: 120,
  checksum: 'x',
  status: 'ready',
  error_code: null,
  retryable: false,
  page_count: null,
  chunk_count: 2,
  created_at: '2026-10-18T00:00:00Z',
  processed_at: '2026-10-18T00:00:01Z',
});

const TEXT = 'First chunk about heat.\n\nSecond chunk about wind.';
const text: SourceText = {
  source_id: 'source',
  format: 'text',
  text: TEXT,
  pages: [],
  chunks: [
    { id: 'p0', chunk_index: 0, char_start: 0, char_end: 25, page_number: null, token_count: 5 },
    {
      id: 'p1',
      chunk_index: 1,
      char_start: 27,
      char_end: TEXT.length,
      page_number: null,
      token_count: 5,
    },
  ],
  html: null,
};

function Opener({ request }: { request: ViewerRequest }) {
  const open = useSourceViewer();
  useEffect(() => open(request), [open, request]);
  return null;
}

function show(request: ViewerRequest) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={client}>
        <SourceViewerProvider>
          <Opener request={request} />
        </SourceViewerProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

const request: ViewerRequest = { workspaceId: 'workspace', sourceId: 'source', filename: 'notes.txt' };

beforeEach(() => {
  // jsdom lays nothing out, so it has no scrolling to do.
  Element.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
  Range.prototype.getBoundingClientRect = () => new DOMRect();
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  window.localStorage.clear();
});

describe('a kind of file the viewer cannot show', () => {
  it.each([
    ['en', MESSAGES.en],
    ['ru', MESSAGES.ru],
  ] as const)('falls back to a download, explained in %s', async (locale, messages) => {
    window.localStorage.setItem('synapse.locale', locale);
    vi.mocked(getSource).mockResolvedValue(source('application/zip'));
    const blob = new Blob(['bytes']);
    vi.mocked(getSourceFile).mockResolvedValue({ blob, filename: 'notes.zip' });

    show(request);

    const message = await screen.findByText(messages.sources.viewer.unsupported);
    const fallback = message.closest('.viewer-fallback') as HTMLElement;
    fireEvent.click(within(fallback).getByRole('button', { name: messages.sources.viewer.download }));

    await waitFor(() => expect(saveFile).toHaveBeenCalledWith(blob, 'notes.zip'));
    expect(getSourceText).not.toHaveBeenCalled();
  });
});

describe('a text source opened from a citation', () => {
  it('shows the text and selects the cited chunk', async () => {
    vi.mocked(getSource).mockResolvedValue(source('text/plain'));
    vi.mocked(getSourceText).mockResolvedValue(text);

    show({
      ...request,
      citation: {
        marker: 1,
        chunk_id: 'p1',
        source_id: 'source',
        filename: 'notes.txt',
        page_number: null,
        char_start: 27,
        char_end: TEXT.length,
      },
    });

    const cited = await screen.findByRole('button', { current: true });
    expect(cited).toHaveProperty('dataset.chunk', 'p1');
    expect(document.querySelector('.viewer-text')?.textContent).toBe(TEXT);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('says so when the cited chunk is no longer there', async () => {
    vi.mocked(getSource).mockResolvedValue(source('text/plain'));
    vi.mocked(getSourceText).mockResolvedValue(text);

    show({
      ...request,
      citation: {
        marker: 1,
        chunk_id: 'reindexed',
        source_id: 'source',
        filename: 'notes.txt',
        page_number: null,
        char_start: 40,
        char_end: 9_999,
      },
    });

    expect(await screen.findByText(MESSAGES.en.sources.viewer.citationMissing)).toBeTruthy();
  });
});
