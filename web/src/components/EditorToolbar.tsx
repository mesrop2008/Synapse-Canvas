import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react';
import { useEditorState, type Editor } from '@tiptap/react';

import { useI18n } from '../hooks/useI18n';
import { LINE_HEIGHTS } from '../editor/blockFormat';
import { ALIGNMENTS } from '../editor/extensions';
import { shortcutKeys, type ShortcutAction } from '../editor/shortcuts';
import type { MessageKey } from '../../i18n';
import type { IconProps } from './icons';
import {
  AlignCenterIcon,
  AlignJustifyIcon,
  AlignLeftIcon,
  AlignRightIcon,
  BoldIcon,
  BulletListIcon,
  CodeBlockIcon,
  ColumnAfterIcon,
  ColumnBeforeIcon,
  DeleteColumnIcon,
  DeleteRowIcon,
  DeleteTableIcon,
  HeaderRowIcon,
  HighlightIcon,
  IndentIcon,
  InlineCodeIcon,
  ItalicIcon,
  KeyboardIcon,
  LinkIcon,
  MergeCellsIcon,
  MoreIcon,
  NoColourIcon,
  OrderedListIcon,
  OutdentIcon,
  PageBreakIcon,
  QuoteIcon,
  RowAfterIcon,
  RowBeforeIcon,
  RuleIcon,
  SplitCellIcon,
  StrikeIcon,
  SubscriptIcon,
  SuperscriptIcon,
  TableIcon,
  TaskListIcon,
  TextColourIcon,
  UnderlineIcon,
} from './editorIcons';

/** Values are what docx export writes, so they name real fonts. */
export const FONTS: ReadonlyArray<{ value: string; label: MessageKey }> = [
  { value: 'Arial', label: 'document.toolbar.fonts.arial' },
  { value: 'Calibri', label: 'document.toolbar.fonts.calibri' },
  { value: 'Georgia', label: 'document.toolbar.fonts.georgia' },
  { value: 'Times New Roman', label: 'document.toolbar.fonts.timesNewRoman' },
  { value: 'Verdana', label: 'document.toolbar.fonts.verdana' },
  { value: 'Courier New', label: 'document.toolbar.fonts.courierNew' },
];

export const FONT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72];

const BLOCKS = ['paragraph', 'h1', 'h2', 'h3', 'codeBlock'] as const;
type Block = (typeof BLOCKS)[number];

const BLOCK_LABELS: Record<Block, MessageKey> = {
  paragraph: 'document.toolbar.blocks.paragraph',
  h1: 'document.toolbar.blocks.h1',
  h2: 'document.toolbar.blocks.h2',
  h3: 'document.toolbar.blocks.h3',
  codeBlock: 'document.toolbar.blocks.codeBlock',
};

const ALIGN_ICONS: Record<(typeof ALIGNMENTS)[number], ComponentType<IconProps>> = {
  left: AlignLeftIcon,
  center: AlignCenterIcon,
  right: AlignRightIcon,
  justify: AlignJustifyIcon,
};

/** <input type="color"> takes nothing but #rrggbb. */
function asHex(colour: string | null | undefined, fallback: string): string {
  if (colour && /^#[0-9a-f]{6}$/i.test(colour)) return colour;
  const rgb = colour?.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!rgb) return fallback;
  return `#${rgb
    .slice(1, 4)
    .map((part) => Number(part).toString(16).padStart(2, '0'))
    .join('')}`;
}

function snapshot(editor: Editor) {
  const textStyle = editor.getAttributes('textStyle');
  const block = editor.isActive('heading')
    ? (`h${editor.getAttributes('heading').level}` as Block)
    : editor.isActive('codeBlock')
      ? 'codeBlock'
      : 'paragraph';
  const can = editor.can();
  return {
    block: BLOCKS.includes(block) ? block : 'paragraph',
    fontFamily: (textStyle.fontFamily as string | undefined) ?? '',
    fontSize: (textStyle.fontSize as string | undefined) ?? '',
    colour: (textStyle.color as string | undefined) ?? null,
    highlight: editor.isActive('highlight')
      ? ((editor.getAttributes('highlight').color as string | undefined) ?? null)
      : null,
    lineHeight:
      ((editor.getAttributes('paragraph').lineHeight ??
        editor.getAttributes('heading').lineHeight) as string | null) ?? '',
    marks: {
      bold: editor.isActive('bold'),
      italic: editor.isActive('italic'),
      underline: editor.isActive('underline'),
      strike: editor.isActive('strike'),
      code: editor.isActive('code'),
      superscript: editor.isActive('superscript'),
      subscript: editor.isActive('subscript'),
      link: editor.isActive('link'),
    },
    align: ALIGNMENTS.find((alignment) => editor.isActive({ textAlign: alignment })) ?? 'left',
    lists: {
      bulletList: editor.isActive('bulletList'),
      orderedList: editor.isActive('orderedList'),
      taskList: editor.isActive('taskList'),
      blockquote: editor.isActive('blockquote'),
    },
    canIndent: can.indent(),
    canOutdent: can.outdent(),
    inTable: editor.isActive('table'),
    table: {
      addRowBefore: can.addRowBefore(),
      addRowAfter: can.addRowAfter(),
      deleteRow: can.deleteRow(),
      addColumnBefore: can.addColumnBefore(),
      addColumnAfter: can.addColumnAfter(),
      deleteColumn: can.deleteColumn(),
      mergeCells: can.mergeCells(),
      splitCell: can.splitCell(),
      toggleHeaderRow: can.toggleHeaderRow(),
      deleteTable: can.deleteTable(),
    },
  };
}

type ToolbarState = ReturnType<typeof snapshot>;

interface Group {
  id: string;
  label: MessageKey;
  content: ReactNode;
}

interface EditorToolbarProps {
  editor: Editor;
  disabled: boolean;
  onLink: () => void;
  onHelp: () => void;
}

export function EditorToolbar({ editor, disabled, onLink, onHelp }: EditorToolbarProps) {
  const { t } = useI18n();
  const state = useEditorState({ editor, selector: ({ editor: current }) => snapshot(current) });
  const run = (command: (chain: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) =>
    command(editor.chain().focus()).run();

  const tip = (label: MessageKey, action?: ShortcutAction) =>
    action
      ? t('document.toolbar.withShortcut', {
          action: t(label),
          keys: shortcutKeys(action, t).join('+'),
        })
      : t(label);

  const button = (
    id: string,
    label: MessageKey,
    Icon: ComponentType<IconProps>,
    onClick: () => void,
    options: { active?: boolean; enabled?: boolean; shortcut?: ShortcutAction } = {},
  ) => (
    <button
      key={id}
      type="button"
      className="tool"
      aria-label={t(label)}
      title={tip(label, options.shortcut)}
      aria-pressed={options.active}
      disabled={disabled || options.enabled === false}
      onClick={onClick}
    >
      <Icon size={17} />
    </button>
  );

  const groups: Group[] = [
    {
      id: 'block',
      label: 'document.toolbar.groups.block',
      content: (
        <select
          className="tool-select"
          aria-label={t('document.toolbar.blockType')}
          title={t('document.toolbar.blockType')}
          value={state.block}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value as Block;
            run((chain) =>
              next === 'paragraph'
                ? chain.setParagraph()
                : next === 'codeBlock'
                  ? chain.setCodeBlock()
                  : chain.setHeading({ level: Number(next.slice(1)) as 1 | 2 | 3 }),
            );
          }}
        >
          {BLOCKS.map((block) => (
            <option key={block} value={block}>
              {t(BLOCK_LABELS[block])}
            </option>
          ))}
        </select>
      ),
    },
    {
      id: 'font',
      label: 'document.toolbar.groups.font',
      content: (
        <>
          <select
            className="tool-select"
            aria-label={t('document.toolbar.fontFamily')}
            title={t('document.toolbar.fontFamily')}
            value={state.fontFamily}
            disabled={disabled}
            onChange={(event) => {
              const family = event.target.value;
              run((chain) => (family ? chain.setFontFamily(family) : chain.unsetFontFamily()));
            }}
          >
            <option value="">{t('document.toolbar.fontDefault')}</option>
            {FONTS.map((font) => (
              <option key={font.value} value={font.value} style={{ fontFamily: font.value }}>
                {t(font.label)}
              </option>
            ))}
          </select>
          <select
            className="tool-select tool-select-narrow"
            aria-label={t('document.toolbar.fontSize')}
            title={t('document.toolbar.fontSize')}
            value={state.fontSize}
            disabled={disabled}
            onChange={(event) => {
              const size = event.target.value;
              run((chain) => (size ? chain.setFontSize(size) : chain.unsetFontSize()));
            }}
          >
            <option value="">{t('document.toolbar.fontSizeDefault')}</option>
            {FONT_SIZES.map((size) => (
              <option key={size} value={`${size}pt`}>
                {t('document.toolbar.points', { size })}
              </option>
            ))}
          </select>
        </>
      ),
    },
    {
      id: 'marks',
      label: 'document.toolbar.groups.marks',
      content: (
        <>
          {button('bold', 'document.toolbar.bold', BoldIcon, () => run((c) => c.toggleBold()), {
            active: state.marks.bold,
            shortcut: 'bold',
          })}
          {button('italic', 'document.toolbar.italic', ItalicIcon, () => run((c) => c.toggleItalic()), {
            active: state.marks.italic,
            shortcut: 'italic',
          })}
          {button(
            'underline',
            'document.toolbar.underline',
            UnderlineIcon,
            () => run((c) => c.toggleUnderline()),
            { active: state.marks.underline, shortcut: 'underline' },
          )}
          {button('strike', 'document.toolbar.strike', StrikeIcon, () => run((c) => c.toggleStrike()), {
            active: state.marks.strike,
            shortcut: 'strike',
          })}
          {button('code', 'document.toolbar.code', InlineCodeIcon, () => run((c) => c.toggleCode()), {
            active: state.marks.code,
            shortcut: 'code',
          })}
          {button(
            'superscript',
            'document.toolbar.superscript',
            SuperscriptIcon,
            () => run((c) => c.toggleSuperscript()),
            { active: state.marks.superscript, shortcut: 'superscript' },
          )}
          {button(
            'subscript',
            'document.toolbar.subscript',
            SubscriptIcon,
            () => run((c) => c.toggleSubscript()),
            { active: state.marks.subscript, shortcut: 'subscript' },
          )}
          {button('link', 'document.toolbar.link', LinkIcon, onLink, {
            active: state.marks.link,
            shortcut: 'link',
          })}
        </>
      ),
    },
    {
      id: 'colour',
      label: 'document.toolbar.groups.colour',
      content: (
        <>
          <ColourPicker
            label="document.toolbar.textColour"
            icon={<TextColourIcon size={17} swatch={state.colour ?? undefined} />}
            value={asHex(state.colour, '#000000')}
            disabled={disabled}
            onPick={(colour) => run((c) => c.setColor(colour))}
          />
          {button('noColour', 'document.toolbar.clearColour', NoColourIcon, () => run((c) => c.unsetColor()), {
            enabled: state.colour !== null,
          })}
          <ColourPicker
            label="document.toolbar.highlight"
            shortcut={tip('document.toolbar.highlight', 'highlight')}
            icon={<HighlightIcon size={17} swatch={state.highlight ?? '#fde68a'} />}
            value={asHex(state.highlight, '#fde68a')}
            disabled={disabled}
            onPick={(colour) => run((c) => c.setHighlight({ color: colour }))}
          />
          {button(
            'noHighlight',
            'document.toolbar.clearHighlight',
            NoColourIcon,
            () => run((c) => c.unsetHighlight()),
            { enabled: state.highlight !== null },
          )}
        </>
      ),
    },
    {
      id: 'paragraph',
      label: 'document.toolbar.groups.paragraph',
      content: (
        <>
          {ALIGNMENTS.map((alignment) => {
            const action = `align${alignment[0]!.toUpperCase()}${alignment.slice(1)}` as ShortcutAction;
            return button(
              alignment,
              `document.toolbar.align.${alignment}`,
              ALIGN_ICONS[alignment],
              () => run((c) => c.setTextAlign(alignment)),
              { active: state.align === alignment, shortcut: action },
            );
          })}
          <select
            className="tool-select tool-select-narrow"
            aria-label={t('document.toolbar.lineHeight')}
            title={t('document.toolbar.lineHeight')}
            value={state.lineHeight}
            disabled={disabled}
            onChange={(event) => {
              const height = event.target.value;
              run((chain) => (height ? chain.setLineHeight(height) : chain.unsetLineHeight()));
            }}
          >
            <option value="">{t('document.toolbar.lineHeightDefault')}</option>
            {LINE_HEIGHTS.map((height) => (
              <option key={height} value={height}>
                {height}
              </option>
            ))}
          </select>
          {button('outdent', 'document.toolbar.outdent', OutdentIcon, () => run((c) => c.outdent()), {
            enabled: state.canOutdent,
            shortcut: 'outdent',
          })}
          {button('indent', 'document.toolbar.indent', IndentIcon, () => run((c) => c.indent()), {
            enabled: state.canIndent,
            shortcut: 'indent',
          })}
        </>
      ),
    },
    {
      id: 'lists',
      label: 'document.toolbar.groups.lists',
      content: (
        <>
          {button(
            'bulletList',
            'document.toolbar.bulletList',
            BulletListIcon,
            () => run((c) => c.toggleBulletList()),
            { active: state.lists.bulletList, shortcut: 'bulletList' },
          )}
          {button(
            'orderedList',
            'document.toolbar.orderedList',
            OrderedListIcon,
            () => run((c) => c.toggleOrderedList()),
            { active: state.lists.orderedList, shortcut: 'orderedList' },
          )}
          {button(
            'taskList',
            'document.toolbar.taskList',
            TaskListIcon,
            () => run((c) => c.toggleTaskList()),
            { active: state.lists.taskList, shortcut: 'taskList' },
          )}
          {button(
            'blockquote',
            'document.toolbar.blockquote',
            QuoteIcon,
            () => run((c) => c.toggleBlockquote()),
            { active: state.lists.blockquote, shortcut: 'blockquote' },
          )}
        </>
      ),
    },
    {
      id: 'insert',
      label: 'document.toolbar.groups.insert',
      content: (
        <>
          {button(
            'codeBlock',
            'document.toolbar.codeBlock',
            CodeBlockIcon,
            () => run((c) => c.toggleCodeBlock()),
            { active: state.block === 'codeBlock', shortcut: 'codeBlock' },
          )}
          {button('rule', 'document.toolbar.horizontalRule', RuleIcon, () =>
            run((c) => c.setHorizontalRule()),
          )}
          {button(
            'table',
            'document.toolbar.table',
            TableIcon,
            () => run((c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true })),
            { enabled: !state.inTable },
          )}
          {button(
            'pageBreak',
            'document.toolbar.pageBreak',
            PageBreakIcon,
            () => run((c) => c.setPageBreak()),
            { enabled: !state.inTable, shortcut: 'pageBreak' },
          )}
        </>
      ),
    },
  ];

  if (state.inTable) {
    groups.push({
      id: 'table',
      label: 'document.toolbar.groups.table',
      content: <TableTools button={button} state={state} run={run} />,
    });
  }

  return (
    <OverflowRow
      groups={groups}
      label={t('document.toolbar.label')}
      moreLabel={t('document.toolbar.more')}
      trailing={
        <button
          type="button"
          className="tool"
          aria-label={t('document.toolbar.shortcuts')}
          title={tip('document.toolbar.shortcuts', 'shortcuts')}
          onClick={onHelp}
        >
          <KeyboardIcon size={17} />
        </button>
      }
    />
  );
}

type ButtonFactory = (
  id: string,
  label: MessageKey,
  Icon: ComponentType<IconProps>,
  onClick: () => void,
  options?: { active?: boolean; enabled?: boolean; shortcut?: ShortcutAction },
) => ReactNode;

function TableTools({
  button,
  state,
  run,
}: {
  button: ButtonFactory;
  state: ToolbarState;
  run: (command: (chain: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) => boolean;
}) {
  const tools: Array<[keyof ToolbarState['table'], ComponentType<IconProps>]> = [
    ['addRowBefore', RowBeforeIcon],
    ['addRowAfter', RowAfterIcon],
    ['deleteRow', DeleteRowIcon],
    ['addColumnBefore', ColumnBeforeIcon],
    ['addColumnAfter', ColumnAfterIcon],
    ['deleteColumn', DeleteColumnIcon],
    ['mergeCells', MergeCellsIcon],
    ['splitCell', SplitCellIcon],
    ['toggleHeaderRow', HeaderRowIcon],
    ['deleteTable', DeleteTableIcon],
  ];
  return (
    <>
      {tools.map(([command, Icon]) =>
        button(command, `document.toolbar.tableTools.${command}`, Icon, () => run((c) => c[command]()), {
          enabled: state.table[command],
        }),
      )}
    </>
  );
}

function ColourPicker({
  label,
  shortcut,
  icon,
  value,
  disabled,
  onPick,
}: {
  label: MessageKey;
  shortcut?: string;
  icon: ReactNode;
  value: string;
  disabled: boolean;
  onPick: (colour: string) => void;
}) {
  const { t } = useI18n();
  return (
    <label className="tool tool-colour" title={shortcut ?? t(label)} aria-disabled={disabled}>
      {icon}
      <input
        type="color"
        aria-label={t(label)}
        value={value}
        disabled={disabled}
        onChange={(event) => onPick(event.target.value)}
      />
    </label>
  );
}

/** The groups that fit stay in the row; the rest move, whole, into a menu
 *  behind "More". Widths are measured while a group is in the row and
 *  remembered, so a group can come back when there is room again. */
function OverflowRow({
  groups,
  label,
  moreLabel,
  trailing,
}: {
  groups: Group[];
  label: string;
  moreLabel: string;
  trailing: ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const row = useRef<HTMLDivElement>(null);
  const widths = useRef(new Map<string, number>());
  const [fitting, setFitting] = useState(groups.length);
  const [open, setOpen] = useState(false);
  const ids = groups.map((group) => group.id).join(' ');

  const layout = useCallback(() => {
    const box = container.current;
    if (!box) return;
    for (const element of row.current?.querySelectorAll<HTMLElement>('[data-group]') ?? []) {
      widths.current.set(element.dataset.group!, element.offsetWidth);
    }
    // Room for the trailing buttons, which never overflow.
    let room = box.clientWidth - 2 * 40;
    let count = 0;
    for (const id of ids.split(' ')) {
      // Unmeasured: show it once, so the next pass knows its width.
      const width = widths.current.get(id) ?? 0;
      if (width > room) break;
      room -= width;
      count += 1;
    }
    setFitting((current) => (current === count ? current : count));
  }, [ids]);

  useLayoutEffect(layout);

  useEffect(() => {
    const box = container.current;
    if (!box) return;
    const observer = new ResizeObserver(layout);
    observer.observe(box);
    return () => observer.disconnect();
  }, [layout]);

  const overflow = groups.slice(fitting);
  useEffect(() => {
    if (overflow.length === 0) setOpen(false);
  }, [overflow.length]);

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return;
      if (event instanceof MouseEvent && container.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    window.addEventListener('keydown', close);
    window.addEventListener('mousedown', close);
    return () => {
      window.removeEventListener('keydown', close);
      window.removeEventListener('mousedown', close);
    };
  }, [open]);

  return (
    <div className="toolbar" role="toolbar" aria-label={label} ref={container}>
      <div className="toolbar-row" ref={row}>
        {groups.slice(0, fitting).map((group) => (
          <ToolGroup key={group.id} group={group} />
        ))}
      </div>
      <div className="toolbar-trailing">
        {overflow.length > 0 && (
          <button
            type="button"
            className="tool"
            aria-label={moreLabel}
            title={moreLabel}
            aria-haspopup="true"
            aria-expanded={open}
            onClick={() => setOpen((current) => !current)}
          >
            <MoreIcon size={17} />
          </button>
        )}
        {trailing}
      </div>
      {open && overflow.length > 0 && (
        <div className="toolbar-overflow">
          {overflow.map((group) => (
            <ToolGroup key={group.id} group={group} labelled />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolGroup({ group, labelled = false }: { group: Group; labelled?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="tool-group" role="group" aria-label={t(group.label)} data-group={group.id}>
      {labelled && <span className="tool-group-label">{t(group.label)}</span>}
      <div className="tool-group-items">{group.content}</div>
    </div>
  );
}
