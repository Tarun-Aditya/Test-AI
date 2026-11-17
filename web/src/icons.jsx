const P = {
  dash: <><rect x="3" y="3" width="8" height="10" rx="1.5" /><rect x="13" y="3" width="8" height="6" rx="1.5" /><rect x="13" y="11" width="8" height="10" rx="1.5" /><rect x="3" y="15" width="8" height="6" rx="1.5" /></>,
  studio: <><path d="M4 5h16v11H4z" /><path d="M8 20h8M12 16v4" /></>,
  runs: <path d="M3 12h4l3 8 4-16 3 8h4" />,
  lib: <path d="M4 4h5v16H4zM10 4h4v16h-4zM15 5l4-1 3 15-4 1z" />,
  pc: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M17 6l3 3" /></>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  unlock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 7.5-2" /></>,
  eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  up: <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />,
  copy: <><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></>,
  spark: <path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" />,
  moon: <path d="M20 15A8 8 0 1 1 9 4a7 7 0 0 0 11 11z" />,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>,
  refresh: <path d="M20 11a8 8 0 1 0-2 6M20 4v7h-7" />,
  video: <><rect x="3" y="6" width="13" height="12" rx="2" /><path d="M16 10l5-3v10l-5-3z" /></>,
  doc: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 13h6M9 17h6" /></>,
  trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  layers: <path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5" />,
};

export default function Icon({ name, size = 18, style, fill }) {
  if (name === "play") return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" style={{ fill: "currentColor", stroke: "none", ...style }}><path d="M7 4v16l13-8z" /></svg>;
  return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" style={{ width: size, height: size, fill: fill || "none", ...style }}>{P[name]}</svg>;
}
