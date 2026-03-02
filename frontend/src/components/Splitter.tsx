import { useCallback, useRef } from "react";

interface Props {
  onResize: (width: number) => void;
}

export default function Splitter({ onResize }: Props) {
  const dragging = useRef(false);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      // Disable iframe pointer events during drag
      document.querySelectorAll("iframe").forEach((f) => {
        (f as HTMLIFrameElement).style.pointerEvents = "none";
      });

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const w = Math.max(240, Math.min(ev.clientX, window.innerWidth - 300));
        onResize(w);
      };

      const onUp = () => {
        dragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.querySelectorAll("iframe").forEach((f) => {
          (f as HTMLIFrameElement).style.pointerEvents = "";
        });
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [onResize]
  );

  return <div className="splitter" onMouseDown={onMouseDown} />;
}
