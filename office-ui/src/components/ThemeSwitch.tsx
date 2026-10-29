import { Switch } from "@/components/ui/switch";

/** Light/dark as a real role="switch", so assistive tech hears the state. */
export function ThemeSwitch({ dark, onToggle, className }: { dark: boolean; onToggle: () => void; className?: string }) {
  return <Switch className={className} checked={dark} onCheckedChange={onToggle} aria-label="Dark theme" title="Dark theme" />;
}
