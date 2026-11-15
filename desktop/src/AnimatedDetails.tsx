import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Keep native summary keyboard semantics, and retain the subtree through collapse. */
export default function AnimatedDetails({ className, summary, summaryLabel, summaryTitle, summaryAction, scrollKey, initialExpanded = false, onExpandedChange, children, onInspect }: {
  className: string; summary: ReactNode; summaryLabel?: string; summaryTitle?: string; scrollKey?: string;
  summaryAction?: ReactNode;
  initialExpanded?: boolean; onExpandedChange?: (expanded: boolean) => void; children: ReactNode; onInspect: () => void;
}) {
  const [expanded, setExpanded] = useState(initialExpanded);
  const [present, setPresent] = useState(initialExpanded);
  const initial = useRef(initialExpanded);
  const animatedExpanded = useRef(initialExpanded);
  const desired = useRef(initialExpanded);
  const panel = useRef<HTMLDivElement>(null);

  // Depend only on user expansion. Polling must not restart animations or move scroll positions.
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    element.toggleAttribute("inert", !expanded);
    // A regrouped event restores its measured-open state without replaying an animation.
    if (animatedExpanded.current === expanded) return;
    animatedExpanded.current = expanded;
    const finish = () => {
      element.style.height = desired.current ? "auto" : "0px";
      element.style.opacity = desired.current ? "1" : "0";
      if (!desired.current) setPresent(false);
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    const height = element.getBoundingClientRect().height;
    const opacity = getComputedStyle(element).opacity;
    element.style.height = `${height}px`;
    element.style.opacity = opacity;
    // Establish the current rendered height before transitioning to the measured target.
    void element.offsetHeight;
    let timeout = 0;
    const frame = requestAnimationFrame(() => {
      element.style.height = expanded ? `${element.scrollHeight}px` : "0px";
      element.style.opacity = expanded ? "1" : "0";
      const seconds = Number.parseFloat(getComputedStyle(element).getPropertyValue("--transition-duration-basic")) || .15;
      timeout = window.setTimeout(finish, seconds * 1000 + 50);
    });
    const transitioned = (event: TransitionEvent) => {
      if (event.target === element && event.propertyName === "height") finish();
    };
    element.addEventListener("transitionend", transitioned);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timeout); element.removeEventListener("transitionend", transitioned); };
  }, [expanded]);

  return <details className={`${className}${summaryAction ? " has-summary-action" : ""}`} data-scroll-key={scrollKey} open={present}>
    <summary aria-label={summaryLabel} aria-expanded={expanded} title={summaryTitle || undefined} onClick={(event) => {
      event.preventDefault();
      onInspect();
      desired.current = !desired.current;
      onExpandedChange?.(desired.current);
      setPresent(true);
      setExpanded(desired.current);
    }}>{summary}{summaryAction && <span className="detail-summary-action" onClick={(event) => event.stopPropagation()}>{summaryAction}</span>}</summary>
    <div ref={panel} className="detail-collapsible" style={{ height: initial.current ? "auto" : "0px", opacity: initial.current ? 1 : 0 }}>{children}</div>
  </details>;
}
