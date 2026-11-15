import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

/** A measured disclosure that stays mounted through polling and dismissal. */
export default function DisclosurePopover({ open, onClose, trigger, id, label, className, children }: {
  open: boolean; onClose: () => void; trigger: RefObject<HTMLButtonElement>;
  id: string; label: string; className: string; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    element.toggleAttribute("inert", !open);
    const finish = () => { element.style.height = open ? "auto" : "0px"; element.style.opacity = open ? "1" : "0"; };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finish(); return; }
    element.style.height = `${element.getBoundingClientRect().height}px`;
    void element.offsetHeight;
    let timeout = 0;
    const frame = requestAnimationFrame(() => {
      element.style.height = open ? `${element.scrollHeight}px` : "0px";
      element.style.opacity = open ? "1" : "0";
      timeout = window.setTimeout(finish, 200);
    });
    const transitioned = (event: TransitionEvent) => { if (event.target === element && event.propertyName === "height") finish(); };
    element.addEventListener("transitionend", transitioned);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timeout); element.removeEventListener("transitionend", transitioned); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const available = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), summary, a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])') || []).filter((element) => element.getClientRects().length > 0 && !element.closest("[inert]"));
    const frame = requestAnimationFrame(() => (available()[0] || panel.current)?.focus({ preventScroll: true }));
    const dismiss = () => { onClose(); trigger.current?.focus(); };
    const pointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) onClose();
    };
    const focus = (event: FocusEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) onClose();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); dismiss(); return; }
      if (!(event.target instanceof HTMLElement) || !panel.current?.contains(event.target)) return;
      const items = available(), index = items.indexOf(event.target);
      if (event.key === "Tab" && ((event.shiftKey && index <= 0) || (!event.shiftKey && index === items.length - 1))) {
        if (event.shiftKey) { event.preventDefault(); trigger.current?.focus({ preventScroll: true }); }
        else {
          const all = Array.from(document.querySelectorAll<HTMLElement>('button:not(:disabled), summary, a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')).filter((element) => element.getClientRects().length > 0 && !element.closest("[inert]") && !panel.current?.contains(element));
          const next = trigger.current ? all[all.indexOf(trigger.current) + 1] : undefined;
          if (next) { event.preventDefault(); next.focus({ preventScroll: true }); }
        }
        onClose();
      } else if (!["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && items.length) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
        items[next].focus({ preventScroll: true });
      }
    };
    document.addEventListener("pointerdown", pointer);
    document.addEventListener("focusin", focus);
    document.addEventListener("keydown", key);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("pointerdown", pointer); document.removeEventListener("focusin", focus); document.removeEventListener("keydown", key); };
  }, [open, onClose, trigger]);
  return <div ref={panel} id={id} className={`disclosure-popover ${className}`} role="dialog" tabIndex={-1} aria-label={label} aria-hidden={!open}>
    <div className="popover-surface">{children}</div>
  </div>;
}
