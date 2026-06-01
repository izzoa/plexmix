// PlexMix WebUI — app shell: icon rail, top bar, command palette
const { useState, useEffect, useRef } = React;
const NAV = [
{ group: "Main", items: [
  { id: "dashboard", label: "Dashboard", icon: "layout-dashboard", key: "D" },
  { id: "generator", label: "Generator", icon: "sparkles", key: "G" },
  { id: "library", label: "Library", icon: "library", key: "L" }]
},
{ group: "Tools", items: [
  { id: "tagging", label: "Tagging", icon: "tags", key: "T" },
  { id: "history", label: "History", icon: "history", key: "H" }]
},
{ group: "System", items: [
  { id: "doctor", label: "Doctor", icon: "stethoscope", key: "X", badge: true },
  { id: "settings", label: "Settings", icon: "settings", key: "S" }]
}];


const PAGE_META = {
  dashboard: { title: "Dashboard", sub: "Your library at a glance" },
  generator: { title: "Generator", sub: "Describe a vibe, let AI curate the mix" },
  library: { title: "Library", sub: `${LIB.totalTracks.toLocaleString()} tracks synced from Plex` },
  tagging: { title: "AI Tagging", sub: "Mood, environment & instrument tags" },
  history: { title: "History", sub: "Saved & generated playlists" },
  doctor: { title: "Doctor", sub: "Diagnostics & system health" },
  settings: { title: "Settings", sub: "Connections, providers & advanced" }
};

function IconRail({ page, setPage, theme, toggleTheme }) {
  const logo = theme === "dark" ? "assets/logo-dark.svg" : "assets/logo-light.svg";
  return (
    <nav className="rail">
      <img className="rail-logo" src={logo} alt="PlexMix" onClick={() => setPage("generator")} title="PlexMix" />
      <div className="rail-sep" />
      {NAV.map((grp, gi) =>
      <React.Fragment key={grp.group}>
          {gi > 0 && <div className="rail-sep" />}
          <div className="rail-group">
            {grp.items.map((it) =>
          <div key={it.id}
          className={"rail-item" + (page === it.id ? " active" : "")}
          onClick={() => setPage(it.id)}>
                <Icon name={it.icon} size={20} />
                {it.badge && page !== it.id && <span className="rail-badge" />}
                <span className="rail-tip">{it.label}<span className="k">g {it.key.toLowerCase()}</span></span>
              </div>
          )}
          </div>
        </React.Fragment>
      )}
      <div className="rail-spacer" />
      <div className="rail-group">
        <div className="rail-item" onClick={toggleTheme}>
          <Icon name={theme === "dark" ? "sun" : "moon"} size={19} />
          <span className="rail-tip">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
        </div>
      </div>
    </nav>);

}

function TopBar({ page, onOpenCmd, theme, toggleTheme, setPage }) {
  const meta = PAGE_META[page] || { title: "", sub: "" };
  return (
    <header className="topbar">
      <div className="topbar-title">
        <span className="brand">PlexMix</span>
        <span className="crumb-sep">/</span>
        <span className="t" data-comment-anchor="d147d5e130-span-67-9">{meta.title}</span>
        <span className="s">{meta.sub}</span>
      </div>
      <div className="topbar-spacer" />
      <div className="cmd-trigger" onClick={onOpenCmd}>
        <Icon name="search" size={15} />
        <span>Search or jump to…</span>
        <span className="kbd">⌘K</span>
      </div>
      <button className="icon-btn" title="Toggle theme" onClick={toggleTheme}>
        <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
      </button>
      <div className="avatar" title="izzo · Plex" onClick={() => setPage("settings")}>iz</div>
    </header>);

}

// ---- Command palette --------------------------------------------
function CommandPalette({ open, onClose, setPage, runAction }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {setQ("");setSel(0);setTimeout(() => inputRef.current && inputRef.current.focus(), 30);}
  }, [open]);

  const navItems = NAV.flatMap((g) => g.items.map((it) => ({
    type: "nav", id: it.id, title: it.label, sub: "Go to page", icon: it.icon,
    run: () => {setPage(it.id);onClose();}
  })));
  const actions = [
  { type: "action", id: "gen", title: "Generate a playlist", sub: "Start a new mix", icon: "sparkles", run: () => {setPage("generator");onClose();} },
  { type: "action", id: "sync", title: "Sync library", sub: "Pull latest from Plex", icon: "refresh-cw", run: () => {runAction("sync");onClose();} },
  { type: "action", id: "tag", title: "Tag all untagged tracks", sub: "688 tracks need tags", icon: "tags", run: () => {setPage("tagging");onClose();} },
  { type: "action", id: "doctor", title: "Run diagnostics", sub: "plexmix doctor", icon: "stethoscope", run: () => {setPage("doctor");onClose();} },
  { type: "action", id: "theme", title: "Toggle dark mode", sub: "Appearance", icon: "moon", run: () => {runAction("theme");onClose();} }];

  const vibes = VIBES.slice(0, 4).map((v) => ({
    type: "vibe", id: v, title: v, sub: "Generate this vibe", icon: "music",
    run: () => {runAction("vibe:" + v);onClose();}
  }));

  const all = [...actions, ...navItems, ...vibes];
  const ql = q.trim().toLowerCase();
  const filtered = ql ? all.filter((i) => (i.title + " " + i.sub).toLowerCase().includes(ql)) : all;

  useEffect(() => {setSel(0);}, [q]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {onClose();} else
      if (e.key === "ArrowDown") {e.preventDefault();setSel((s) => Math.min(s + 1, filtered.length - 1));} else
      if (e.key === "ArrowUp") {e.preventDefault();setSel((s) => Math.max(s - 1, 0));} else
      if (e.key === "Enter") {e.preventDefault();filtered[sel] && filtered[sel].run();}
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, sel, onClose]);

  if (!open) return null;
  const groups = [
  { label: "Actions", items: filtered.filter((i) => i.type === "action") },
  { label: "Jump to", items: filtered.filter((i) => i.type === "nav") },
  { label: "Quick vibes", items: filtered.filter((i) => i.type === "vibe") }].
  filter((g) => g.items.length);

  let idx = -1;
  return (
    <div className="cmdk-backdrop" onMouseDown={onClose}>
      <div className="cmdk" onMouseDown={(e) => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <Icon name="search" size={18} color="var(--fg-3)" />
          <input ref={inputRef} className="cmdk-input" placeholder="Search pages, actions, vibes…"
          value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="kbd">esc</span>
        </div>
        <div className="cmdk-list">
          {groups.length === 0 && <div style={{ padding: "28px 12px", textAlign: "center", color: "var(--fg-3)", fontSize: 14 }}>No matches for “{q}”</div>}
          {groups.map((g) =>
          <div key={g.label}>
              <div className="cmdk-group-label">{g.label}</div>
              {g.items.map((i) => {
              idx++;
              const mine = idx;
              return (
                <div key={i.id} className={"cmdk-item" + (sel === mine ? " sel" : "")}
                onMouseEnter={() => setSel(mine)} onClick={i.run}>
                    <span className="ci-ico"><Icon name={i.icon} size={16} /></span>
                    <div style={{ flex: 1 }}>
                      <div className="ci-title">{i.title}</div>
                      <div className="ci-sub">{i.sub}</div>
                    </div>
                    {sel === mine && <span className="kbd">↵</span>}
                  </div>);

            })}
            </div>
          )}
        </div>
        <div className="cmdk-foot">
          <span><span className="kbd">↑↓</span> navigate</span>
          <span><span className="kbd">↵</span> select</span>
          <span><span className="kbd">esc</span> close</span>
        </div>
      </div>
    </div>);

}

// ---- Toasts ------------------------------------------------------
function ToastHost({ toasts }) {
  const TONE = { success: ["circle-check", "var(--pm-success)"], info: ["info", "var(--pm-info)"], error: ["circle-alert", "var(--pm-error)"] };
  return (
    <div className="toast-wrap">
      {toasts.map((t) => {
        const [ic, col] = TONE[t.tone] || TONE.info;
        return (
          <div key={t.id} className="toast">
            <span className="t-ico"><Icon name={ic} size={18} color={col} /></span>
            <span className="t-msg">{t.msg}</span>
          </div>);

      })}
    </div>);

}

Object.assign(window, { NAV, PAGE_META, IconRail, TopBar, CommandPalette, ToastHost });