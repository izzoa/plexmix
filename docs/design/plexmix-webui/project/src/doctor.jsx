// PlexMix WebUI — Doctor
const { useState } = React;
function Doctor({ onToast }) {
  const CHECKS = [
    { id: "plex", name: "Plex connection", icon: "server", status: "pass", detail: "Connected to plex.local:32400 · library “Music”" },
    { id: "ai", name: "AI provider", icon: "brain", status: "pass", detail: "Google Gemini · gemini-2.0-flash · key valid" },
    { id: "emb", name: "Embedding provider", icon: "layers", status: "pass", detail: "gemini-embedding-001 · 3072d" },
    { id: "db", name: "SQLite database", icon: "database", status: "pass", detail: "184 MB · 12,480 tracks · schema v7" },
    { id: "faiss", name: "FAISS index", icon: "cpu", status: "warn", detail: "344 tracks missing embeddings — run embed to backfill" },
    { id: "dim", name: "Embedding dimensions", icon: "ruler", status: "pass", detail: "Index 3072d matches provider 3072d" },
    { id: "tags", name: "Tag coverage", icon: "tags", status: "warn", detail: "688 tracks untagged — generation quality may drop" },
    { id: "audio", name: "Audio analysis (Essentia)", icon: "audio-waveform", status: "idle", detail: "Optional · not installed" },
  ];
  const [results, setResults] = useState(CHECKS);
  const [running, setRunning] = useState(false);

  const STAT = {
    pass: { ic: "circle-check", col: "var(--pm-success)", bg: "var(--success-bg)", badge: "badge-green", word: "Pass" },
    warn: { ic: "triangle-alert", col: "var(--pm-warning)", bg: "var(--warning-bg)", badge: "badge-yellow", word: "Warn" },
    fail: { ic: "circle-x", col: "var(--pm-error)", bg: "var(--error-bg)", badge: "badge-red", word: "Fail" },
    idle: { ic: "minus", col: "var(--fg-3)", bg: "var(--surface-sunken)", badge: "badge-gray", word: "Skipped" },
  };
  const passed = results.filter((r) => r.status === "pass").length;
  const warned = results.filter((r) => r.status === "warn").length;

  async function runAll() {
    setRunning(true);
    setResults((rs) => rs.map((r) => ({ ...r, _checking: true })));
    for (let i = 0; i < CHECKS.length; i++) {
      await new Promise((r) => setTimeout(r, 240));
      setResults((rs) => rs.map((r, j) => j === i ? { ...r, _checking: false } : r));
    }
    setRunning(false);
    onToast(`Diagnostics complete · ${passed} passed, ${warned} warnings`, "info");
  }

  return (
    <div className="scroll"><div className="page">
      <div className="card statusbar section fade-up" style={{ alignItems: "center" }}>
        <div className="ico" style={{ width: 44, height: 44, borderRadius: 12, background: warned ? "var(--warning-bg)" : "var(--success-bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon name={warned ? "shield-alert" : "shield-check"} size={22} color={warned ? "var(--pm-warning)" : "var(--pm-success)"} />
        </div>
        <div>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 17 }}>{warned ? "System healthy, with notes" : "All systems healthy"}</div>
          <div className="mono" style={{ fontSize: 12.5, color: "var(--fg-3)", marginTop: 2 }}>{passed} passed · {warned} warnings · 1 skipped</div>
        </div>
        <span style={{ flex: 1 }} />
        <button className="btn btn-3 btn-primary glow" disabled={running} onClick={runAll}>
          <Icon name={running ? "loader" : "stethoscope"} size={16} className={running ? "spin" : ""} /> {running ? "Running…" : "Run All Checks"}
        </button>
      </div>

      {(warned > 0) && (
        <div className="callout callout-warning section">
          <span className="c-ico"><Icon name="triangle-alert" size={16} /></span>
          <div><div className="c-title">2 checks need attention</div>
            <div className="c-body">Backfill embeddings and tag the remaining tracks for best generation results. Run <code>plexmix embed</code> then <code>plexmix tag --all</code>.</div></div>
        </div>
      )}

      <div className="tbl-wrap">
        {results.map((r) => {
          const s = STAT[r.status];
          return (
            <div className="check" key={r.id}>
              <div className="ico" style={{ background: s.bg }}>
                {r._checking ? <Icon name="loader" size={18} className="spin" color="var(--fg-3)" /> : <Icon name={r.icon} size={18} color={s.col} />}
              </div>
              <div className="cinfo">
                <div className="cname">{r.name}</div>
                <div className="cdetail">{r._checking ? "checking…" : <span dangerouslySetInnerHTML={{ __html: r.detail.replace(/`([^`]+)`/g, "<code>$1</code>") }} />}</div>
              </div>
              {!r._checking && <span className={"badge " + s.badge}><Icon name={s.ic} size={12} /> {s.word}</span>}
            </div>
          );
        })}
      </div>
    </div></div>
  );
}
Object.assign(window, { Doctor });
