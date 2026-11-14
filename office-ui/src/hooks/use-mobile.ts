import * as React from "react"

const MOBILE_BREAKPOINT = 768

export function useIsMobile(breakpoint = MOBILE_BREAKPOINT) {
  const [isMobile, setIsMobile] = React.useState(false)

  React.useEffect(() => {
    // matchMedia is absent in jsdom; innerWidth keeps the answer right there too.
    const mql = window.matchMedia?.(`(max-width: ${breakpoint - 1}px)`)
    const onChange = () => setIsMobile(window.innerWidth < breakpoint)
    mql?.addEventListener("change", onChange)
    onChange()
    return () => mql?.removeEventListener("change", onChange)
  }, [breakpoint])

  return isMobile
}
