import { useConversationStore } from "../../stores/conversation";
import { useShellStore } from "../../stores/shell";
import type { Anomaly } from "./anomalies";
import { formatValue } from "./format";

/**
 * Investigate an anomaly: open the chat over Monitoring, start a fresh conversation, and ask the question.
 * It only asks. The agent reads data and answers; anything that changes something still needs approval in the chat.
 */
export async function investigate({ metric: m }: Anomaly): Promise<void> {
  const title = m.label || m.name || m.metric_id;
  const value = formatValue(m.value, m.unit, m.display);
  const expected = m.judgement?.expected != null ? formatValue(m.judgement.expected, m.unit, m.display) : null;
  useShellStore.getState().askAbout(`${title} · ${value}${expected ? ` vs ${expected} expected` : ""}`);
  const store = useConversationStore.getState();
  await store.createThread();
  await useConversationStore.getState().submit(
    `Investigate ${title}: it is ${value}${expected ? `, expected ${expected}` : ""}. What changed, and what is driving it? Show the evidence.`,
  );
}
