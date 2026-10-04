import { describe, it, expect, vi } from "vitest";
import { attachAutoCorrect, type AutoCorrectItem } from "../utils/autocorrect";

describe("autocorrect", () => {
  it("attaches to textarea and autocorrects misspellings on space", () => {
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    const onCorrect = vi.fn();
    const detach = attachAutoCorrect(textarea, onCorrect);

    textarea.value = "teh";
    textarea.selectionStart = 3;
    textarea.selectionEnd = 3;

    const event = new KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    });
    textarea.dispatchEvent(event);

    expect(textarea.value).toBe("ten ");
    expect(onCorrect).toHaveBeenCalledWith(
      expect.objectContaining({
        original: "teh",
        corrected: "ten",
        start: 0,
        end: 3,
      })
    );
    detach();
    document.body.removeChild(textarea);
  });

  it("autocorrects misspellings on Tab key without inserting tab character", () => {
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    let eventDetail: AutoCorrectItem | null = null;
    textarea.addEventListener("autocorrect", (e: Event) => {
      eventDetail = (e as CustomEvent<AutoCorrectItem>).detail;
    });

    const detach = attachAutoCorrect(textarea);

    textarea.value = "Give me teh";
    textarea.selectionStart = 11;
    textarea.selectionEnd = 11;

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    textarea.dispatchEvent(event);

    expect(textarea.value).toBe("Give me ten");
    expect(textarea.selectionStart).toBe(11);
    expect(event.defaultPrevented).toBe(true);
    expect(eventDetail).toEqual(
      expect.objectContaining({
        original: "teh",
        corrected: "ten",
        start: 8,
        end: 11,
      })
    );
    detach();
    document.body.removeChild(textarea);
  });

  it("preserves whitelisted domain words like seleric, cac, cpc and api", () => {
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    const detach = attachAutoCorrect(textarea);

    for (const word of ["seleric", "cac", "cpc", "mer", "roas"]) {
      textarea.value = word;
      textarea.selectionStart = word.length;
      textarea.selectionEnd = word.length;

      const event = new KeyboardEvent("keydown", {
        key: "Tab",
        bubbles: true,
        cancelable: true,
      });
      textarea.dispatchEvent(event);

      expect(textarea.value).toBe(word);
      expect(event.defaultPrevented).toBe(false);
    }

    detach();
    document.body.removeChild(textarea);
  });

  it("preserves casing when autocorrecting capitalized words", () => {
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);

    const detach = attachAutoCorrect(textarea);

    textarea.value = "Teh";
    textarea.selectionStart = 3;
    textarea.selectionEnd = 3;

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    textarea.dispatchEvent(event);

    expect(textarea.value).toBe("Ten");
    detach();
    document.body.removeChild(textarea);
  });
});

