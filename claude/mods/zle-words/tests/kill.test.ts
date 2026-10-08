import type {ClientKeyEvent, PromptBox, PromptEditInput} from 'claude-code';
import {expect, mock, test} from 'claude-code/testing';
import type {Engine} from 'claude-code/testing';

const DEFAULT_MINUS_SLASH = '*?_-.[]~=&;!#$%^(){}<>';

/** Raises one prompt.edit through the plugin, as the editor does. */
function edit($: Engine, input: PromptEditInput): Promise<PromptBox> {
  // The kit answers $.prompt.edit at runtime but its typings leave the call out.
  const prompt = $.prompt as unknown as {
    edit(e: PromptEditInput): Promise<PromptBox>;
  };
  return prompt.edit(input);
}

/** A key-driven edit at `cursor`, the splice left as the engine's own. */
function keyEdit(
  key: ClientKeyEvent,
  text: string,
  cursor = text.length,
): PromptEditInput {
  return {
    origin: {kind: 'composer'},
    key,
    text,
    cursor,
    start: cursor,
    end: cursor,
    inputText: '',
  };
}

function ctrlW(text: string, cursor = text.length): PromptEditInput {
  return keyEdit({key: 'w', ctrl: true}, text, cursor);
}

function alt(key: string, text: string, cursor: number): PromptEditInput {
  return keyEdit({key, meta: true}, text, cursor);
}

test('ctrl+w stops at chars outside WORDCHARS', async ($, on) => {
  mock.env(on, {WORDCHARS: DEFAULT_MINUS_SLASH});
  expect((await edit($, ctrlW('cd ~/src/foo-bar'))).text).toBe('cd ~/src/');
  expect((await edit($, ctrlW('cd ~/src/'))).text).toBe('cd ~/');
});

test('ctrl+w skips trailing separators first, like zsh', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, ctrlW('git commit  -m  '))).toEqual({
    text: 'git commit  -',
    cursor: 13,
  });
});

test('ctrl+w kills only before the cursor', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, ctrlW('foo.bar baz', 7))).toEqual({
    text: 'foo. baz',
    cursor: 4,
  });
});

test('unset WORDCHARS falls back to the zsh default', async ($, on) => {
  mock.env(on, {});
  expect((await edit($, ctrlW('ls a/b/c.txt'))).text).toBe('ls ');
});

test('CJK counts as word characters', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect((await edit($, ctrlW('修改 键位-分词'))).text).toBe('修改 键位-');
});

test('astral characters are stepped over whole', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, ctrlW('a 𝒳𝒴'))).toEqual({text: 'a ', cursor: 2});
});

test('alt+backspace kills backward like ctrl+w', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, alt('backspace', 'foo-bar', 7))).toEqual({
    text: 'foo-',
    cursor: 4,
  });
});

test('alt+b and alt+f move by word, zsh-style', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, alt('b', 'foo-bar baz', 8))).toEqual({
    text: 'foo-bar baz',
    cursor: 4,
  });
  // forward-word lands on the start of the next word, not the end of this one.
  expect(await edit($, alt('f', 'foo-bar baz', 0))).toEqual({
    text: 'foo-bar baz',
    cursor: 4,
  });
});

test('alt+d kills forward to the end of the word', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  expect(await edit($, alt('d', 'foo  bar-baz', 3))).toEqual({
    text: 'foo-baz',
    cursor: 3,
  });
});

test('other keys pass through to the engine', async ($, on) => {
  mock.env(on, {WORDCHARS: ''});
  on('prompt.edit', () => ({text: 'from-engine', cursor: 0}));
  const ctrlAltW = keyEdit({key: 'w', ctrl: true, meta: true}, 'ab');
  expect((await edit($, ctrlAltW)).text).toBe('from-engine');
  const typed = {...keyEdit({key: 'c'}, 'ab'), inputText: 'c'};
  expect((await edit($, typed)).text).toBe('from-engine');
});

test('WORDCHARS is read once per load', async ($, on) => {
  let reads = 0;
  on('env.get', () => {
    reads++;
    return {value: '-'};
  });
  await edit($, ctrlW('a-b c'));
  await edit($, ctrlW('a-b c'));
  expect((await edit($, ctrlW('x a-b'))).text).toBe('x ');
  expect(reads).toBe(1);
});
