// PlexMix WebUI — root app
const { useState, useEffect, useRef } = React;
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "dark": false,
  "density": "comfortable",
  "accent": "balanced"
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [page, setPage] = useState("generator");
  const [cmdOpen, setCmdOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [seed, setSeed] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState(0);
  const syncRef = useRef(false);

  const theme = t.dark ? "dark" : "light";
  const toggleTheme = () => setTweak("dark", !t.dark);

  useEffect(() => {
    const r = document.documentElement;
    r.classList.toggle("dark", !!t.dark);
    r.setAttribute("data-density", t.density);
    r.setAttribute("data-accent", t.accent);
  }, [t.dark, t.density, t.accent]);

  function addToast(msg, tone = "info") {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts, { id, msg, tone }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 2800);
  }

  async function startSync() {
    if (syncRef.current) return;
    syncRef.current = true; setSyncing(true); setSyncProgress(0);
    for (let i = 1; i <= 30; i++) {
      await new Promise((r) => setTimeout(r, 90));
      setSyncProgress(Math.round((i / 30) * 100));
    }
    setSyncing(false); syncRef.current = false;
    addToast("Library synced · 218 new tracks", "success");
  }

  function runAction(a) {
    if (a === "sync") { setPage("library"); startSync(); }
    else if (a === "theme") { toggleTheme(); }
    else if (a.startsWith("vibe:")) { setPage("generator"); setSeed(a.slice(5)); }
  }

  // keyboard: ⌘K palette, "/" palette, vim g+key nav
  useEffect(() => {
    let gPending = false, gTimer = null;
    const isTyping = (e) => { const el = e.target; return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable); };
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdOpen((o) => !o); return; }
      if (isTyping(e)) return;
      if (e.key === "/") { e.preventDefault(); setCmdOpen(true); return; }
      if (gPending) {
        const map = { d: "dashboard", g: "generator", l: "library", t: "tagging", h: "history", x: "doctor", s: "settings" };
        if (map[e.key.toLowerCase()]) { setPage(map[e.key.toLowerCase()]); }
        gPending = false; clearTimeout(gTimer); return;
      }
      if (e.key.toLowerCase() === "g") { gPending = true; gTimer = setTimeout(() => { gPending = false; }, 700); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  let body;
  if (page === "dashboard") body = <Dashboard setPage={setPage} onToast={addToast} runAction={runAction} />;
  else if (page === "generator") body = <Generator onToast={addToast} seed={seed} clearSeed={() => setSeed(null)} />;
  else if (page === "library") body = <Library onToast={addToast} syncing={syncing} syncProgress={syncProgress} startSync={startSync} />;
  else if (page === "tagging") body = <Tagging onToast={addToast} />;
  else if (page === "history") body = <History onToast={addToast} setPage={setPage} />;
  else if (page === "doctor") body = <Doctor onToast={addToast} />;
  else if (page === "settings") body = <Settings onToast={addToast} />;

  return (
    <div className="shell">
      <IconRail page={page} setPage={setPage} theme={theme} toggleTheme={toggleTheme} />
      <div className="main">
        <TopBar page={page} onOpenCmd={() => setCmdOpen(true)} theme={theme} toggleTheme={toggleTheme} setPage={setPage} />
        {body}
      </div>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} setPage={setPage} runAction={runAction} />
      <ToastHost toasts={toasts} />

      <TweaksPanel title="Appearance">
        <TweakSection label="Theme" />
        <TweakToggle label="Dark mode" value={t.dark} onChange={(v) => setTweak("dark", v)} />
        <TweakSection label="Layout" />
        <TweakRadio label="Density" value={t.density} options={["comfortable", "compact"]} onChange={(v) => setTweak("density", v)} />
        <TweakSection label="Accent" />
        <TweakRadio label="Orange intensity" value={t.accent} options={["subtle", "balanced", "vivid"]} onChange={(v) => setTweak("accent", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
