import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";

const focusable = 'button:not(:disabled), summary, a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
const visibleControls = (root: ParentNode) => Array.from(root.querySelectorAll<HTMLElement>(focusable)).filter(
  element => element.getClientRects().length > 0 && !element.closest("[inert]") && getComputedStyle(element).visibility === "visible",
);

/** Floating surfaces keep their natural size so content and shadow move together. */
export default function DisclosurePopover({ open, onClose, trigger, id, label, className, children, focusLast = false }: {
  open: boolean; onClose: () => void; trigger: RefObject<HTMLButtonElement>;
  id: string; label: string; className: string; children: ReactNode; focusLast?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = panel.current;
    node?.toggleAttribute("inert", !open);
    if (!open || !node) return;
    const focusEntry = () => {
      const items = visibleControls(node);
      const entry = items[focusLast ? items.length - 1 : 0];
      (entry || node).focus({ preventScroll: true });
      return !!entry && document.activeElement === entry;
    };
    if (focusEntry()) return;
    // Give descendants time to inherit the new visibility. Never steal focus
    // if the user has already moved to a specific control or out of the popover.
    const retry = window.setTimeout(() => {
      if (document.activeElement === node || document.activeElement === trigger.current) focusEntry();
    }, 50);
    return () => window.clearTimeout(retry);
  }, [open, focusLast, trigger]);
  useEffect(() => {
    if (!open) return;
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
      const items = visibleControls(panel.current), index = items.indexOf(event.target);
      if (event.key === "Tab" && ((event.shiftKey && index <= 0) || (!event.shiftKey && index === items.length - 1))) {
        if (event.shiftKey) { event.preventDefault(); trigger.current?.focus({ preventScroll: true }); }
        else {
          const all = visibleControls(document).filter(element => !panel.current?.contains(element));
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
    return () => { document.removeEventListener("pointerdown", pointer); document.removeEventListener("focusin", focus); document.removeEventListener("keydown", key); };
  }, [open, onClose, trigger]);
  return <div ref={panel} id={id} className={`disclosure-popover ${className}`} role="dialog" tabIndex={-1} aria-label={label} aria-hidden={!open} data-open={open}>
    <div className="popover-surface">{children}</div>
  </div>;
}
