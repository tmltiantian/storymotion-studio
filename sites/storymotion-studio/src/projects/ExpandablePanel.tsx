import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import type { CreatorTask } from "./creatorTask";

type ExpandablePanelProps = {
  title: string;
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
  tone?: CreatorTask["tone"];
};

export function ExpandablePanel({
  title,
  summary,
  children,
  defaultOpen = false,
  tone,
}: ExpandablePanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const triggerId = useId();

  return (
    <section className={`expandable-panel${tone ? ` tone-${tone}` : ""}`}>
      <button
        id={triggerId}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span>
          <strong>{title}</strong>
          <small>{summary}</small>
        </span>
        <ChevronDown aria-hidden="true" size={17} />
      </button>
      {open ? (
        <div id={panelId} role="region" aria-labelledby={triggerId}>
          {children}
        </div>
      ) : null}
    </section>
  );
}
