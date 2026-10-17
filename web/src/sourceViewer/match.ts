/** Finding a passage in text that was extracted differently: pypdf on the
 *  server and pdf.js here disagree about spaces and line breaks, and a
 *  rendered Word file joins its blocks differently from the extracted
 *  text, whose table cells are joined by " | ". Matching ignores
 *  whitespace, those bars and case, and maps back. */

export interface Span {
  start: number;
  end: number;
}

export interface PassageMatch extends Span {
  /** How much of the passage was found, from its start, as a fraction. A
   *  passage running on past its page is found only in part. */
  found: number;
  /** The passage after the part found, to look for on the next page. */
  rest: string;
}

interface Squeezed {
  text: string;
  /** For each character of `text`, its index in the original. */
  origin: number[];
}

const IGNORED = /[\s­​|]/;

function squeeze(source: string): Squeezed {
  let text = '';
  const origin: number[] = [];
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]!;
    if (IGNORED.test(char)) continue;
    // Some characters fold to two, e.g. "İ".
    for (const folded of char.toLocaleLowerCase()) {
      text += folded;
      origin.push(index);
    }
  }
  return { text, origin };
}

/** Text to search many times: squeezed once. */
export class Haystack {
  private readonly squeezed: Squeezed;

  constructor(readonly text: string) {
    this.squeezed = squeeze(text);
  }

  private span(at: number, length: number): Span {
    const { origin } = this.squeezed;
    return { start: origin[at]!, end: origin[at + length - 1]! + 1 };
  }

  /** Every occurrence of `query`, in this text's own indices. */
  findAll(query: string): Span[] {
    const needle = squeeze(query).text;
    if (!needle) return [];
    const found: Span[] = [];
    let at = this.squeezed.text.indexOf(needle);
    while (at >= 0) {
      found.push(this.span(at, needle.length));
      at = this.squeezed.text.indexOf(needle, at + needle.length);
    }
    return found;
  }

  /**
   * Where `passage` is -- or, failing that, the longest start of it that is
   * here. Null when not even `minimum` characters of it are. `near` picks
   * between repeats, by distance from that index.
   */
  findPassage(passage: string, options: { minimum?: number; near?: number } = {}): PassageMatch | null {
    const needle = squeeze(passage);
    if (!needle.text) return null;
    const haystack = this.squeezed.text;
    let low = Math.min(options.minimum ?? 16, needle.text.length);
    let high = needle.text.length;
    if (haystack.indexOf(needle.text.slice(0, low)) < 0) return null;
    // Whether a start of some length is found holds up to one length.
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (haystack.indexOf(needle.text.slice(0, middle)) >= 0) low = middle;
      else high = middle - 1;
    }

    const prefix = needle.text.slice(0, low);
    let best = haystack.indexOf(prefix);
    if (options.near !== undefined) {
      const distance = (at: number) => Math.abs(this.squeezed.origin[at]! - options.near!);
      for (let at = best; at >= 0; at = haystack.indexOf(prefix, at + 1)) {
        if (distance(at) < distance(best)) best = at;
      }
    }
    const restFrom = low < needle.text.length ? needle.origin[low]! : passage.length;
    return {
      ...this.span(best, low),
      found: low / needle.text.length,
      rest: passage.slice(restFrom),
    };
  }
}

/** `text` as HTML with the parts inside `marks` wrapped, for pdf.js's text
 *  layer. `offset` is where `text` starts in the text the spans index. */
export function markedHtml(
  text: string,
  offset: number,
  marks: Array<Span & { className: string }>,
): string {
  const cuts = new Set([0, text.length]);
  for (const mark of marks) {
    cuts.add(Math.min(Math.max(mark.start - offset, 0), text.length));
    cuts.add(Math.min(Math.max(mark.end - offset, 0), text.length));
  }
  const points = [...cuts].sort((a, b) => a - b);
  let html = '';
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index]!;
    const to = points[index + 1]!;
    const piece = escapeHtml(text.slice(from, to));
    const classes = marks
      .filter((mark) => mark.start - offset <= from && mark.end - offset >= to)
      .map((mark) => mark.className);
    html += classes.length ? `<mark class="${classes.join(' ')}">${piece}</mark>` : piece;
  }
  return html;
}

function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}
