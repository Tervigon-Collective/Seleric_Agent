type SpellChecker = {
  correct(word: string): boolean;
  suggest(word: string): string[];
  add(word: string): unknown;
};

let spellChecker: SpellChecker | null = null;
let loading: Promise<void> | null = null;

const whitelist = new Set([
  'seleric', 'inr', 'sku', 'skus', 'api', 'p&l', 'ai', 'ui', 'ux', 'sdk',
  'js', 'ts', 'jsx', 'tsx', 'css', 'html', 'json',
  'cac', 'cpc', 'mer', 'roas', 'cpm', 'ctr', 'gmv', 'aov', 'ncac',
  'adset', 'adsets', 'campaign', 'campaigns', 'langfuse', 'qdrant', 'livekit',
  'blended', 'laya', 'fastapi', 'ga4', 'sql', 'postgres', 'redis',
]);

/**
 * Parsing the 550 KB dictionary blocks the main thread (~200 ms), so it is fetched and built
 * on first use instead of at import time. Until it resolves the keydown handler is a no-op.
 */
export function loadSpellChecker(): Promise<void> {
  loading ??= Promise.all([import('nspell'), import('./dict/en.aff?raw'), import('./dict/en.dic?raw')])
    .then(([{ default: nspell }, aff, dic]) => {
      const checker = nspell(aff.default, dic.default);
      whitelist.forEach((word) => checker.add(word));
      spellChecker = checker;
    })
    .catch((err) => {
      console.error('Failed to load spellcheck dictionary:', err);
    });
  return loading;
}

export interface AutoCorrectItem {
  id: string;
  original: string;
  corrected: string;
  start: number;
  end: number;
}

function setTextareaValue(inputElement: HTMLTextAreaElement, newText: string) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    typeof window !== 'undefined'
      ? window.HTMLTextAreaElement?.prototype
      : HTMLTextAreaElement.prototype,
    'value'
  )?.set;
  if (nativeInputValueSetter) {
    nativeInputValueSetter.call(inputElement, newText);
  } else {
    inputElement.value = newText;
  }
}

interface PendingUndo {
  item: AutoCorrectItem;
  /** Cursor position immediately after the correction (includes trigger suffix). */
  cursorAfter: number;
  /** Trigger character appended after the corrected word (space, punctuation, etc.). */
  suffix: string;
}

export function attachAutoCorrect(
  inputElement: HTMLTextAreaElement,
  onCorrect?: (item: AutoCorrectItem) => void,
) {
  // Idle time, so the one-off dictionary build never lands on a keystroke or first paint.
  (window.requestIdleCallback ?? window.setTimeout)(() => void loadSpellChecker());

  const triggerKeys = new Set([
    'Tab', ' ', '.', ',', '!', '?', ';', ':', 'Enter',
  ]);

  let pendingUndo: PendingUndo | null = null;

  const clearUndo = () => {
    pendingUndo = null;
  };

  const undoCorrection = (e: KeyboardEvent) => {
    if (!pendingUndo) return false;

    const { item, cursorAfter, suffix } = pendingUndo;
    const cursorPos = inputElement.selectionStart ?? 0;
    const selectionEnd = inputElement.selectionEnd ?? cursorPos;
    if (cursorPos !== selectionEnd) return false;
    if (cursorPos !== cursorAfter) return false;

    const fullText = inputElement.value;
    // Guard: text must still match what we wrote when correcting.
    if (fullText.slice(item.start, item.end) !== item.corrected) {
      clearUndo();
      return false;
    }
    if (suffix && fullText.slice(item.end, item.end + suffix.length) !== suffix) {
      clearUndo();
      return false;
    }

    e.preventDefault();

    const restored =
      fullText.slice(0, item.start) + item.original + suffix + fullText.slice(cursorAfter);
    setTextareaValue(inputElement, restored);

    const newCursorPos = item.start + item.original.length + suffix.length;
    inputElement.setSelectionRange(newCursorPos, newCursorPos);
    clearUndo();

    inputElement.dispatchEvent(
      new CustomEvent('autocorrect-undo', {
        bubbles: true,
        detail: item,
      })
    );
    inputElement.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  };

  const listener = (e: KeyboardEvent) => {
    if (e.key === 'Backspace') {
      if (undoCorrection(e)) return;
      clearUndo();
      return;
    }

    if (!spellChecker || !triggerKeys.has(e.key)) {
      // Any other edit invalidates undo of the last correction.
      if (e.key.length === 1 || e.key === 'Delete' || e.key === 'Enter') {
        clearUndo();
      }
      return;
    }

    const cursorPos = inputElement.selectionStart ?? inputElement.value.length;
    const fullText = inputElement.value;
    const textBeforeCursor = fullText.slice(0, cursorPos);
    const match = textBeforeCursor.match(/([a-zA-Z]+)$/);
    if (!match) return;

    const lastWord = match[1];
    const wordStartIndex = match.index ?? 0;

    if (lastWord === lastWord.toUpperCase()) return;

    const isCorrect = spellChecker.correct(lastWord);
    if (!isCorrect) {
      const suggestions = spellChecker.suggest(lastWord);
      if (suggestions && suggestions.length > 0) {
        let bestMatch = suggestions[0];
        const isCapitalized = lastWord[0] === lastWord[0].toUpperCase();
        if (isCapitalized) {
          bestMatch = bestMatch.charAt(0).toUpperCase() + bestMatch.slice(1);
        }

        e.preventDefault();
        const textAfterCursor = fullText.slice(cursorPos);

        let suffix = '';
        if (e.key === 'Tab') {
          suffix = '';
        } else if (e.key === 'Enter') {
          suffix = e.shiftKey ? '\n' : '';
        } else {
          suffix = e.key;
        }

        const replacement = bestMatch + suffix;
        const newText =
          fullText.slice(0, wordStartIndex) + replacement + textAfterCursor;

        setTextareaValue(inputElement, newText);

        const newCursorPos = wordStartIndex + replacement.length;
        inputElement.setSelectionRange(newCursorPos, newCursorPos);

        const item: AutoCorrectItem = {
          id: `${wordStartIndex}-${Date.now()}`,
          original: lastWord,
          corrected: bestMatch,
          start: wordStartIndex,
          end: wordStartIndex + bestMatch.length,
        };

        pendingUndo = {
          item,
          cursorAfter: newCursorPos,
          suffix,
        };

        if (onCorrect) {
          onCorrect(item);
        }

        inputElement.dispatchEvent(
          new CustomEvent('autocorrect', {
            bubbles: true,
            detail: item,
          })
        );

        inputElement.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } else {
      clearUndo();
    }
  };

  inputElement.addEventListener('keydown', listener);

  return () => {
    clearUndo();
    inputElement.removeEventListener('keydown', listener);
  };
}
