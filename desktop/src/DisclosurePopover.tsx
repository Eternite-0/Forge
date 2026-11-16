import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

/** Floating surfaces keep their natural size so content and shadow move together. */
export default function DisclosurePopover({ open, onClose, trigger, id, label, className, children, focusLast = false }: {
  open: boolean; onClose: () => void; trigger: RefObject<HTMLButtonElement>;
  id: string; label: string; className: string; children: ReactNode; focusLast?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    panel.current?.toggleAttribute("inert", !open);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const available = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), summary, a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])') || []).filter((element) => element.getClientRects().length > 0 && !element.closest("[inert]"));
    const frame = requestAnimationFrame(() => {
      const items = available();
      (items[focusLast ? items.length - 1 : 0] || panel.current)?.focus({ preventScroll: true });
    });
    const dismiss = () => { onClose(); trigger.current?.focus(); };
    const pointer = (event: PointerEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) onClose();
    };
    const focus = (event: FocusEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) onClose();
    };
    const key = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
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
  }, [open, onClose, trigger, focusLast]);
  return <div ref={panel} id={id} className={`disclosure-popover ${className}`} role="dialog" tabIndex={-1} aria-label={label} aria-hidden={!open} data-open={open}>
    <div className="popover-surface">{children}</div>
  </div>;
}
