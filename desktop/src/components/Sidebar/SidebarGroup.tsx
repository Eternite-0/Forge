import { useId, useState, type ReactNode } from "react";

export default function SidebarGroup({ label, children, action }: { label: string; children: ReactNode; action?: ReactNode }) {
  const [open, setOpen] = useState(true);
  const id = useId();
  return <section className="sidebar-group">
    <div className="sidebar-group-heading"><button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      <span>{label}</span><svg data-chevron viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
    </button>{action}</div>
    <div id={id} hidden={!open}>{children}</div>
  </section>;
}
