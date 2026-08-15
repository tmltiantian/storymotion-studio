import {
  type RefObject,
  useLayoutEffect,
} from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableElements(surface: HTMLElement): HTMLElement[] {
  return Array.from(surface.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.hasAttribute("hidden"),
  );
}

export function useContainedSurface({
  surfaceRef,
  initialFocusRef,
  returnFocusRef,
  busyRef,
  onClose,
}: {
  surfaceRef: RefObject<HTMLElement | null>;
  initialFocusRef: RefObject<HTMLElement | null>;
  returnFocusRef: RefObject<HTMLElement | null>;
  busyRef: RefObject<boolean>;
  onClose: () => void;
}) {
  useLayoutEffect(() => {
    const surface = surfaceRef.current;
    const background = document.querySelector<HTMLElement>(".app-shell");
    if (!surface) return;

    const hadInert = background?.hasAttribute("inert") ?? false;
    const previousAriaHidden = background
      ? background.getAttribute("aria-hidden")
      : null;
    background?.setAttribute("inert", "");
    background?.setAttribute("aria-hidden", "true");
    const activeSurface = surface;
    const returnFocus = returnFocusRef.current;
    (initialFocusRef.current ?? activeSurface).focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busyRef.current) onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements(activeSurface);
      if (focusable.length === 0) {
        event.preventDefault();
        activeSurface.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !activeSurface.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !activeSurface.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (background) {
        if (!hadInert) background.removeAttribute("inert");
        if (previousAriaHidden === null) {
          background.removeAttribute("aria-hidden");
        } else {
          background.setAttribute("aria-hidden", previousAriaHidden);
        }
      }
      returnFocus?.focus();
    };
  }, [busyRef, initialFocusRef, onClose, returnFocusRef, surfaceRef]);
}
