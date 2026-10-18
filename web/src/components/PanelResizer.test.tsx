// @vitest-environment jsdom
import { useRef } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DEFAULT_WIDTH, PanelResizer, usePanelWidth } from './PanelResizer';

/** jsdom lays nothing out, so the layout's width is set by hand. */
const LAYOUT_WIDTH = 1500;

function Harness() {
  const layout = useRef<HTMLElement | null>(null);
  const [width, setWidth] = usePanelWidth();
  return (
    <main
      ref={(element) => {
        layout.current = element;
        if (element) Object.defineProperty(element, 'clientWidth', { value: LAYOUT_WIDTH });
      }}
    >
      <PanelResizer layout={layout} width={width} onResize={setWidth} label="resize" />
    </main>
  );
}

const width = () => Number(screen.getByRole('separator').getAttribute('aria-valuenow'));

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe('the assistant panel', () => {
  it('starts at the default width', () => {
    render(<Harness />);
    expect(width()).toBe(DEFAULT_WIDTH);
  });

  it('widens from the keyboard, up to its share of the layout, and remembers it', () => {
    render(<Harness />);
    const handle = screen.getByRole('separator');

    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(width()).toBeGreaterThan(DEFAULT_WIDTH);

    fireEvent.keyDown(handle, { key: 'End' });
    expect(width()).toBe(LAYOUT_WIDTH * 0.6);
    expect(window.localStorage.getItem('synapse.ai_panel_width')).toBe(String(LAYOUT_WIDTH * 0.6));

    cleanup();
    render(<Harness />);
    expect(width()).toBe(LAYOUT_WIDTH * 0.6);
  });

  it('never narrows past its minimum, and a double click restores the default', () => {
    render(<Harness />);
    const handle = screen.getByRole('separator');

    fireEvent.keyDown(handle, { key: 'Home' });
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(width()).toBe(340);

    fireEvent.doubleClick(handle);
    expect(width()).toBe(DEFAULT_WIDTH);
  });
});
