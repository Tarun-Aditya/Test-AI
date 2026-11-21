import { useEffect, useRef } from "react";

/** A confirmation dialog in the app's own style (focus moves in, Tab stays inside, Esc cancels, focus goes back). */
export default function Confirm({ ask, onAnswer }) {
  const box = useRef(null);
  const back = useRef(null);
  useEffect(() => {
    back.current = document.activeElement;
    const first = box.current?.querySelector("[data-first]");
    first?.focus();
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onAnswer(false); }
      if (e.key === "Tab") {
        const f = [...box.current.querySelectorAll("button")];
        if (!f.length) return;
        const i = f.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); back.current?.focus?.(); };
  }, [onAnswer]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onAnswer(false)}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="cfm-t" aria-describedby="cfm-b" ref={box}>
        <h3 id="cfm-t">{ask.title}</h3>
        {ask.body && <p id="cfm-b" className="mut">{ask.body}</p>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn" data-first onClick={() => onAnswer(false)}>{ask.cancel || "Cancel"}</button>
          <button className={"btn " + (ask.danger ? "danger solid" : "primary")} onClick={() => onAnswer(true)}>{ask.ok || "OK"}</button>
        </div>
      </div>
    </div>
  );
}
