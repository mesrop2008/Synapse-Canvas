import { Svg, type IconProps } from './icons';

/** The formatting toolbar's glyphs, on the same 24-unit grid as icons.tsx. */

const icon = (paths: string) =>
  function EditorIcon(props: IconProps) {
    return (
      <Svg {...props}>
        <path d={paths} />
      </Svg>
    );
  };

export const BoldIcon = (props: IconProps) => (
  <Svg {...props}>
    <path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z" strokeWidth={2.3} />
  </Svg>
);
export const ItalicIcon = icon('M19 4h-9M14 20H5M15 4 9 20');
export const UnderlineIcon = icon('M6 4v6a6 6 0 0 0 12 0V4M4 20h16');
export const StrikeIcon = icon(
  'M16 6.5C15 5 13.6 4.5 12 4.5c-2.5 0-4.5 1.3-4.5 3.4 0 1.5.9 2.5 2.7 3.1M4 12h16M8 17.5c1 1.4 2.5 2 4.2 2 2.6 0 4.6-1.4 4.6-3.5 0-.8-.2-1.4-.6-2',
);
export const InlineCodeIcon = icon('m8 7-5 5 5 5M16 7l5 5-5 5');
export const SuperscriptIcon = icon(
  'm4 19 8-8M12 19l-8-8M20 12h-4c0-1.5.44-2 1.5-2.5S20 8.33 20 7c0-.47-.17-.93-.48-1.29a2.11 2.11 0 0 0-2.62-.44c-.42.24-.74.62-.9 1.07',
);
export const SubscriptIcon = icon(
  'm4 5 8 8M12 5l-8 8M20 19h-4c0-1.5.44-2 1.5-2.5S20 15.33 20 14c0-.47-.17-.93-.48-1.29a2.11 2.11 0 0 0-2.62-.44c-.42.24-.74.62-.9 1.07',
);

/** The bar under the letter shows the colour that is set. */
export const TextColourIcon = ({ swatch, ...props }: IconProps & { swatch?: string }) => (
  <Svg {...props}>
    <path d="m6 15 6-11 6 11M8.2 11h7.6" />
    <rect x="4" y="18" width="16" height="3" rx="1" fill={swatch ?? 'currentColor'} stroke="none" />
  </Svg>
);
export const HighlightIcon = ({ swatch, ...props }: IconProps & { swatch?: string }) => (
  <Svg {...props}>
    <path d="m9 9.5-4 4V16h6l2.5-2.5M19 8.5l-4.1 4.1a1.6 1.6 0 0 1-2.3 0l-3.2-3.2a1.6 1.6 0 0 1 0-2.3L13.5 3" />
    <rect x="4" y="18" width="16" height="3" rx="1" fill={swatch ?? 'currentColor'} stroke="none" />
  </Svg>
);

export const AlignLeftIcon = icon('M21 6H3M15 12H3M17 18H3');
export const AlignCenterIcon = icon('M21 6H3M17 12H7M19 18H5');
export const AlignRightIcon = icon('M21 6H3M21 12H9M21 18H7');
export const AlignJustifyIcon = icon('M3 6h18M3 12h18M3 18h18');
export const IndentIcon = icon('M21 5H11M21 12H11M21 19H11M3 8l4 4-4 4');
export const OutdentIcon = icon('M21 5H11M21 12H11M21 19H11M7 8l-4 4 4 4');

export const BulletListIcon = icon('M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01');
export const OrderedListIcon = icon(
  'M10 6h11M10 12h11M10 18h11M4 4h1v4M4 8h2M6 19H4c0-1 2-1.6 2-2.7 0-.6-.5-1.3-1.2-1.3-.5 0-.9.2-1 .6',
);
export const TaskListIcon = icon('m3 7 2 2 4-4M3 17l2 2 4-4M13 6h8M13 12h8M13 18h8');
export const QuoteIcon = icon('M6 17h3l2-4V7H5v6h3zM14 17h3l2-4V7h-6v6h3z');
export const CodeBlockIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="m10 9-3 3 3 3M14 15l3-3-3-3" />
  </Svg>
);
export const RuleIcon = icon('M3 12h18M7 7h10M7 17h10');
export const LinkIcon = icon(
  'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
);
export const PageBreakIcon = icon('M5 3v5h14V3M5 21v-5h14v5M3 12h2M8 12h2M14 12h2M19 12h2');

const Grid = ({ children, ...props }: IconProps & { children?: React.ReactNode }) => (
  <Svg {...props}>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    {children}
  </Svg>
);
export const TableIcon = (props: IconProps) => (
  <Grid {...props}>
    <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
  </Grid>
);
export const MergeCellsIcon = (props: IconProps) => (
  <Grid {...props}>
    <path d="M12 3v5M12 16v5M8 12h8M14 10l2 2-2 2M10 10l-2 2 2 2" />
  </Grid>
);
export const SplitCellIcon = (props: IconProps) => (
  <Grid {...props}>
    <path d="M12 3v18" />
  </Grid>
);
export const HeaderRowIcon = (props: IconProps) => (
  <Grid {...props}>
    <path d="M3 9h18M5 6h14" />
  </Grid>
);
export const DeleteTableIcon = (props: IconProps) => (
  <Grid {...props}>
    <path d="m9 9 6 6M15 9l-6 6" />
  </Grid>
);

export const RowBeforeIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="3" y="12" width="18" height="9" rx="2" />
    <path d="M12 3v6M9 6h6" />
  </Svg>
);
export const RowAfterIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="3" y="3" width="18" height="9" rx="2" />
    <path d="M12 15v6M9 18h6" />
  </Svg>
);
export const DeleteRowIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="3" y="7" width="18" height="10" rx="2" />
    <path d="m10 10 4 4M14 10l-4 4" />
  </Svg>
);
export const ColumnBeforeIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="12" y="3" width="9" height="18" rx="2" />
    <path d="M3 12h6M6 9v6" />
  </Svg>
);
export const ColumnAfterIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="3" y="3" width="9" height="18" rx="2" />
    <path d="M15 12h6M18 9v6" />
  </Svg>
);
export const DeleteColumnIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="7" y="3" width="10" height="18" rx="2" />
    <path d="m10 10 4 4M14 10l-4 4" />
  </Svg>
);

export const MoreIcon = (props: IconProps) => (
  <Svg {...props}>
    <circle cx="5" cy="12" r="1.2" fill="currentColor" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" />
    <circle cx="19" cy="12" r="1.2" fill="currentColor" />
  </Svg>
);
export const KeyboardIcon = (props: IconProps) => (
  <Svg {...props}>
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M10 13h4M7 16h10" />
  </Svg>
);
export const NoColourIcon = icon('M4.9 4.9l14.2 14.2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z');
