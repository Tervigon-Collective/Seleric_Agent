import { useEffect, useRef } from "react";
import { XIcon } from "./icons";
import { PromptRegistry } from "./PromptRegistry";

export function PromptRegistryModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    queueMicrotask(() => closeRef.current?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
    >
      <section
        className="prompt-registry-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-registry-title"
      >
        <header className="prompt-registry-modal-head">
          <div>
            <h2 id="prompt-registry-title">Prompt registry</h2>
            <p>Browse L1, golden, diagnosis, and domain questions — click to edit in the input.</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-btn"
            aria-label="Close prompt registry"
            title="Close"
            onClick={onClose}
          >
            <XIcon size={16} />
          </button>
        </header>
        <PromptRegistry onPicked={onClose} defaultChip="golden" />
      </section>
    </div>
  );
}
