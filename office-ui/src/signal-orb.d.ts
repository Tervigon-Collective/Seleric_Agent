import type { DetailedHTMLProps, HTMLAttributes } from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "signal-orb": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & { state?: string; level?: string; particles?: string };
    }
  }
}
