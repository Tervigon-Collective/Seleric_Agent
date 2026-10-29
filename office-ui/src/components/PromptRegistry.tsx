import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Button } from "@/components/ui/button";
import { useAui } from "@assistant-ui/react";
import { useMemo, useState } from "react";
import {
  DOMAIN_FILTERS,
  DOMAIN_LABELS,
  FILTER_CHIPS,
  PROMPT_REGISTRY,
  filterPrompts,
  frontChatPrompts,
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

function PromptChip({
  prompt,
  onSelect,
  showBadges,
}: {
  prompt: RegistryPrompt;
  onSelect: (text: string) => void;
  showBadges: boolean;
}) {
  const badges = showBadges
    ? [
        prompt.tags.includes("exec") ? "Executive" : null,
        prompt.tags.includes("diagnosis") ? "Diagnosis" : null,
      ].filter(Boolean)
    : [];

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-auto w-full justify-start py-2 text-left font-normal text-wrap whitespace-normal"
      onClick={() => onSelect(prompt.text)}
      title="Add to input — edit before sending"
    >
      <span>{prompt.text}</span>
      {badges.length > 0 && (
        <span className="ml-auto flex gap-1" aria-hidden="true">
          {badges.map((badge) => <Badge key={badge} variant="outline">{badge}</Badge>)}
        </span>
      )}
    </Button>
  );
}

export function PromptRegistry({
  onPicked,
  defaultChip = "exec",
  surface = "browse",
}: {
  onPicked?: () => void;
  defaultChip?: FilterChipId;
  /** Empty chat shows only fast, reliable lookups. The modal keeps the full registry. */
  surface?: "front" | "browse";
} = {}) {
  const aui = useAui();
  const [chip, setChip] = useState<FilterChipId>(defaultChip);
  const [domain, setDomain] = useState<"all" | PromptDomain>("all");
  const front = surface === "front";

  const filtered = useMemo(
    () => (front ? frontChatPrompts() : filterPrompts(PROMPT_REGISTRY, chip, domain)),
    [front, chip, domain],
  );
  const groups = useMemo(() => groupByDomain(filtered), [filtered]);

  const selectPrompt = (text: string) => {
    aui.composer.setText(text);
    onPicked?.();
    // Let React flush the composer value before focusing.
    requestAnimationFrame(() => focusComposer());
  };

  return (
    <div className="flex w-full flex-col gap-3" role="region" aria-label={front ? "Suggested questions" : "Prompt registry"}>
      {!front && (
        <>
          <div className="flex flex-col gap-2" role="toolbar" aria-label="Filter prompts">
            <ToggleGroup type="single" variant="outline" size="sm" className="flex-wrap" aria-label="Kind" value={chip} onValueChange={(value) => value && setChip(value as FilterChipId)}>
              {FILTER_CHIPS.map((item) => <ToggleGroupItem key={item.id} value={item.id}>{item.label}</ToggleGroupItem>)}
            </ToggleGroup>
            <ToggleGroup type="single" variant="outline" size="sm" className="flex-wrap" aria-label="Domain" value={domain} onValueChange={(value) => value && setDomain(value as "all" | PromptDomain)}>
              {DOMAIN_FILTERS.map((item) => <ToggleGroupItem key={item.id} value={item.id}>{item.label}</ToggleGroupItem>)}
            </ToggleGroup>
          </div>

          <p className="text-xs text-muted-foreground">
            Click a question to place it in the input — edit, then send.
          </p>
        </>
      )}

      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground" role="status">No prompts match these filters.</p>
      ) : front ? (
        <div className="flex w-full max-w-xl flex-col gap-2" role="list">
          {filtered.map((prompt) => (
            <PromptChip key={prompt.id} prompt={prompt} onSelect={selectPrompt} showBadges={false} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4" role="list">
          {groups.map((group) => (
            <div key={group.domain} className="flex flex-col gap-2" role="listitem">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{DOMAIN_LABELS[group.domain]}</p>
              <div className="flex flex-col gap-2">
                {group.prompts.map((prompt) => (
                  <PromptChip key={prompt.id} prompt={prompt} onSelect={selectPrompt} showBadges />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
