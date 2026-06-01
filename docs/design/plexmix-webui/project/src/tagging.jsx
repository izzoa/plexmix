// PlexMix WebUI — AI Tagging
const { useState, useRef } = React;
function Tagging({ onToast }) {
  const [genre, setGenre] = useState("Any genre");
  const [onlyUntagged, setOnlyUntagged] = useState(true);
  const [preview, setPreview] = useState(null);
  const [running, setRunning] = useState(false);
  const [batch, setBatch] = useState(0);
  const [done, setDone] = useState(0);
  const [progress, setProgress] = useState(0);
  const cancelRef = useRef(false);
  const totalBatches = 14;
  const [recent, setRecent] = useState(() => TRACKS.slice(0, 8).map((t, i) => ({ ...t, _id: i })));
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");

  function doPreview() {
    const n = genre === "Any genre" ? 688 : Math.floor(120 + Math.random() * 280);
    setPreview(n);
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function start() {
    const total = preview || 688;
    cancelRef.current = false; setRunning(true); setBatch(0); setDone(0); setProgress(0);
    for (let b = 1; b <= totalBatches; b++) {
      if (cancelRef.current) break;
      setBatch(b);
      await wait(420);
      setDone(Math.round((b / totalBatches) * total));
      setProgress(Math.round((b / totalBatches) * 100));
    }
    if (!cancelRef.current) {
      setRunning(false);
      onToast(`Tagged ${total} tracks`, "success");
      // shuffle recent to feel updated
      setRecent(TRACKS.slice(0, 8).map((t, i) => ({ ...t, _id: i })));
    } else { setRunning(false); }
  }
  function cancel() { cancelRef.current = true; onToast("Tagging cancelled", "info"); }

  function saveEdit(id) {
    setRecent((rs) => rs.map((r) => r._id === id ? { ...r, tags: draft } : r));
    setEditing(null); onToast("Tags updated", "success");
  }

  return (
    <div className="scroll"><div className="page">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }} className="section">
        {/* preset */}
        <div className="card qa primary" style={{ cursor: "default", alignItems: "flex-start", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, width: "100%" }}>
            <div className="ico" style={{ background: "var(--brand-3)" }}><Icon name="wand-sparkles" size={20} color="var(--brand-9)" /></div>
            <div><div className="qt">Tag All Untagged</div><div className="qd">688 tracks · ~3–5 mood tags each</div></div>
          </div>
          <button className="btn btn-3 btn-primary glow" style={{ width: "100%" }} disabled={running}
                  onClick={() => { setPreview(688); start(); }}>
            <Icon name="sparkles" size={16} /> Tag 688 untagged tracks
          </button>
        </div>

        {/* custom builder */}
        <div className="card" style={{ padding: "var(--card-pad)" }}>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 15, marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="filter" size={16} color="var(--fg-2)" /> Custom selection
          </div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
            <select className="minisel" style={{ flex: 1 }} value={genre} onChange={(e) => { setGenre(e.target.value); setPreview(null); }}>
              <option>Any genre</option>{GENRES.map((g) => <option key={g}>{g}</option>)}
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--fg-2)", whiteSpace: "nowrap" }}>
              <input type="checkbox" checked={onlyUntagged} onChange={(e) => { setOnlyUntagged(e.target.checked); setPreview(null); }} /> Only untagged
            </label>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button className="btn btn-3 btn-soft" onClick={doPreview}><Icon name="eye" size={15} /> Preview</button>
            {preview != null && <span className="mono" style={{ fontSize: 14 }}><b style={{ color: "var(--brand-11)" }}>{preview}</b> tracks match</span>}
            <span style={{ flex: 1 }} />
            <button className="btn btn-3 btn-primary" disabled={!preview || running} onClick={start}>Start</button>
          </div>
        </div>
      </div>

      {/* progress */}
      {running && (
        <div className="card section fade-up" style={{ padding: "var(--card-pad)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
            <Icon name="loader" size={18} className="spin" color="var(--brand-9)" />
            <span style={{ fontWeight: 600 }}>Tagging in progress</span>
            <span className="mono" style={{ color: "var(--fg-3)" }}>Batch {batch}/{totalBatches}</span>
            <span style={{ flex: 1 }} />
            <span className="mono"><b>{done}</b> tags generated</span>
            <button className="btn btn-sm btn-ghost" onClick={cancel}><Icon name="x" size={14} /> Cancel</button>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div className="pbar"><div className="pfill" style={{ width: progress + "%" }} /></div>
            <span className="mono" style={{ minWidth: 44 }}>{progress}%</span>
          </div>
        </div>
      )}

      {/* recent */}
      <div className="section">
        <div className="section-head"><h2>Recently Tagged</h2></div>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Title</th><th>Artist</th><th>Mood tags</th><th style={{ width: 160 }}>Environment</th><th style={{ width: 160 }}>Instruments</th><th style={{ width: 50 }}></th></tr></thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t._id}>
                  <td><span style={{ fontWeight: 500 }}>{t.t}</span></td>
                  <td className="fg2">{t.a}</td>
                  <td style={{ minWidth: 220 }}>
                    {editing === t._id
                      ? <input className="input" style={{ padding: "5px 8px", fontSize: 13 }} autoFocus value={draft}
                               onChange={(e) => setDraft(e.target.value)}
                               onKeyDown={(e) => { if (e.key === "Enter") saveEdit(t._id); if (e.key === "Escape") setEditing(null); }} />
                      : <TagPills tags={t.tags} />}
                  </td>
                  <td className="fg3" style={{ fontSize: 13 }}>{t.env}</td>
                  <td className="fg3" style={{ fontSize: 13 }}>{t.inst}</td>
                  <td>
                    {editing === t._id
                      ? <button className="icon-btn" onClick={() => saveEdit(t._id)}><Icon name="check" size={15} color="var(--pm-success)" /></button>
                      : <button className="icon-btn" onClick={() => { setEditing(t._id); setDraft(t.tags); }}><Icon name="pencil" size={14} /></button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div></div>
  );
}
Object.assign(window, { Tagging });
