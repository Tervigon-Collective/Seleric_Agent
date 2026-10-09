import { useAui } from "@assistant-ui/react";
import { useMemo, useState } from "react";
import {
  DOMAIN_FILTERS,
  DOMAIN_LABELS,
  FILTER_CHIPS,
  PROMPT_REGISTRY,
  filterPrompts,
  groupByDomain,
  type FilterChipId,
  type PromptDomain,
  type RegistryPrompt,
} from "../data/promptRegistry";

function focusComposer() {
  const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
  if (!input) return;
  input.focus();
  const end = input.value.length;
  input.setSelectionRange(end, end);
}

function PromptChip({ prompt, onSelect }: { prompt: RegistryPrompt; onSelect: (text: string) => void }) {
  const badges = [
    prompt.level === "L1" ? "L1" : null,
    prompt.tags.includes("golden") ? "Golden" : null,
    prompt.tags.includes("diagnosis") ? "Diagnosis" : null,
  ].filter(Boolean);

  return (
    <button
      type="button"
      className="prompt-chip"
      onClick={() => onSelect(prompt.text)}
      title="Add to input — edit before sending"
    >
      <span className="prompt-chip-text">{prompt.text}</span>
      {badges.length > 0 && (
        <span className="prompt-chip-badges" aria-hidden="true">
          {badges.map((badge) => (
            <span key={badge} className="prompt-chip-badge">{badge}</span>
          ))}
        </span>
      )}
    </button>
  );
}

export function PromptRegistry({
  onPicked,
  defaultChip = "L1",
}: {
  onPicked?: () => void;
  defaultChip?: FilterChipId;
} = {}) {
  const aui = useAui();
  const [chip, setChip] = useState<FilterChipId>(defaultChip);
  const [domain, setDomain] = useState<"all" | PromptDomain>("all");

  const filtered = useMemo(
    () => filterPrompts(PROMPT_REGISTRY, chip, domain),
    [chip, domain],
  );
  const groups = useMemo(() => groupByDomain(filtered), [filtered]);

  const selectPrompt = (text: string) => {
    aui.composer.setText(text);
    onPicked?.();
    // Let React flush the composer value before focusing.
    requestAnimationFrame(() => focusComposer());
  };

  return (
    <div className="prompt-registry" role="region" aria-label="Prompt registry">
      <div className="prompt-filters" role="toolbar" aria-label="Filter prompts">
        <div className="prompt-filter-row" role="group" aria-label="Kind">
          {FILTER_CHIPS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`prompt-filter${chip === item.id ? " active" : ""}`}
              aria-pressed={chip === item.id}
              onClick={() => setChip(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="prompt-filter-row" role="group" aria-label="Domain">
          {DOMAIN_FILTERS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`prompt-filter subtle${domain === item.id ? " active" : ""}`}
              aria-pressed={domain === item.id}
              onClick={() => setDomain(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <p className="prompt-registry-hint">
        Click a question to place it in the input — edit, then send.
      </p>

      {groups.length === 0 ? (
        <p className="empty-note" role="status">No prompts match these filters.</p>
      ) : (
        <div className="prompt-groups" role="list">
          {groups.map((group) => (
            <div key={group.domain} className="prompt-group" role="listitem">
              <p className="prompt-group-label">{DOMAIN_LABELS[group.domain]}</p>
              <div className="empty-suggestions prompt-chips">
                {group.prompts.map((prompt) => (
                  <PromptChip key={prompt.id} prompt={prompt} onSelect={selectPrompt} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
