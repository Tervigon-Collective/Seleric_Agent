import nspell from 'nspell';
import aff from './dict/en.aff?raw';
import dic from './dict/en.dic?raw';

let spellChecker: ReturnType<typeof nspell> | null = null;

const whitelist = new Set([
  'seleric', 'inr', 'sku', 'skus', 'api', 'p&l', 'ai', 'ui', 'ux', 'sdk',
  'js', 'ts', 'jsx', 'tsx', 'css', 'html', 'json',
  'cac', 'cpc', 'mer', 'roas', 'cpm', 'ctr', 'gmv', 'aov', 'ncac',
  'adset', 'adsets', 'campaign', 'campaigns', 'langfuse', 'qdrant', 'livekit',
  'blended', 'laya', 'fastapi', 'ga4', 'sql', 'postgres', 'redis',
]);

try {
  spellChecker = nspell(aff, dic);
  whitelist.forEach((word) => spellChecker?.add(word));
} catch (err) {
  console.error('Failed to load spellcheck dictionary:', err);
}

export interface AutoCorrectItem {
  id: string;
  original: string;
  corrected: string;
  start: number;
  end: number;
}

export function attachAutoCorrect(
  inputElement: HTMLTextAreaElement,
  onCorrect?: (item: AutoCorrectItem) => void,
) {
  const triggerKeys = new Set([
    'Tab', ' ', '.', ',', '!', '?', ';', ':', 'Enter',
  ]);

  const listener = (e: KeyboardEvent) => {
    if (!spellChecker || !triggerKeys.has(e.key)) return;

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

        const newCursorPos = wordStartIndex + replacement.length;
        inputElement.setSelectionRange(newCursorPos, newCursorPos);

        const item: AutoCorrectItem = {
          id: `${wordStartIndex}-${Date.now()}`,
          original: lastWord,
          corrected: bestMatch,
          start: wordStartIndex,
          end: wordStartIndex + bestMatch.length,
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
    }
  };

  inputElement.addEventListener('keydown', listener);

  return () => {
    inputElement.removeEventListener('keydown', listener);
  };
}
