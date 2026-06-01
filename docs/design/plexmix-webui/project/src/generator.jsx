// PlexMix WebUI — Generator (the showpiece)
const { useState, useEffect, useRef } = React;
const GEN_PHASES = [
  { key: "embed",  label: "Embed vibe",   icon: "brain" },
  { key: "search", label: "Vector search", icon: "search" },
  { key: "filter", label: "Diversity",     icon: "layers" },
  { key: "order",  label: "Order set",     icon: "audio-waveform" },
];

// ---- particle vector-field visualisation ------------------------
function ThinkViz({ phase, theme }) {
  const ref = useRef(null);
  const stateRef = useRef({ parts: [], raf: 0, phase: 0, w: 0, h: 0 });

  useEffect(() => { stateRef.current.phase = phase; assignTargets(); }, [phase]);

  function rand(a, b) { return a + Math.random() * (b - a); }
  function assignTargets() {
    const S = stateRef.current; const { w, h, parts } = S;
    if (!w) return;
    const cx = w / 2, cy = h / 2;
    parts.forEach((p, i) => {
      const ph = S.phase;
      if (ph === 0) { // converge into a tight cluster (the query embedding)
        const a = rand(0, Math.PI * 2), r = rand(0, 46);
        p.tx = cx + Math.cos(a) * r; p.ty = cy + Math.sin(a) * r;
        p.hl = i < 26; p.dim = 0.5;
      } else if (ph === 1) { // scatter to a field; near ones become candidates
        p.tx = rand(w * 0.08, w * 0.92); p.ty = rand(h * 0.14, h * 0.86);
        const d = Math.hypot(p.tx - cx, p.ty - cy);
        p.hl = d < Math.min(w, h) * 0.34; p.dim = p.hl ? 1 : 0.28;
      } else if (ph === 2) { // keep ~48 candidates bright, fade rest
        p.hl = i % 6 === 0; p.dim = p.hl ? 1 : 0.08;
      } else { // order onto an energy curve
        if (p.hl) {
          const idx = Math.floor(i / 6);
          const tx = w * 0.1 + (idx / 7) * w * 0.8;
          const ty = cy - Math.sin((idx / 7) * Math.PI * 1.6) * h * 0.26;
          p.tx = tx; p.ty = ty; p.dim = 1;
        } else { p.dim = 0.05; }
      }
    });
  }

  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const ctx = cv.getContext("2d");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function size() {
      const r = cv.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = r.width * dpr; cv.height = r.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const S = stateRef.current; S.w = r.width; S.h = r.height;
      if (!S.parts.length) {
        S.parts = Array.from({ length: 300 }, () => ({
          x: rand(0, r.width), y: rand(0, r.height), tx: rand(0, r.width), ty: rand(0, r.height),
          hl: false, dim: 0.4, size: rand(1.1, 2.4),
        }));
      }
      assignTargets();
    }
    size();
    window.addEventListener("resize", size);
    const orange = "#F97316";
    const base = theme === "dark" ? "120,120,135" : "150,150,160";
    function frame() {
      const S = stateRef.current; const { w, h, parts } = S;
      ctx.clearRect(0, 0, w, h);
      // links between bright nodes
      ctx.lineWidth = 1;
      for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        const k = reduce ? 1 : 0.08;
        p.x += (p.tx - p.x) * k; p.y += (p.ty - p.y) * k;
        if (p.hl) {
          for (let j = i + 1; j < parts.length; j++) {
            const q = parts[j];
            if (!q.hl) continue;
            const d = Math.hypot(p.x - q.x, p.y - q.y);
            if (d < 74) { ctx.strokeStyle = `rgba(249,115,22,${0.12 * (1 - d / 74)})`; ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke(); }
          }
        }
      }
      for (const p of parts) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.hl ? p.size + 0.7 : p.size, 0, Math.PI * 2);
        ctx.fillStyle = p.hl ? `rgba(249,115,22,${p.dim})` : `rgba(${base},${p.dim})`;
        ctx.fill();
      }
      stateRef.current.raf = requestAnimationFrame(frame);
    }
    frame();
    return () => { cancelAnimationFrame(stateRef.current.raf); window.removeEventListener("resize", size); };
  }, [theme]);

  return <canvas ref={ref} />;
}

// ---- main generator ---------------------------------------------
function Generator({ onToast, seed, clearSeed }) {
  const [mode, setMode] = useState("idle"); // idle | thinking | results
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState(false);
  const [showAdv, setShowAdv] = useState(false);
  const [maxTracks, setMaxTracks] = useState(30);
  const [ordering, setOrdering] = useState("energy");
  const [genre, setGenre] = useState("Any genre");
  const [phase, setPhase] = useState(0);
  const [progress, setProgress] = useState(0);
  const [log, setLog] = useState([]);
  const [tracks, setTracks] = useState([]);
  const cancelRef = useRef(false);
  const logRef = useRef(null);

  useEffect(() => {
    if (seed) { setQuery(seed); clearSeed && clearSeed(); setTimeout(() => run(seed), 120); }
  }, [seed]);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  function pushLog(line) { setLog((l) => [...l, line]); }

  async function run(qOverride) {
    const q = (qOverride || query).trim();
    if (!q) { onToast("Describe a vibe first", "error"); return; }
    cancelRef.current = false;
    setMode("thinking"); setPhase(0); setProgress(0); setLog([]); setTracks([]);
    const ORD = { similarity: "similarity", random: "random", alternating: "alternating-artists", energy: "energy-curve" }[ordering];

    pushLog({ p: "$", t: `plexmix create "${q}"`, c: "pmt" });
    await wait(420); if (cancelRef.current) return;
    // phase 0 — embed
    pushLog({ t: "Embedding query · gemini-embedding-001 · 3072d", c: "dim" });
    await stepProgress(0, 24); if (cancelRef.current) return;
    pushLog({ t: "✓ query vector ready", c: "ok" });

    // phase 1 — search
    setPhase(1);
    pushLog({ t: `FAISS search · scanning ${LIB.totalTracks.toLocaleString()} embedded tracks`, c: "dim" });
    await stepProgress(24, 56); if (cancelRef.current) return;
    pushLog({ t: "✓ 312 candidate tracks within similarity threshold", c: "ok" });

    // phase 2 — diversity
    setPhase(2);
    pushLog({ t: `Diversity selection · max ${maxTracks} · de-clustering artists & albums`, c: "dim" });
    await stepProgress(56, 80); if (cancelRef.current) return;
    pushLog({ t: `✓ ${maxTracks} tracks selected · 14 artists`, c: "ok" });

    // phase 3 — order
    setPhase(3);
    pushLog({ t: `Ordering · strategy = ${ORD}`, c: "dim" });
    await stepProgress(80, 100); if (cancelRef.current) return;
    pushLog({ t: "✓ playlist ready", c: "ok" });
    await wait(380); if (cancelRef.current) return;

    setTracks(buildPlaylist(maxTracks, genre));
    setMode("results");
  }

  async function stepProgress(from, to) {
    const steps = 14;
    for (let i = 1; i <= steps; i++) {
      if (cancelRef.current) return;
      setProgress(Math.round(from + (to - from) * (i / steps)));
      await wait(38 + Math.random() * 34);
    }
  }

  function buildPlaylist(n, g) {
    let pool = TRACKS.filter((t) => g === "Any genre" || t.g === g);
    if (pool.length < 4) pool = TRACKS;
    const out = [];
    for (let i = 0; i < n; i++) { out.push({ ...pool[i % pool.length], _id: i }); }
    return out;
  }

  function cancel() { cancelRef.current = true; setMode("idle"); }
  function regenerate() { run(); }
  function removeTrack(id) { setTracks((ts) => ts.filter((t) => t._id !== id)); }

  const totalDur = tracks.reduce((s, t) => s + t.d, 0);

  // ---------- IDLE ----------
  if (mode === "idle") {
    return (
      <div className="gen-stage">
        <div className="gen-hero">
          <div className="gen-hero-glow" />
          <div className="gen-hero-inner">
            <div className="gen-kicker"><Icon name="sparkles" size={14} /> AI Playlist Generator</div>
            <h1 className="gen-title">What should your<br /><span className="accent">library</span> sound like?</h1>
            <p className="gen-sub">Describe a mood, a moment, or a memory. PlexMix searches every track you own and curates a playlist that fits — no streaming catalog, just your music.</p>

            <div className={"prompt-box" + (focus ? " focus" : "")}>
              <textarea className="prompt-input" rows={2} placeholder="rainy day melancholy with a little hope…"
                value={query} onChange={(e) => setQuery(e.target.value)}
                onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run(); }} />
              <div className="prompt-foot">
                <button className={"opt-btn" + (showAdv ? " on" : "")} onClick={() => setShowAdv((s) => !s)}>
                  <Icon name="sliders-horizontal" size={15} /> Options
                  <Icon name={showAdv ? "chevron-up" : "chevron-down"} size={14} />
                </button>
                <span className="charcount">{query.length}/280</span>
                <span className="spacer" />
                <button className="btn btn-3 btn-primary glow" onClick={() => run()} disabled={!query.trim()}>
                  <Icon name="sparkles" size={16} /> Generate
                  <span className="kbd" style={{ background: "rgba(0,0,0,.14)", color: "inherit", border: "none" }}>⌘↵</span>
                </button>
              </div>
              {showAdv && (
                <div className="adv-panel">
                  <div>
                    <div className="adv-label">Tracks <span className="val">{maxTracks}</span></div>
                    <input type="range" min={10} max={100} step={5} value={maxTracks} onChange={(e) => setMaxTracks(+e.target.value)} />
                  </div>
                  <div>
                    <div className="adv-label">Genre focus</div>
                    <select className="select" value={genre} onChange={(e) => setGenre(e.target.value)}>
                      <option>Any genre</option>
                      {GENRES.map((g) => <option key={g}>{g}</option>)}
                    </select>
                  </div>
                  <div className="adv-full">
                    <div className="adv-label">Track ordering</div>
                    <div className="seg">
                      {[["similarity", "Similarity"], ["random", "Random"], ["alternating", "Alt. artists"], ["energy", "Energy curve"]].map(([k, l]) => (
                        <button key={k} className={ordering === k ? "on" : ""} onClick={() => setOrdering(k)}>{l}</button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="vibe-row">
              {VIBES.slice(0, 6).map((v) => (
                <button key={v} className="vibe-pill" onClick={() => { setQuery(v); run(v); }}>{v}</button>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---------- THINKING ----------
  if (mode === "thinking") {
    return (
      <div className="gen-stage">
        <div className="think">
          <div className="think-inner fade-up">
            <div className="think-head">
              <div className="think-query"><Icon name="sparkles" size={15} color="var(--brand-9)" /> Curating <b>“{query}”</b></div>
            </div>
            <div className="viz-wrap">
              <ThinkViz phase={phase} theme={document.documentElement.classList.contains("dark") ? "dark" : "light"} />
              <div className="viz-phase-label">{GEN_PHASES[phase].label} · {progress}%</div>
            </div>
            <div className="phase-track">
              {GEN_PHASES.map((p, i) => (
                <div key={p.key} className={"phase-node " + (i < phase ? "done" : i === phase ? "active" : "")}>
                  <div className="phase-line"><div className="fill" style={{ width: i < phase ? "100%" : "0%" }} /></div>
                  <div className="phase-dot"><Icon name={i < phase ? "check" : p.icon} size={16} /></div>
                  <div className="phase-label">{p.label}</div>
                </div>
              ))}
            </div>
            <div className="gen-progress">
              <div className="pbar"><div className="pfill" style={{ width: progress + "%" }} /></div>
              <span className="pct">{progress}%</span>
              <button className="btn btn-sm btn-ghost" onClick={cancel}><Icon name="x" size={14} /> Cancel</button>
            </div>
            <div className="genlog" ref={logRef}>
              {log.map((l, i) => (
                <div className="ln" key={i}>
                  {l.p && <span className="pmt">{l.p}</span>}
                  <span className={l.c || ""}>{l.t}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---------- RESULTS ----------
  return (
    <div className="scroll"><div className="gen-results fade-up">
      <div className="result-head">
        <div className="result-cover">
          {tracks.slice(0, 4).map((t, i) => <div key={i} style={{ background: artColors(t.al + i) }} />)}
        </div>
        <div className="result-meta">
          <div className="rk">Generated playlist</div>
          <h1>{query.charAt(0).toUpperCase() + query.slice(1)}</h1>
          <div className="result-stats">
            <span className="mono">{tracks.length} tracks</span> ·
            <span className="mono">{fmtTotal(totalDur)}</span> ·
            <span>ordered by</span> <span className="badge badge-orange">{ordering === "energy" ? "energy curve" : ordering}</span>
          </div>
          <div className="result-actions">
            <button className="btn btn-3 btn-blue" onClick={() => onToast("Saved to Plex · “" + query + "”", "success")}><Icon name="server" size={16} /> Save to Plex</button>
            <button className="btn btn-3 btn-green" onClick={() => onToast("Saved to local library", "success")}><Icon name="hard-drive" size={16} /> Save Locally</button>
            <button className="btn btn-3 btn-soft" onClick={() => onToast("Exported playlist.m3u", "info")}><Icon name="download" size={16} /> Export M3U</button>
            <button className="btn btn-3 btn-outline" onClick={regenerate}><Icon name="refresh-cw" size={16} /> Regenerate</button>
          </div>
        </div>
      </div>

      <div className="tbl-wrap" style={{ padding: 6 }}>
        {tracks.map((t, i) => (
          <div className="trk" key={t._id}>
            <span className="grip"><Icon name="grip-vertical" size={15} /></span>
            <span className="tnum">{i + 1}</span>
            <span className="play"><Icon name="play" size={14} /></span>
            <Art seed={t.al} size={38} />
            <div className="tinfo">
              <div className="ttitle">{t.t}</div>
              <div className="tartist">{t.a}</div>
            </div>
            <span className="talbum">{t.al}</span>
            <span className="tdur">{fmtDur(t.d)}</span>
            <button className="icon-btn trm" title="Remove" onClick={() => removeTrack(t._id)}><Icon name="x" size={15} /></button>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 18, textAlign: "center" }}>
        <button className="btn btn-3 btn-ghost" onClick={() => setMode("idle")}><Icon name="plus" size={16} /> New playlist</button>
      </div>
    </div></div>
  );
}

Object.assign(window, { Generator });
