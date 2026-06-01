// PlexMix WebUI — Dashboard
function Dashboard({ setPage, onToast, runAction }) {
  const stats = [
    { lab: "Tracks", num: LIB.totalTracks.toLocaleString(), icon: "music", col: "var(--pm-info)", bg: "var(--info-bg)", delta: "+218 this week" },
    { lab: "Embedded", num: LIB.embedded.toLocaleString(), icon: "layers", col: "var(--pm-success)", bg: "var(--success-bg)", delta: "97.2% coverage" },
    { lab: "Tagged", num: LIB.tagged.toLocaleString(), icon: "tags", col: "var(--pm-purple)", bg: "var(--purple-bg)", delta: "688 untagged" },
    { lab: "Playlists", num: LIB.playlists, icon: "list-music", col: "var(--brand-9)", bg: "var(--brand-3)", delta: "6 this month" },
  ];
  const embPct = Math.round((LIB.embedded / LIB.totalTracks) * 100);
  const tagPct = Math.round((LIB.tagged / LIB.totalTracks) * 100);

  return (
    <div className="scroll"><div className="page">
      {/* connection status */}
      <div className="card statusbar fade-up section">
        <div className="item"><span className="dot dot-success" /><span className="lab">Plex</span><span className="val">connected</span></div>
        <div className="vsep" />
        <div className="item"><span className="dot dot-success" /><span className="lab">AI</span><span className="val">Gemini 2.0 Flash</span></div>
        <div className="vsep" />
        <div className="item"><span className="dot dot-success" /><span className="lab">Embeddings</span><span className="val">gemini · 3072d</span></div>
        <div className="vsep" />
        <div className="item"><Icon name="clock" size={14} color="var(--fg-3)" /><span className="lab">Last sync</span><span className="val mono" style={{ fontSize: 12.5 }}>{LIB.lastSync}</span></div>
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm btn-soft" onClick={() => runAction("sync")}><Icon name="refresh-cw" size={14} /> Sync</button>
      </div>

      {/* stat tiles */}
      <div className="stat-grid section">
        {stats.map((s, i) => (
          <div key={s.lab} className={"tile stat-tile hover-lift fade-up s" + (i + 1)}>
            <div className="ico" style={{ background: s.bg }}><Icon name={s.icon} size={20} color={s.col} /></div>
            <div>
              <div className="num">{s.num}</div>
              <div className="lab">{s.lab}</div>
              <div className="delta mono" style={{ color: "var(--fg-3)", marginTop: 8 }}>{s.delta}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: "var(--section-gap)", alignItems: "start" }}>
        {/* quick actions */}
        <div className="section">
          <div className="section-head"><h2>Quick Actions</h2></div>
          <div className="qa-grid">
            <div className="card qa primary hover-lift" onClick={() => setPage("generator")}>
              <div className="ico" style={{ background: "var(--brand-3)" }}><Icon name="sparkles" size={20} color="var(--brand-9)" /></div>
              <div><div className="qt">Generate Playlist</div><div className="qd">Describe a vibe, get a mix</div></div>
              <Icon name="arrow-right" size={18} className="arrow" />
            </div>
            <div className="card qa hover-lift" onClick={() => runAction("sync")}>
              <div className="ico" style={{ background: "var(--info-bg)" }}><Icon name="refresh-cw" size={20} color="var(--pm-info)" /></div>
              <div><div className="qt">Sync Library</div><div className="qd">Pull latest from Plex</div></div>
              <Icon name="arrow-right" size={18} className="arrow" />
            </div>
            <div className="card qa hover-lift" onClick={() => setPage("tagging")}>
              <div className="ico" style={{ background: "var(--purple-bg)" }}><Icon name="tags" size={20} color="var(--pm-purple)" /></div>
              <div><div className="qt">Tag Untagged</div><div className="qd">688 tracks need tags</div></div>
              <Icon name="arrow-right" size={18} className="arrow" />
            </div>
            <div className="card qa hover-lift" onClick={() => setPage("doctor")}>
              <div className="ico" style={{ background: "var(--success-bg)" }}><Icon name="stethoscope" size={20} color="var(--pm-success)" /></div>
              <div><div className="qt">Run Doctor</div><div className="qd">Check system health</div></div>
              <Icon name="arrow-right" size={18} className="arrow" />
            </div>
          </div>

          {/* coverage */}
          <div className="tile section" style={{ marginTop: 16 }}>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 15, marginBottom: 14 }}>Pipeline coverage</div>
            {[["Embeddings", embPct, "var(--pm-success)"], ["AI tags", tagPct, "var(--pm-purple)"]].map(([l, pct, c]) => (
              <div key={l} style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 13 }}>
                  <span className="fg2">{l}</span><span className="mono">{pct}%</span>
                </div>
                <div className="pbar"><div className="pfill" style={{ width: pct + "%", background: c }} /></div>
              </div>
            ))}
          </div>
        </div>

        {/* recent playlists */}
        <div className="section">
          <div className="section-head"><h2>Recent Playlists</h2><span className="more" onClick={() => setPage("history")}>View all</span></div>
          <div className="card" style={{ overflow: "hidden" }}>
            {PLAYLISTS.slice(0, 5).map((p, i) => (
              <div key={p.id} onClick={() => setPage("history")}
                   style={{ display: "flex", alignItems: "center", gap: 13, padding: "12px 16px", cursor: "pointer", borderBottom: i < 4 ? "1px solid var(--border-subtle)" : "none" }}
                   className="hover-row">
                <div style={{ width: 44, height: 44, borderRadius: 8, overflow: "hidden", display: "grid", gridTemplateColumns: "1fr 1fr", flexShrink: 0 }}>
                  {p.seeds.map((s, j) => <div key={j} style={{ background: artColors(s + j) }} />)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</div>
                  <div className="mono" style={{ fontSize: 12, color: "var(--fg-3)", marginTop: 2 }}>{p.count} tracks · {fmtTotal(p.dur)}</div>
                </div>
                <Icon name="chevron-right" size={16} color="var(--fg-3)" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div></div>
  );
}
Object.assign(window, { Dashboard });
