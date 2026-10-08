/** Lightweight outlined icon set (Lucide-style stroke icons, inline SVG).
 * Avoids adding a dependency for a handful of chrome icons. 1.75px stroke,
 * 16px default, currentColor. */

import type { ReactNode } from "react";

interface IconProps {
  size?: number;
  className?: string;
  ariaHidden?: boolean;
}

function Base({ size = 16, className, ariaHidden = true, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={ariaHidden}
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const MenuIcon = (p: IconProps) => (
  <Base {...p}><line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" /><line x1="4" y1="18" x2="20" y2="18" /></Base>
);
export const PanelRightIcon = (p: IconProps) => (
  <Base {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><line x1="15" y1="4" x2="15" y2="20" /></Base>
);
export const SearchIcon = (p: IconProps) => (
  <Base {...p}><circle cx="11" cy="11" r="7" /><line x1="16.5" y1="16.5" x2="21" y2="21" /></Base>
);
export const PlusIcon = (p: IconProps) => (
  <Base {...p}><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></Base>
);
export const XIcon = (p: IconProps) => (
  <Base {...p}><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></Base>
);
export const SendIcon = (p: IconProps) => (
  <Base {...p}><line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" /></Base>
);
export const StopIcon = (p: IconProps) => (
  <Base {...p}><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" /></Base>
);
export const PencilIcon = (p: IconProps) => (
  <Base {...p}><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></Base>
);
export const ArchiveIcon = (p: IconProps) => (
  <Base {...p}><rect x="2" y="3" width="20" height="5" rx="1" /><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /><line x1="10" y1="12" x2="14" y2="12" /></Base>
);
export const MicIcon = (p: IconProps) => (
  <Base {...p}><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><line x1="12" y1="17" x2="12" y2="22" /></Base>
);
export const PaperclipIcon = (p: IconProps) => (
  <Base {...p}><path d="m21 11-9.5 9.5a5.5 5.5 0 0 1-7.8-7.8L13 3.5a4 4 0 0 1 5.6 5.6l-9.2 9.2a2.5 2.5 0 0 1-3.5-3.5L15 5.7" /></Base>
);
export const CopyIcon = (p: IconProps) => (
  <Base {...p}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></Base>
);
export const CheckIcon = (p: IconProps) => (
  <Base {...p}><polyline points="20 6 9 17 4 12" /></Base>
);
export const ChevronDownIcon = (p: IconProps) => (
  <Base {...p}><polyline points="6 9 12 15 18 9" /></Base>
);
export const ArrowDownIcon = (p: IconProps) => (
  <Base {...p}><line x1="12" y1="5" x2="12" y2="19" /><polyline points="19 12 12 19 5 12" /></Base>
);
export const ActivityIcon = (p: IconProps) => (
  <Base {...p}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></Base>
);
export const SunIcon = (p: IconProps) => (
  <Base {...p}><circle cx="12" cy="12" r="4" /><line x1="12" y1="2" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="22" /><line x1="2" y1="12" x2="5" y2="12" /><line x1="19" y1="12" x2="22" y2="12" /><line x1="4.9" y1="4.9" x2="7" y2="7" /><line x1="17" y1="17" x2="19.1" y2="19.1" /><line x1="17" y1="7" x2="19.1" y2="4.9" /><line x1="4.9" y1="19.1" x2="7" y2="17" /></Base>
);
export const MoonIcon = (p: IconProps) => (
  <Base {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></Base>
);
export const DownloadIcon = (p: IconProps) => (
  <Base {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></Base>
);
export const SlidersIcon = (p: IconProps) => (
  <Base {...p}><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></Base>
);
