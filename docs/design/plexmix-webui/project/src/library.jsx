// PlexMix WebUI — Library
const { useState, useMemo } = React;
function Library({ onToast, syncing, syncProgress, startSync }) {
  const [search, setSearch] = useState("");
  const [genre, setGenre] = useState("All genres");
  const [embFilter, setEmbFilter] = useState("All");
  const [sel, setSel] = useState(() => new Set());
  const [page, setPage] = useState(0);
  const pageSize = 12;

  // expand pool to feel like a real library
  const pool = useMemo(() => {
    const out = [];
    for (let i = 0; i < 8; i++) TRACKS.forEach((t, j) => out.push({ ...t, _id: i * 100 + j, emb: t.emb && !(i === 3 && j % 4 === 0) }));
    return out;
  }, []);

  const filtered = pool.filter((t) => {
    if (genre !== "All genres" && t.g !== genre) return false;
    if (embFilter === "Embedded" && !t.emb) return false;
    if (embFilter === "Missing" && t.emb) return false;
    if (search && !(t.t + t.a + t.al).toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, pages - 1);
  const rows = filtered.slice(cur * pageSize, cur * pageSize + pageSize);
  const allSel = rows.length > 0 && rows.every((t) => sel.has(t._id));

  function toggle(id) { setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function toggleAll() { setSel((s) => { const n = new Set(s); allSel ? rows.forEach((t) => n.delete(t._id)) : rows.forEach((t) => n.add(t._id)); return n; }); }

  return (
    <div className="scroll">
      {syncing && <div className="loadbar"><div className="ind" /></div>}
      <div className="page page-wide">
        {syncing && (
          <div className="callout callout-info section fade-up" style={{ alignItems: "center" }}>
            <Icon name="loader" size={16} className="spin" />
            <div className="c-body" style={{ flex: 1, display: "flex", alignItems: "center", gap: 14 }}>
              <span className="mono" style={{ minWidth: 230 }}>Fetching track {Math.round(syncProgress / 100 * LIB.totalTracks).toLocaleString()} / {LIB.totalTracks.toLocaleString()}</span>
              <div className="pbar" style={{ maxWidth: 320 }}><div className="pfill" style={{ width: syncProgress + "%" }} /></div>
              <span className="mono">{syncProgress}%</span>
            </div>
          </div>
        )}

        <div className="filterbar">
          <div className="search">
            <Icon name="search" size={16} color="var(--fg-3)" />
            <input placeholder="Search title, artist, album…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} />
            {search && <Icon name="x" size={15} color="var(--fg-3)" style={{ cursor: "pointer" }} onClick={() => setSearch("")} />}
          </div>
          <select className="minisel" value={genre} onChange={(e) => { setGenre(e.target.value); setPage(0); }}>
            <option>All genres</option>{GENRES.map((g) => <option key={g}>{g}</option>)}
          </select>
          <select className="minisel" value={embFilter} onChange={(e) => { setEmbFilter(e.target.value); setPage(0); }}>
            <option>All</option><option>Embedded</option><option>Missing</option>
          </select>
          <span style={{ flex: 1 }} />
          {sel.size > 0 && (
            <button className="btn btn-3 btn-soft" onClick={() => onToast(`Embedding ${sel.size} tracks…`, "info")}>
              <Icon name="layers" size={15} /> Embed {sel.size}
            </button>
          )}
          <button className="btn btn-3 btn-primary glow" disabled={syncing} onClick={startSync}>
            <Icon name={syncing ? "loader" : "refresh-cw"} size={16} className={syncing ? "spin" : ""} /> {syncing ? "Syncing…" : "Sync Library"}
          </button>
        </div>

        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 38 }}><input type="checkbox" checked={allSel} onChange={toggleAll} /></th>
                <th>Title</th><th>Artist</th><th>Album</th><th style={{ width: 90 }}>Genre</th>
                <th style={{ width: 56 }}>Year</th><th>Tags</th><th style={{ width: 64 }}>Embed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t._id}>
                  <td><input type="checkbox" checked={sel.has(t._id)} onChange={() => toggle(t._id)} /></td>
                  <td><div style={{ display: "flex", alignItems: "center", gap: 11 }}><Art seed={t.al} size={34} /><span style={{ fontWeight: 500 }}>{t.t}</span></div></td>
                  <td className="fg2">{t.a}</td>
                  <td className="fg2">{t.al}</td>
                  <td><span className="badge badge-gray">{t.g}</span></td>
                  <td className="mono fg2">{t.y}</td>
                  <td><TagPills tags={t.tags} /></td>
                  <td>{t.emb
                    ? <span className="badge badge-green"><Icon name="check" size={12} /> yes</span>
                    : <span className="badge badge-gray">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 16 }}>
          <span className="mono" style={{ fontSize: 13, color: "var(--fg-3)" }}>
            {filtered.length.toLocaleString()} tracks{sel.size ? ` · ${sel.size} selected` : ""}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="btn btn-sm btn-soft" disabled={cur === 0} onClick={() => setPage(cur - 1)}><Icon name="chevron-left" size={15} /></button>
            <span className="mono" style={{ fontSize: 13 }}>{cur + 1} / {pages}</span>
            <button className="btn btn-sm btn-soft" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}><Icon name="chevron-right" size={15} /></button>
          </div>
        </div>
      </div>
    </div>
  );
}
Object.assign(window, { Library });
