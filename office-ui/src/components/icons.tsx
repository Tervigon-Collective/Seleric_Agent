/** The app's icon names, backed by lucide (shadcn's icon library). Same names and props as before. */
import {
  ActivityIcon as Activity, ArchiveIcon as Archive, ArrowDownIcon as ArrowDown, ArrowRightIcon as ArrowRight,
  BookTextIcon as BookText, CheckIcon as Check, ChevronDownIcon as ChevronDown, CopyIcon as Copy, DownloadIcon as Download,
  EllipsisIcon as Ellipsis, ImageIcon as Image, LinkIcon as Link, Maximize2Icon as Maximize, MenuIcon as Menu,
  MicIcon as Mic, MicOffIcon as MicOff, Minimize2Icon as Minimize, MoonIcon as Moon, PanelRightIcon as PanelRight,
  PaperclipIcon as Paperclip, PencilIcon as Pencil, PlusIcon as Plus, RotateCcwIcon as RotateCcw, SearchIcon as Search,
  SendHorizontalIcon as SendHorizontal, SlidersHorizontalIcon as SlidersHorizontal, SparklesIcon as Sparkles,
  SquareIcon as Square, SunIcon as Sun, ThumbsDownIcon as ThumbsDown, ThumbsUpIcon as ThumbsUp, XIcon as X,
  type LucideIcon,
} from "lucide-react";

export interface IconProps {
  size?: number;
  className?: string;
  ariaHidden?: boolean;
}

const wrap = (Icon: LucideIcon) => ({ size, className, ariaHidden = true }: IconProps) =>
  <Icon size={size} className={className} aria-hidden={ariaHidden} focusable="false" />;

export const MenuIcon = wrap(Menu);
export const MoreIcon = wrap(Ellipsis);
export const PanelRightIcon = wrap(PanelRight);
export const SearchIcon = wrap(Search);
export const PlusIcon = wrap(Plus);
export const XIcon = wrap(X);
export const SendIcon = wrap(SendHorizontal);
export const StopIcon = wrap(Square);
export const PencilIcon = wrap(Pencil);
export const ArchiveIcon = wrap(Archive);
export const MicIcon = wrap(Mic);
export const MicOffIcon = wrap(MicOff);
export const SparklesIcon = wrap(Sparkles);
export const PaperclipIcon = wrap(Paperclip);
export const CopyIcon = wrap(Copy);
export const CheckIcon = wrap(Check);
export const ChevronDownIcon = wrap(ChevronDown);
export const ArrowDownIcon = wrap(ArrowDown);
export const ArrowRightIcon = wrap(ArrowRight);
export const ActivityIcon = wrap(Activity);
export const SunIcon = wrap(Sun);
export const MoonIcon = wrap(Moon);
export const DownloadIcon = wrap(Download);
export const SlidersIcon = wrap(SlidersHorizontal);
export const ExpandIcon = wrap(Maximize);
export const ShrinkIcon = wrap(Minimize);
export const ResetZoomIcon = wrap(RotateCcw);
export const ImageIcon = wrap(Image);
export const ThumbUpIcon = wrap(ThumbsUp);
export const ThumbDownIcon = wrap(ThumbsDown);
export const LinkIcon = wrap(Link);
export const PromptsIcon = wrap(BookText);
