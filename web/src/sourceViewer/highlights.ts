import type { Span } from './match';

/** Painted with the CSS Custom Highlight API, which marks ranges without
 *  touching the DOM -- React's, or the server's HTML. Where a browser lacks
 *  it, nothing is painted and scrolling still finds the place. */
export type HighlightName =
  | 'source-cited'
  | 'source-match'
  | 'source-match-current'
  | 'source-passage-even'
  | 'source-passage-odd';

/** Offsets into `root.textContent` as ranges, in the order given. */
export function rangesFor(root: Node, spans: Span[]): Range[] {
  const nodes: Text[] = [];
  const starts: number[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let total = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    nodes.push(node as Text);
    starts.push(total);
    total += (node as Text).data.length;
  }

  const locate = (offset: number, preferNext: boolean): [Text, number] | null => {
    let low = 0;
    let high = nodes.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (starts[middle]! <= offset) low = middle;
      else high = middle - 1;
    }
    const node = nodes[low];
    if (!node) return null;
    const within = offset - starts[low]!;
    // At a node's end, a start belongs to the next node.
    if (preferNext && within === node.data.length && nodes[low + 1]) return [nodes[low + 1]!, 0];
    return [node, Math.min(within, node.data.length)];
  };

  const ranges: Range[] = [];
  for (const span of spans) {
    const start = locate(span.start, true);
    const end = locate(span.end, false);
    if (!start || !end) continue;
    const range = document.createRange();
    range.setStart(...start);
    range.setEnd(...end);
    ranges.push(range);
  }
  return ranges;
}

function registry(): HighlightRegistry | null {
  return typeof CSS !== 'undefined' && 'highlights' in CSS ? CSS.highlights : null;
}

export function paint(name: HighlightName, ranges: Range[]): void {
  const highlights = registry();
  if (!highlights) return;
  if (ranges.length) highlights.set(name, new Highlight(...ranges));
  else highlights.delete(name);
}

export function clearAll(): void {
  const highlights = registry();
  if (!highlights) return;
  for (const name of [
    'source-cited',
    'source-match',
    'source-match-current',
    'source-passage-even',
    'source-passage-odd',
  ] satisfies HighlightName[]) {
    highlights.delete(name);
  }
}

/** Brings `range` to a third of the way down `container`. */
export function scrollToRange(container: HTMLElement, range: Range): void {
  const rect = range.getBoundingClientRect();
  const box = container.getBoundingClientRect();
  container.scrollTo({
    top: container.scrollTop + rect.top - box.top - container.clientHeight / 3,
    behavior: 'smooth',
  });
}
