import type {ClientKeyEvent, EngineInterface, Register} from 'claude-code';

/** zsh's default WORDCHARS, used when the variable is unset. Set-but-empty is respected as empty. */
const ZSH_DEFAULT_WORDCHARS = '*?_-.[]~=/&;!#$%^(){}<>';

const ALPHANUMERIC = /^[\p{L}\p{M}\p{N}]$/u;

/**
 * The keys zsh's emacs keymap binds to its word widgets. Doubles as the hook's
 * matcher, so ordinary typing never reaches the plugin.
 */
const WORD_KEYS = [
  {key: 'w', ctrl: true},
  {key: 'backspace', meta: true},
  {key: /^[bfd]$/, meta: true},
] as const;

/** Whether one code point counts as part of a word. */
type IsWordChar = (char: string) => boolean;

/** The zle widgets this mod reimplements, named as zsh names them. */
type Widget =
  | 'backward-kill-word'
  | 'backward-word'
  | 'forward-word'
  | 'kill-word';

/** The prompt box after an edit. */
interface PromptBox {
  text: string;
  cursor: number;
}

/** Maps a key press to the zle widget zsh's emacs keymap binds it to. */
function widgetOf(keyEvent: ClientKeyEvent): Widget | undefined {
  const {key, ctrl, meta, shift} = keyEvent;
  if (shift) return undefined;
  if (ctrl && !meta) return key === 'w' ? 'backward-kill-word' : undefined;
  if (!meta || ctrl) return undefined;
  switch (key) {
    case 'backspace':
      return 'backward-kill-word';
    case 'b':
      return 'backward-word';
    case 'f':
      return 'forward-word';
    case 'd':
      return 'kill-word';
    default:
      return undefined;
  }
}

/** Builds the word-character test zle uses: alphanumerics plus WORDCHARS. */
function makeIsWordChar(wordchars: string): IsWordChar {
  const extraWordChars = new Set(wordchars);
  return (char) => extraWordChars.has(char) || ALPHANUMERIC.test(char);
}

function isHighSurrogate(codeUnit: number): boolean {
  return codeUnit >= 0xd800 && codeUnit <= 0xdbff;
}

function isLowSurrogate(codeUnit: number): boolean {
  return codeUnit >= 0xdc00 && codeUnit <= 0xdfff;
}

// Steps by code point, so a scan costs the length of the word, not of the draft.
function previousCodePointIndex(text: string, index: number): number {
  const isPair =
    index >= 2 &&
    isLowSurrogate(text.charCodeAt(index - 1)) &&
    isHighSurrogate(text.charCodeAt(index - 2));
  return isPair ? index - 2 : index - 1;
}

function nextCodePointIndex(text: string, index: number): number {
  return index + ((text.codePointAt(index) ?? 0) > 0xffff ? 2 : 1);
}

/** Moves left from `index` while code points are (or, with `overWords` false, are not) word chars. */
function skipBackward(
  text: string,
  index: number,
  isWordChar: IsWordChar,
  overWords: boolean,
): number {
  while (index > 0) {
    const previous = previousCodePointIndex(text, index);
    if (isWordChar(text.slice(previous, index)) !== overWords) break;
    index = previous;
  }
  return index;
}

/** Moves right from `index` while code points are (or, with `overWords` false, are not) word chars. */
function skipForward(
  text: string,
  index: number,
  isWordChar: IsWordChar,
  overWords: boolean,
): number {
  while (index < text.length) {
    const next = nextCodePointIndex(text, index);
    if (isWordChar(text.slice(index, next)) !== overWords) break;
    index = next;
  }
  return index;
}

/** Where zle's backward-word lands: back over separators, then over the word. */
function backwardWordIndex(
  text: string,
  cursor: number,
  isWordChar: IsWordChar,
): number {
  return skipBackward(
    text,
    skipBackward(text, cursor, isWordChar, false),
    isWordChar,
    true,
  );
}

/** Where zle's forward-word lands: over the word, then over separators, to the next word's start. */
function forwardWordIndex(
  text: string,
  cursor: number,
  isWordChar: IsWordChar,
): number {
  return skipForward(
    text,
    skipForward(text, cursor, isWordChar, true),
    isWordChar,
    false,
  );
}

/** Where zle's kill-word stops: over separators, then to the end of the word. */
function killWordEndIndex(
  text: string,
  cursor: number,
  isWordChar: IsWordChar,
): number {
  return skipForward(
    text,
    skipForward(text, cursor, isWordChar, false),
    isWordChar,
    true,
  );
}

/** Runs one widget against the draft, as zle would. */
function applyWidget(
  widget: Widget,
  text: string,
  cursor: number,
  isWordChar: IsWordChar,
): PromptBox {
  switch (widget) {
    case 'backward-kill-word': {
      const start = backwardWordIndex(text, cursor, isWordChar);
      return {text: text.slice(0, start) + text.slice(cursor), cursor: start};
    }
    case 'kill-word': {
      const end = killWordEndIndex(text, cursor, isWordChar);
      return {text: text.slice(0, cursor) + text.slice(end), cursor};
    }
    case 'backward-word':
      return {text, cursor: backwardWordIndex(text, cursor, isWordChar)};
    case 'forward-word':
      return {text, cursor: forwardWordIndex(text, cursor, isWordChar)};
    default: {
      const unhandled: never = widget;
      throw new Error(`unhandled widget: ${unhandled}`);
    }
  }
}

// The process environment is fixed for the session, so WORDCHARS is read once
// per load. Once read it is a plain value, and the hook answers synchronously
// well within its 50ms budget.
let loadedIsWordChar: IsWordChar | undefined;
let loadingIsWordChar: Promise<IsWordChar> | undefined;

function loadIsWordChar($: EngineInterface): Promise<IsWordChar> {
  loadingIsWordChar ??= $.env.get('WORDCHARS').then((wordchars) => {
    loadedIsWordChar = makeIsWordChar(wordchars ?? ZSH_DEFAULT_WORDCHARS);
    return loadedIsWordChar;
  });
  return loadingIsWordChar;
}

export const register: Register = (on) => {
  on('session.start', ($, e, next) => {
    void loadIsWordChar($);
    return next(e);
  });

  on('prompt.edit', {key: WORD_KEYS}, ($, e, next) => {
    const widget = e.key && widgetOf(e.key);
    if (!widget) return next(e);
    if (loadedIsWordChar) {
      return applyWidget(widget, e.text, e.cursor, loadedIsWordChar);
    }
    return loadIsWordChar($).then((isWordChar) =>
      applyWidget(widget, e.text, e.cursor, isWordChar),
    );
  });
};
