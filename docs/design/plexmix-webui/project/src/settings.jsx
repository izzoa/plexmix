// PlexMix WebUI — Settings
const { useState } = React;
function TestButton({ label, onToast, okMsg }) {
  const [state, setState] = useState("idle"); // idle | testing | ok
  function go() {
    setState("testing");
    setTimeout(() => { setState("ok"); onToast(okMsg, "success"); setTimeout(() => setState("idle"), 2400); }, 1300);
  }
  return (
    <button className={"btn btn-3 " + (state === "ok" ? "btn-green" : "btn-soft")} onClick={go} disabled={state === "testing"}>
      <Icon name={state === "testing" ? "loader" : state === "ok" ? "check" : "plug-zap"} size={16} className={state === "testing" ? "spin" : ""} />
      {state === "testing" ? "Testing…" : state === "ok" ? "Connected" : label}
    </button>
  );
}

function Field({ label, help, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <label className="field-label">{label}</label>
      {children}
      {help && <div className="field-help">{help}</div>}
    </div>
  );
}

function Settings({ onToast }) {
  const [tab, setTab] = useState("plex");
  const [aiProv, setAiProv] = useState("Google Gemini");
  const [embProv, setEmbProv] = useState("Google Gemini");
  const [temp, setTemp] = useState(0.7);
  const [aiModel, setAiModel] = useState(AI_MODELS["Google Gemini"][0]);
  const [embModel, setEmbModel] = useState(EMB_MODELS["Google Gemini"][0]);

  const TABS = [
    { id: "plex", label: "Plex", icon: "server" },
    { id: "ai", label: "AI Provider", icon: "brain" },
    { id: "emb", label: "Embeddings", icon: "layers" },
    { id: "adv", label: "Advanced", icon: "sliders-horizontal" },
  ];

  return (
    <div className="scroll"><div className="page" style={{ maxWidth: 760 }}>
      <div className="tabs section">
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}><Icon name={t.icon} size={15} /> {t.label}</button>
        ))}
      </div>

      <div className="card fade-up" style={{ padding: 26 }}>
        {tab === "plex" && (<>
          <Field label="Server URL" help="Your Plex Media Server address — local IP or plex.direct URL.">
            <input className="input" defaultValue="http://plex.local:32400" />
          </Field>
          <Field label="Plex token" help="Found under Account → Authorized Devices, or via the X-Plex-Token header.">
            <input className="input" type="password" defaultValue="••••••••••••••••••••" />
          </Field>
          <Field label="Music library">
            <select className="select"><option>Music</option><option>Vinyl Rips</option><option>Soundtracks</option></select>
          </Field>
          <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
            <TestButton label="Test Connection" onToast={onToast} okMsg="Plex connection successful · 12,480 tracks" />
            <button className="btn btn-3 btn-primary" onClick={() => onToast("Plex settings saved", "success")}>Save</button>
          </div>
        </>)}

        {tab === "ai" && (<>
          <Field label="Provider">
            <select className="select" value={aiProv} onChange={(e) => { setAiProv(e.target.value); setAiModel(AI_MODELS[e.target.value][0]); }}>
              {AI_PROVIDERS.map((p) => <option key={p}>{p}</option>)}
            </select>
          </Field>
          <Field label="API key" help="Stored securely in your system keyring — never written to disk in plaintext.">
            <input className="input" type="password" defaultValue="••••••••••••••••••••••••" />
          </Field>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
            <Field label="Model">
              <select className="select" value={aiModel} onChange={(e) => setAiModel(e.target.value)}>
                {AI_MODELS[aiProv].map((m) => <option key={m}>{m}</option>)}
              </select>
            </Field>
            <Field label={`Temperature · ${temp.toFixed(1)}`} help="Lower = focused, higher = adventurous.">
              <input type="range" min={0} max={1} step={0.1} value={temp} onChange={(e) => setTemp(+e.target.value)} style={{ marginTop: 14 }} />
            </Field>
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
            <TestButton label="Test Provider" onToast={onToast} okMsg="AI provider responded · model ready" />
            <button className="btn btn-3 btn-primary" onClick={() => onToast("AI settings saved", "success")}>Save</button>
          </div>
        </>)}

        {tab === "emb" && (<>
          <Field label="Provider">
            <select className="select" value={embProv} onChange={(e) => { setEmbProv(e.target.value); setEmbModel(EMB_MODELS[e.target.value][0]); }}>
              {EMB_PROVIDERS.map((p) => <option key={p}>{p}</option>)}
            </select>
          </Field>
          {!embProv.startsWith("Local") && (
            <Field label="API key" help="Can reuse the same key as your AI provider when they match.">
              <input className="input" type="password" defaultValue="••••••••••••••••••••••••" />
            </Field>
          )}
          <Field label="Model">
            <select className="select" value={embModel} onChange={(e) => setEmbModel(e.target.value)}>
              {EMB_MODELS[embProv].map((m) => <option key={m}>{m}</option>)}
            </select>
          </Field>
          <div className="callout callout-info" style={{ marginBottom: 18 }}>
            <span className="c-ico"><Icon name="info" size={16} /></span>
            <div className="c-body">Changing embedding dimensions requires re-embedding your whole library. Current index: <code>3072d</code>.</div>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <TestButton label="Test Embeddings" onToast={onToast} okMsg="Generated test embedding · 3072d" />
            <button className="btn btn-3 btn-primary" onClick={() => onToast("Embedding settings saved", "success")}>Save</button>
          </div>
        </>)}

        {tab === "adv" && (<>
          <Field label="Database path">
            <input className="input mono" style={{ fontSize: 13 }} readOnly defaultValue="~/.plexmix/plexmix.db" />
          </Field>
          <Field label="FAISS index path">
            <input className="input mono" style={{ fontSize: 13 }} readOnly defaultValue="~/.plexmix/index.faiss" />
          </Field>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
            <Field label="Sync batch size"><input className="input mono" defaultValue="500" /></Field>
            <Field label="Embedding batch size"><input className="input mono" defaultValue="100" /></Field>
          </div>
          <Field label="Log level">
            <select className="select"><option>INFO</option><option>DEBUG</option><option>WARNING</option><option>ERROR</option></select>
          </Field>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn-3 btn-primary" onClick={() => onToast("Advanced settings saved", "success")}>Save</button>
            <button className="btn btn-3 btn-outline" onClick={() => onToast("Rebuilding FAISS index…", "info")}><Icon name="refresh-cw" size={15} /> Rebuild index</button>
          </div>
        </>)}
      </div>
    </div></div>
  );
}
Object.assign(window, { Settings, TestButton, Field });
