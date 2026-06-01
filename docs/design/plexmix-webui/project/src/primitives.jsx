// PlexMix UI Kit — shared primitives
const { useEffect, useRef } = React;

// Lucide icon rendered into a React-owned wrapper that React never
// reconciles children into (JSX renders an empty <span>), so mutating its
// innerHTML in an effect can't corrupt React's virtual DOM on re-render.
function toPascal(name) {
  return name.split("-").map((s) => s.charAt(0).toUpperCase() + s.slice(1)).join("");
}
function Icon({ name, size = 18, color, style, className }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !window.lucide) return;
    const node = lucide.icons[toPascal(name)];
    el.innerHTML = "";
    if (node) {
      const svg = lucide.createElement(node);
      svg.setAttribute("width", size);
      svg.setAttribute("height", size);
      svg.style.display = "block";
      el.appendChild(svg);
    }
  }, [name, size]);
  return (
    <span
      ref={ref}
      className={className}
      style={{ display: "inline-flex", width: size, height: size, color, flexShrink: 0, ...style }}
    />
  );
}

function Button({ variant = "soft", size = "3", icon, children, glow, disabled, onClick, title, style }) {
  const cls = [
    "btn",
    `btn-${size}`,
    `btn-${variant}`,
    glow ? "glow" : "",
    !children ? "btn-icon" : "",
  ].join(" ").trim();
  const iSize = size === "4" ? 18 : size === "sm" ? 14 : 16;
  return (
    <button className={cls} disabled={disabled} onClick={onClick} title={title} style={style}>
      {icon && <Icon name={icon} size={iSize} />}
      {children}
    </button>
  );
}

function Badge({ tone = "gray", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function StatusDot({ state }) {
  const cls = { success: "dot-success", error: "dot-error", warning: "dot-warning" }[state] || "dot-success";
  return <span className={`dot ${cls} ${state === "error" ? "pulse" : ""}`} />;
}

function StatTile({ label, value, icon, iconColor, iconBg, stagger }) {
  return (
    <div className={`stat fade-up ${stagger || ""}`}>
      <div className="ico" style={{ background: iconBg }}>
        <Icon name={icon} size={20} color={iconColor} />
      </div>
      <div>
        <div className="num">{value}</div>
        <div className="lab">{label}</div>
      </div>
    </div>
  );
}

// Comma-separated tag string → up to 3 soft pills + overflow count
function TagPills({ tags }) {
  if (!tags) return <span style={{ color: "var(--fg-3)" }}>-</span>;
  const arr = tags.split(",").map((t) => t.trim()).filter(Boolean);
  const shown = arr.slice(0, 3);
  const extra = arr.length - shown.length;
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
      {shown.map((t) => <Badge key={t} tone="orange">{t}</Badge>)}
      {extra > 0 && <span className="mono" style={{ fontSize: 12, color: "var(--fg-3)" }}>+{extra}</span>}
    </span>
  );
}

// ── States: callout, empty, skeleton ──────────────────────────────────
const CALLOUT_ICON = { info: "info", warning: "triangle-alert", error: "circle-alert", success: "circle-check" };
function Callout({ tone = "info", title, children }) {
  return (
    <div className={`callout callout-${tone}`}>
      <span className="c-ico"><Icon name={CALLOUT_ICON[tone]} size={16} /></span>
      <div>
        {title && <div className="c-title">{title}</div>}
        <div className="c-body">{children}</div>
      </div>
    </div>
  );
}

function EmptyState({ icon = "inbox", title, desc, action }) {
  return (
    <div className="empty">
      <span className="e-ico"><Icon name={icon} size={22} color="var(--fg-3)" /></span>
      <div className="e-title">{title}</div>
      {desc && <div className="e-desc">{desc}</div>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

function SkeletonRows({ rows = 6, cols = [40, "30%", "22%", "20%", 60] }) {
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 12px", borderBottom: r < rows - 1 ? "1px solid var(--border-subtle)" : "none" }}>
          {cols.map((w, c) => <div key={c} className="skeleton" style={{ height: 12, width: typeof w === "number" ? w : w }} />)}
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { Icon, Button, Badge, StatusDot, StatTile, TagPills, Callout, EmptyState, SkeletonRows });
