import {
  useCallback,
  useEffect,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from 'react';

const STORAGE_KEY = 'synapse.ai_panel_width';
export const DEFAULT_WIDTH = 480;
const MIN_WIDTH = 340;
/** Of the layout's width; the editor keeps the rest. Matches the CSS cap. */
const MAX_SHARE = 0.6;
const KEY_STEP = 32;

function readStoredWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(stored) && stored >= MIN_WIDTH) return stored;
  } catch {
    /* storage blocked; use the default */
  }
  return DEFAULT_WIDTH;
}

/** The side panel's width, remembered for this browser. */
export function usePanelWidth() {
  const [width, setWidth] = useState(readStoredWidth);

  const save = useCallback((next: number) => {
    setWidth(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(Math.round(next)));
    } catch {
      /* the width just will not survive a reload */
    }
  }, []);

  return [width, save] as const;
}

/**
 * A handle on the panel's left edge: dragging left widens the panel. Also
 * works from the keyboard, and a double click restores the default width.
 */
export function PanelResizer({
  layout,
  width,
  onResize,
  label,
}: {
  layout: RefObject<HTMLElement | null>;
  width: number;
  onResize: (width: number) => void;
  label: string;
}) {
  const [dragging, setDragging] = useState(false);

  const measureMax = useCallback(() => {
    const element = layout.current;
    if (!element) return Math.max(MIN_WIDTH, window.innerWidth * MAX_SHARE);
    // The CSS percentage is of the content box.
    const style = getComputedStyle(element);
    const content =
      element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return Math.max(MIN_WIDTH, content * MAX_SHARE);
  }, [layout]);

  // For the ARIA values. Measured after the parent has attached its ref.
  const [shownMax, setShownMax] = useState(MIN_WIDTH);
  useEffect(() => {
    const update = () => setShownMax(measureMax());
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, [measureMax]);

  const maxWidth = () => {
    const max = measureMax();
    if (max !== shownMax) setShownMax(max);
    return max;
  };
  const clamp = (next: number) => Math.min(maxWidth(), Math.max(MIN_WIDTH, next));

  function startDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    // What is shown, which the CSS cap may hold below the stored width.
    const startWidth = Math.min(width, maxWidth());
    setDragging(true);

    const move = (moved: globalThis.PointerEvent) =>
      onResize(clamp(startWidth + startX - moved.clientX));
    const stop = () => {
      setDragging(false);
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const shown = Math.min(width, maxWidth());
    const next =
      event.key === 'ArrowLeft'
        ? shown + KEY_STEP
        : event.key === 'ArrowRight'
          ? shown - KEY_STEP
          : event.key === 'Home'
            ? MIN_WIDTH
            : event.key === 'End'
              ? maxWidth()
              : null;
    if (next === null) return;
    event.preventDefault();
    onResize(clamp(next));
  }

  return (
    <div
      className="panel-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={Math.round(shownMax)}
      aria-valuenow={Math.round(Math.min(width, shownMax))}
      tabIndex={0}
      data-dragging={dragging || undefined}
      onPointerDown={startDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onResize(clamp(DEFAULT_WIDTH))}
    />
  );
}
