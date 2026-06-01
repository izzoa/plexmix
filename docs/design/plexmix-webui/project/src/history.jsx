// PlexMix WebUI — History
const { useState } = React;
function History({ onToast, setPage }) {
  const [open, setOpen] = useState(null);
  const [items, setItems] = useState(PLAYLISTS);
  const [sort, setSort] = useState("Newest");

  const sorted = [...items].sort((a, b) =>
    sort === "Newest" ? b.when.localeCompare(a.when) : sort === "Most tracks" ? b.count - a.count : a.name.localeCompare(b.name));

  function del(id) { setItems((it) => it.filter((p) => p.id !== id)); setOpen(null); onToast("Playlist deleted", "info"); }

  const detail = open && items.find((p) => p.id === open);
  const detailTracks = detail ? TRACKS.slice(0, Math.min(detail.count, TRACKS.length)).map((t, i) => ({ ...t, _id: i })) : [];

  return (
    <div className="scroll"><div className="page page-wide">
      <div className="filterbar">
        <span className="mono" style={{ fontSize: 13, color: "var(--fg-3)" }}>{items.length} saved playlists</span>
        <span style={{ flex: 1 }} />
        <select className="minisel" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option>Newest</option><option>Most tracks</option><option>Name</option>
        </select>
        <button className="btn btn-3 btn-primary glow" onClick={() => setPage("generator")}><Icon name="plus" size={16} /> New Playlist</button>
      </div>

      {items.length === 0 ? (
        <div className="empty">
          <span className="e-ico"><Icon name="list-music" size={22} color="var(--fg-3)" /></span>
          <div className="e-title">No playlists yet</div>
          <div className="e-desc">Generate your first playlist from a mood description and it will show up here.</div>
          <button className="btn btn-3 btn-primary" style={{ marginTop: 8 }} onClick={() => setPage("generator")}><Icon name="sparkles" size={16} /> Generate one</button>
        </div>
      ) : (
        <div className="pl-grid">
          {sorted.map((p, i) => (
            <div key={p.id} className={"card pl-card hover-lift fade-up s" + ((i % 6) + 1)} onClick={() => setOpen(p.id)}>
              <div className="pl-cover">
                {p.seeds.map((s, j) => <div key={j} style={{ background: artColors(s + j) }} />)}
                <div className="pl-overlay">
                  <div className="pob" title="Play"><Icon name="play" size={18} /></div>
                  <div className="pob" title="Export to Plex" onClick={(e) => { e.stopPropagation(); onToast("Exported to Plex", "success"); }}><Icon name="server" size={16} /></div>
                  <div className="pob" title="Export M3U" onClick={(e) => { e.stopPropagation(); onToast("Exported playlist.m3u", "info"); }}><Icon name="download" size={16} /></div>
                </div>
              </div>
              <div className="pl-body">
                <div className="pl-name">{p.name}</div>
                <div className="pl-mood">{p.mood}</div>
                <div className="pl-meta">{p.count} tracks · {fmtTotal(p.dur)}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {detail && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div style={{ display: "flex", gap: 16 }}>
                <div style={{ width: 72, height: 72, borderRadius: 12, overflow: "hidden", display: "grid", gridTemplateColumns: "1fr 1fr", flexShrink: 0 }}>
                  {detail.seeds.map((s, j) => <div key={j} style={{ background: artColors(s + j) }} />)}
                </div>
                <div>
                  <div className="rk" style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--accent-fg)" }}>Playlist</div>
                  <h3 style={{ margin: "3px 0 6px" }}>{detail.name}</h3>
                  <div className="mono" style={{ fontSize: 12.5, color: "var(--fg-3)" }}>{detail.count} tracks · {fmtTotal(detail.dur)} · {detail.when}</div>
                  <div style={{ marginTop: 8 }}><span className="badge badge-orange">{detail.mood}</span></div>
                </div>
              </div>
              <button className="icon-btn" onClick={() => setOpen(null)}><Icon name="x" size={18} /></button>
            </div>
            <div className="modal-body">
              <div style={{ padding: 4 }}>
                {detailTracks.map((t, i) => (
                  <div className="trk" key={t._id}>
                    <span className="tnum">{i + 1}</span>
                    <Art seed={t.al} size={34} />
                    <div className="tinfo"><div className="ttitle">{t.t}</div><div className="tartist">{t.a}</div></div>
                    <span className="tdur">{fmtDur(t.d)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn btn-3 btn-blue" onClick={() => onToast("Exported to Plex", "success")}><Icon name="server" size={16} /> Export to Plex</button>
              <button className="btn btn-3 btn-soft" onClick={() => onToast("Exported playlist.m3u", "info")}><Icon name="download" size={16} /> Export M3U</button>
              <span style={{ flex: 1 }} />
              <button className="btn btn-3 btn-ghost" style={{ color: "var(--pm-error)" }} onClick={() => del(detail.id)}><Icon name="trash-2" size={16} /> Delete</button>
            </div>
          </div>
        </div>
      )}
    </div></div>
  );
}
Object.assign(window, { History });
