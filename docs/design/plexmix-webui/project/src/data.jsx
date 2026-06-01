// PlexMix WebUI — sample data + helpers (original, believable content)
const { useState, useEffect, useRef, useCallback, useMemo } = React;

// ---- helpers ----------------------------------------------------
function fmtDur(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + ":" + String(s).padStart(2, "0");
}
function fmtTotal(sec) {
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h > 0 ? `${h} hr ${m} min` : `${m} min`;
}
// deterministic warm-leaning gradient for an album-art swatch
function artColors(seed) {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 100000;
  const palettes = [
    ["#F97316", "#EA580C"], ["#fb923c", "#c2410c"], ["#f59e0b", "#d97706"],
    ["#e94560", "#b91c1c"], ["#A855F7", "#7c3aed"], ["#3B82F6", "#1d4ed8"],
    ["#22C55E", "#15803d"], ["#0ea5e9", "#0369a1"], ["#f43f5e", "#9f1239"],
    ["#84cc16", "#4d7c0f"], ["#14b8a6", "#0f766e"], ["#eab308", "#a16207"],
  ];
  const p = palettes[h % palettes.length];
  const ang = 100 + (h % 80);
  return `linear-gradient(${ang}deg, ${p[0]}, ${p[1]})`;
}
function Art({ seed, size = 40, radius = 6 }) {
  return (
    <div className="art" style={{ width: size, height: size, borderRadius: radius, background: artColors(seed) }}>
      <div className="gloss" />
    </div>
  );
}

// ---- example vibes ----------------------------------------------
const VIBES = [
  "rainy day melancholy", "high energy dance floor", "90s nostalgia road trip",
  "chill study session", "late night coding focus", "sunday morning coffee",
  "golden hour drive", "heartbreak at 2am", "warehouse warmup",
];

// ---- track pool (original names) --------------------------------
const TRACKS = [
  { t: "Paper Lanterns", a: "Halcyon Drift", al: "Slow Tide", g: "Indie", y: 2021, d: 224, tags: "dreamy,nostalgic,warm", env: "bedroom,rainy", inst: "guitar,synth", emb: true },
  { t: "Neon Cartography", a: "Vela Mono", al: "Citylight", g: "Electronic", y: 2019, d: 312, tags: "energetic,nocturnal,pulsing", env: "club,city", inst: "synth,drum machine", emb: true },
  { t: "Cinder & Smoke", a: "The Lowfields", al: "Ember Roads", g: "Folk", y: 2017, d: 268, tags: "wistful,acoustic,intimate", env: "campfire,forest", inst: "guitar,banjo", emb: true },
  { t: "Glasshouse", a: "Marrow Sun", al: "Greenhouse", g: "Indie", y: 2022, d: 198, tags: "bright,hopeful,airy", env: "morning,garden", inst: "piano,vocals", emb: true },
  { t: "Undertow", a: "Cassette Ghosts", al: "Static Bloom", g: "Shoegaze", y: 2020, d: 287, tags: "hazy,reverb,melancholy", env: "rainy,coastal", inst: "guitar,bass", emb: true },
  { t: "Midnight Arithmetic", a: "Vela Mono", al: "Citylight", g: "Electronic", y: 2019, d: 256, tags: "focus,driving,minimal", env: "late night,studio", inst: "synth,arp", emb: true },
  { t: "Saltwater Hymn", a: "Halcyon Drift", al: "Slow Tide", g: "Indie", y: 2021, d: 241, tags: "calm,oceanic,tender", env: "beach,dusk", inst: "guitar,strings", emb: true },
  { t: "Brass Knuckle Sunrise", a: "The Pocket Kings", al: "Daybreak Soul", g: "Funk", y: 2018, d: 233, tags: "groovy,upbeat,warm", env: "party,summer", inst: "bass,horns", emb: true },
  { t: "Quietest Room", a: "Aoife Lin", al: "Hush", g: "Jazz", y: 2016, d: 305, tags: "smooth,late,intimate", env: "lounge,night", inst: "piano,sax", emb: true },
  { t: "Static on the Line", a: "Cassette Ghosts", al: "Static Bloom", g: "Shoegaze", y: 2020, d: 262, tags: "dense,nostalgic,fuzzy", env: "rainy,bedroom", inst: "guitar,drums", emb: false },
  { t: "Gold Leaf", a: "Marrow Sun", al: "Greenhouse", g: "Indie", y: 2022, d: 215, tags: "warm,sunny,gentle", env: "morning,park", inst: "guitar,vocals", emb: true },
  { t: "Concrete Garden", a: "Vela Mono", al: "Citylight", g: "Electronic", y: 2019, d: 344, tags: "pulsing,nocturnal,driving", env: "city,club", inst: "synth,bass", emb: true },
  { t: "Featherweight", a: "Aoife Lin", al: "Hush", g: "Jazz", y: 2016, d: 278, tags: "soft,reflective,warm", env: "rainy,evening", inst: "piano,upright bass", emb: true },
  { t: "Carousel", a: "The Lowfields", al: "Ember Roads", g: "Folk", y: 2017, d: 252, tags: "bittersweet,acoustic,storytelling", env: "porch,autumn", inst: "guitar,fiddle", emb: false },
  { t: "Velvet Static", a: "Nora Vex", al: "Afterglow", g: "Synthpop", y: 2023, d: 226, tags: "shimmering,danceable,romantic", env: "night drive,city", inst: "synth,drum machine", emb: true },
  { t: "Tin Roof Rhythm", a: "The Pocket Kings", al: "Daybreak Soul", g: "Funk", y: 2018, d: 247, tags: "groovy,joyful,loose", env: "summer,backyard", inst: "guitar,bass,horns", emb: true },
  { t: "Low Tide Lullaby", a: "Halcyon Drift", al: "Slow Tide", g: "Indie", y: 2021, d: 289, tags: "sleepy,tender,oceanic", env: "beach,night", inst: "guitar,synth", emb: true },
  { t: "Afterglow", a: "Nora Vex", al: "Afterglow", g: "Synthpop", y: 2023, d: 211, tags: "euphoric,bright,danceable", env: "club,sunset", inst: "synth,vocals", emb: true },
  { t: "Graphite Skies", a: "Marrow Sun", al: "Greenhouse", g: "Indie", y: 2022, d: 234, tags: "moody,overcast,reflective", env: "rainy,city", inst: "piano,guitar", emb: false },
  { t: "Slow Burn", a: "Aoife Lin", al: "Hush", g: "Jazz", y: 2016, d: 331, tags: "sultry,late,smooth", env: "lounge,midnight", inst: "sax,piano", emb: true },
  { t: "Pulse Width", a: "Vela Mono", al: "Citylight", g: "Electronic", y: 2019, d: 298, tags: "hypnotic,minimal,focus", env: "studio,late night", inst: "synth,arp", emb: true },
  { t: "Wildflower Static", a: "Cassette Ghosts", al: "Static Bloom", g: "Shoegaze", y: 2020, d: 271, tags: "dreamy,fuzzy,warm", env: "summer,field", inst: "guitar,bass", emb: true },
  { t: "Brass & Embers", a: "The Pocket Kings", al: "Daybreak Soul", g: "Funk", y: 2018, d: 219, tags: "warm,celebratory,groovy", env: "party,evening", inst: "horns,bass", emb: true },
  { t: "Northern Line", a: "Nora Vex", al: "Afterglow", g: "Synthpop", y: 2023, d: 243, tags: "propulsive,nocturnal,bright", env: "train,city", inst: "synth,drum machine", emb: false },
];

// ---- recent / saved playlists -----------------------------------
const PLAYLISTS = [
  { id: 1, name: "Rainy Day Melancholy", mood: "rainy day melancholy", count: 28, dur: 6840, when: "2026-05-28 19:42", seeds: ["Slow Tide", "Hush", "Static Bloom", "Greenhouse"] },
  { id: 2, name: "Warehouse Warmup", mood: "high energy dance floor", count: 42, dur: 11520, when: "2026-05-27 22:10", seeds: ["Citylight", "Afterglow", "Daybreak Soul", "Slow Tide"] },
  { id: 3, name: "Sunday Coffee", mood: "sunday morning coffee", count: 22, dur: 5280, when: "2026-05-26 09:15", seeds: ["Greenhouse", "Hush", "Ember Roads", "Slow Tide"] },
  { id: 4, name: "Late Night Coding", mood: "late night coding focus", count: 35, dur: 9300, when: "2026-05-24 01:33", seeds: ["Citylight", "Static Bloom", "Afterglow", "Hush"] },
  { id: 5, name: "Golden Hour Drive", mood: "golden hour drive", count: 31, dur: 7920, when: "2026-05-21 18:05", seeds: ["Afterglow", "Daybreak Soul", "Slow Tide", "Citylight"] },
  { id: 6, name: "90s Nostalgia", mood: "90s nostalgia road trip", count: 26, dur: 6480, when: "2026-05-19 14:48", seeds: ["Ember Roads", "Static Bloom", "Greenhouse", "Hush"] },
];

const GENRES = ["Indie", "Electronic", "Folk", "Shoegaze", "Funk", "Jazz", "Synthpop", "Ambient", "Hip-Hop", "Soul"];

const AI_PROVIDERS = ["Google Gemini", "OpenAI", "Anthropic Claude", "Cohere"];
const AI_MODELS = {
  "Google Gemini": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-flash"],
  "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
  "Anthropic Claude": ["claude-3.7-sonnet", "claude-3.5-haiku"],
  "Cohere": ["command-r-plus", "command-r"],
};
const EMB_PROVIDERS = ["Google Gemini", "OpenAI", "Cohere", "Local (sentence-transformers)"];
const EMB_MODELS = {
  "Google Gemini": ["gemini-embedding-001 · 3072d"],
  "OpenAI": ["text-embedding-3-large · 3072d", "text-embedding-3-small · 1536d"],
  "Cohere": ["embed-english-v3.0 · 1024d"],
  "Local (sentence-transformers)": ["all-MiniLM-L6-v2 · 384d"],
};

// library-wide figures used across pages
const LIB = {
  totalTracks: 12480,
  embedded: 12136,
  tagged: 11792,
  artists: 1247,
  albums: 2038,
  playlists: 18,
  lastSync: "2026-05-29 08:14",
  dbSize: "184 MB",
  indexSize: "146 MB",
};

Object.assign(window, {
  fmtDur, fmtTotal, artColors, Art,
  VIBES, TRACKS, PLAYLISTS, GENRES, LIB,
  AI_PROVIDERS, AI_MODELS, EMB_PROVIDERS, EMB_MODELS,
});
