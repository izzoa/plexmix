"""Generator — the showpiece: idle hero → AI-thinking stage → results.

Bound entirely to the existing ``GeneratorState`` (no backend logic changes
beyond the additive cancel/view-state). The thinking visuals are driven by the
real ``generation_progress`` reported by ``generate_playlist``.
"""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.generator_state import GeneratorState


# Four phases, mapped to the real progress bands (0–10 / 10–40 / 40–70 / 70–100).
_PHASES = [
    ("Embed vibe", "brain"),
    ("Vector search", "search"),
    ("Diversity", "layers"),
    ("Order set", "audio-waveform"),
]

_ORDERINGS = [
    ("similarity", "Similarity"),
    ("random", "Random"),
    ("alternating_artists", "Alt. artists"),
    ("energy_curve", "Energy curve"),
]

# Canvas particle vector-field. A body-level observer manages the canvas
# lifecycle (start on mount, stop on unmount) so it survives Reflex re-renders;
# phase comes from the .viz-wrap[data-phase] attribute, theme from <html>.
_THINKVIZ_JS = """
(function(){
  if (window.__thinkviz__) return; window.__thinkviz__ = true;
  var raf=0, parts=[], W=0, H=0, ro=null, po=null, cv=null, ctx=null;
  function rand(a,b){return a+Math.random()*(b-a);}
  function phase(){var w=document.querySelector('.viz-wrap');return w?parseInt(w.getAttribute('data-phase')||'0',10):0;}
  function assign(){
    var ph=phase(), cx=W/2, cy=H/2;
    parts.forEach(function(p,i){
      if(ph===0){var a=rand(0,Math.PI*2),r=rand(0,46);p.tx=cx+Math.cos(a)*r;p.ty=cy+Math.sin(a)*r;p.hl=i<26;p.dim=0.5;}
      else if(ph===1){p.tx=rand(W*0.08,W*0.92);p.ty=rand(H*0.14,H*0.86);var d=Math.hypot(p.tx-cx,p.ty-cy);p.hl=d<Math.min(W,H)*0.34;p.dim=p.hl?1:0.28;}
      else if(ph===2){p.hl=i%6===0;p.dim=p.hl?1:0.08;}
      else {if(p.hl){var idx=Math.floor(i/6);p.tx=W*0.1+(idx/7)*W*0.8;p.ty=cy-Math.sin((idx/7)*Math.PI*1.6)*H*0.26;p.dim=1;}else{p.dim=0.05;}}
    });
  }
  function size(){
    if(!cv) return; var r=cv.getBoundingClientRect(); var dpr=Math.min(window.devicePixelRatio||1,2);
    cv.width=r.width*dpr; cv.height=r.height*dpr; ctx.setTransform(dpr,0,0,dpr,0,0); W=r.width; H=r.height;
    if(!parts.length){parts=Array.from({length:300},function(){return {x:rand(0,W),y:rand(0,H),tx:rand(0,W),ty:rand(0,H),hl:false,dim:0.4,size:rand(1.1,2.4)};});}
    assign();
  }
  function frame(){
    if(!cv||!document.body.contains(cv)){stop();return;}
    var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var dark=document.documentElement.classList.contains('dark');
    var base=dark?'120,120,135':'150,150,160';
    ctx.clearRect(0,0,W,H); ctx.lineWidth=1;
    for(var i=0;i<parts.length;i++){var p=parts[i];var k=reduce?1:0.08;p.x+=(p.tx-p.x)*k;p.y+=(p.ty-p.y)*k;
      if(p.hl){for(var j=i+1;j<parts.length;j++){var q=parts[j];if(!q.hl)continue;var d=Math.hypot(p.x-q.x,p.y-q.y);
        if(d<74){ctx.strokeStyle='rgba(249,115,22,'+(0.12*(1-d/74))+')';ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}}}}
    for(var m=0;m<parts.length;m++){var pp=parts[m];ctx.beginPath();ctx.arc(pp.x,pp.y,pp.hl?pp.size+0.7:pp.size,0,Math.PI*2);
      ctx.fillStyle=pp.hl?'rgba(249,115,22,'+pp.dim+')':'rgba('+base+','+pp.dim+')';ctx.fill();}
    raf=requestAnimationFrame(frame);
  }
  function start(c){
    cv=c; ctx=cv.getContext('2d'); parts=[]; size();
    if(ro) ro.disconnect(); ro=new ResizeObserver(size); ro.observe(cv);
    var w=document.querySelector('.viz-wrap');
    if(po) po.disconnect();
    if(w){po=new MutationObserver(assign); po.observe(w,{attributes:true,attributeFilter:['data-phase']});}
    cancelAnimationFrame(raf); frame();
  }
  function stop(){cancelAnimationFrame(raf);raf=0;if(ro){ro.disconnect();ro=null;}if(po){po.disconnect();po=null;}cv=null;ctx=null;parts=[];}
  var watch=new MutationObserver(function(){
    var c=document.getElementById('thinkviz');
    if(c && c!==cv){start(c);} else if(!c && cv){stop();}
  });
  watch.observe(document.body,{childList:true,subtree:true});
  var c0=document.getElementById('thinkviz'); if(c0) start(c0);
})();
"""

# Keep the streaming log scrolled to the newest line.
_GENLOG_JS = """
(function(){
  if (window.__genlog_obs__) return; window.__genlog_obs__ = true;
  var obs=new MutationObserver(function(){var g=document.querySelector('.genlog');if(g)g.scrollTop=g.scrollHeight;});
  obs.observe(document.body,{subtree:true,childList:true,characterData:true});
})();
"""


# ── Idle hero ─────────────────────────────────────────────────────────


def _adv_panel() -> rx.Component:
    return rx.box(
        rx.box(
            rx.box(
                "Tracks",
                rx.el.span(GeneratorState.max_tracks, class_name="val"),
                class_name="adv-label",
            ),
            rx.el.input(
                type="range",
                min="10",
                max="100",
                step="5",
                value=GeneratorState.max_tracks,
                on_change=GeneratorState.set_max_tracks,
            ),
        ),
        rx.box(
            rx.box(
                "Candidate pool ×",
                rx.el.span(GeneratorState.candidate_pool_multiplier, class_name="val"),
                class_name="adv-label",
            ),
            rx.el.input(
                type="range",
                min="1",
                max="100",
                step="1",
                value=GeneratorState.candidate_pool_multiplier,
                on_change=GeneratorState.set_candidate_pool_multiplier,
            ),
        ),
        rx.box(
            rx.box("Genre focus", class_name="adv-label"),
            rx.el.input(
                class_name="input",
                placeholder="Any genre",
                value=GeneratorState.genre_filter,
                on_change=GeneratorState.set_genre_filter,
            ),
        ),
        rx.box(
            rx.box("Track ordering", class_name="adv-label"),
            rx.box(
                *[
                    rx.el.button(
                        label,
                        class_name=rx.cond(GeneratorState.shuffle_mode == value, "on", ""),
                        on_click=GeneratorState.set_shuffle_mode(value),
                        type="button",
                    )
                    for value, label in _ORDERINGS
                ],
                class_name="seg",
            ),
            class_name="adv-full",
        ),
        class_name="adv-panel",
    )


def _prompt_box() -> rx.Component:
    return rx.box(
        rx.el.textarea(
            placeholder="rainy day melancholy with a little hope…",
            value=GeneratorState.mood_query,
            on_change=GeneratorState.set_mood_query,
            class_name="prompt-input",
            rows="2",
        ),
        rx.box(
            rx.el.button(
                rx.icon("sliders-horizontal", size=15),
                "Options",
                rx.cond(
                    GeneratorState.show_advanced,
                    rx.icon("chevron-up", size=14),
                    rx.icon("chevron-down", size=14),
                ),
                class_name=rx.cond(GeneratorState.show_advanced, "opt-btn on", "opt-btn"),
                on_click=GeneratorState.toggle_advanced,
                type="button",
            ),
            rx.el.span(GeneratorState.mood_query.length(), "/280", class_name="charcount"),
            rx.box(class_name="spacer"),
            rx.el.button(
                rx.icon("sparkles", size=16),
                "Generate",
                class_name="btn btn-3 btn-primary glow",
                on_click=GeneratorState.generate_playlist,
                disabled=GeneratorState.mood_query == "",
                type="button",
            ),
            class_name="prompt-foot",
        ),
        rx.cond(GeneratorState.show_advanced, _adv_panel(), rx.fragment()),
        class_name="prompt-box",
    )


def _idle_hero() -> rx.Component:
    return rx.box(
        rx.box(class_name="gen-hero-glow"),
        rx.box(
            rx.box(
                rx.icon("sparkles", size=14),
                "AI Playlist Generator",
                class_name="gen-kicker",
            ),
            rx.el.h1(
                "What should your ",
                rx.el.span("library", class_name="accent"),
                " sound like?",
                class_name="gen-title",
            ),
            rx.el.p(
                "Describe a mood, a moment, or a memory. PlexMix searches every track "
                "you own and curates a playlist that fits — just your music.",
                class_name="gen-sub",
            ),
            _prompt_box(),
            rx.cond(
                GeneratorState.generation_message != "",
                rx.box(
                    rx.box(rx.icon("circle-alert", size=16), class_name="c-ico"),
                    rx.box(GeneratorState.generation_message, class_name="c-body"),
                    class_name="callout callout-error",
                    style={"marginTop": "16px", "textAlign": "left"},
                ),
                rx.fragment(),
            ),
            rx.box(
                rx.foreach(
                    GeneratorState.mood_examples,
                    lambda v: rx.el.button(
                        v,
                        class_name="vibe-pill",
                        on_click=[
                            GeneratorState.set_mood_query(v),
                            GeneratorState.generate_playlist,
                        ],
                        type="button",
                    ),
                ),
                class_name="vibe-row",
            ),
            class_name="gen-hero-inner",
        ),
        class_name="gen-hero",
    )


# ── Thinking stage ────────────────────────────────────────────────────


def _phase_node(idx: int, label: str, icon: str) -> rx.Component:
    ap = GeneratorState.active_phase
    return rx.box(
        rx.box(
            rx.box(
                class_name="fill",
                style={"width": rx.cond(ap > idx, "100%", "0%")},
            ),
            class_name="phase-line",
        ),
        rx.box(
            rx.cond(ap > idx, rx.icon("check", size=16), rx.icon(icon, size=16)),
            class_name="phase-dot",
        ),
        rx.box(label, class_name="phase-label"),
        class_name=rx.cond(
            ap > idx,
            "phase-node done",
            rx.cond(ap == idx, "phase-node active", "phase-node"),
        ),
    )


def _thinking_stage() -> rx.Component:
    return rx.box(
        rx.box(
            rx.box(
                rx.box(
                    rx.icon("sparkles", size=15, color="var(--brand-9)"),
                    "Curating ",
                    rx.el.b('"', GeneratorState.mood_query, '"'),
                    class_name="think-query",
                ),
                class_name="think-head",
            ),
            rx.box(
                rx.el.canvas(id="thinkviz"),
                rx.box(GeneratorState.generation_message, class_name="viz-phase-label"),
                class_name="viz-wrap",
                custom_attrs={"data-phase": GeneratorState.active_phase.to_string()},
            ),
            rx.box(
                *[_phase_node(i, label, icon) for i, (label, icon) in enumerate(_PHASES)],
                class_name="phase-track",
            ),
            rx.box(
                rx.box(
                    rx.box(
                        class_name="pfill",
                        style={"width": GeneratorState.generation_progress.to_string() + "%"},
                    ),
                    class_name="pbar",
                ),
                rx.el.span(GeneratorState.generation_progress.to_string() + "%", class_name="pct"),
                rx.el.button(
                    rx.icon("x", size=14),
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=GeneratorState.cancel_generation,
                    type="button",
                ),
                class_name="gen-progress",
            ),
            rx.box(
                rx.foreach(
                    GeneratorState.generation_log,
                    lambda ln: rx.box(rx.el.span(ln), class_name="ln"),
                ),
                class_name="genlog",
            ),
            class_name="think-inner fade-up",
        ),
        class_name="think",
    )


# ── Results ───────────────────────────────────────────────────────────

_COVER_GRADIENTS = [
    "linear-gradient(135deg, #F97316, #EA580C)",
    "linear-gradient(135deg, #e94560, #f39c12)",
    "linear-gradient(135deg, #d68034, #5c2d0f)",
    "linear-gradient(135deg, #f3c9a8, #b55f18)",
]


def _trk_row(track: rx.Var, index: rx.Var) -> rx.Component:
    return rx.box(
        rx.el.span(index + 1, class_name="tnum"),
        rx.box(
            style={
                "width": "38px",
                "height": "38px",
                "borderRadius": "var(--radius-sm)",
                "background": "linear-gradient(135deg, var(--brand-7), var(--brand-10))",
                "flexShrink": "0",
            },
        ),
        rx.box(
            rx.box(track["title"], class_name="ttitle"),
            rx.box(track["artist"], class_name="tartist"),
            class_name="tinfo",
        ),
        rx.el.span(track["album"], class_name="talbum"),
        rx.el.span(track["duration_formatted"], class_name="tdur"),
        rx.el.button(
            rx.icon("x", size=15),
            class_name="icon-btn trm",
            title="Remove",
            on_click=GeneratorState.remove_track(track["id"]),
            type="button",
        ),
        class_name="trk",
    )


def _results_stage() -> rx.Component:
    return rx.box(
        rx.box(
            rx.box(
                *[rx.box(style={"background": g}) for g in _COVER_GRADIENTS],
                class_name="result-cover",
            ),
            rx.box(
                rx.box("Generated playlist", class_name="rk"),
                rx.el.h1(GeneratorState.mood_query),
                rx.box(
                    rx.el.span(
                        GeneratorState.generated_playlist.length(),
                        " tracks",
                        class_name="mono",
                    ),
                    " · ",
                    rx.el.span(GeneratorState.total_duration_label, class_name="mono"),
                    " · ordered by ",
                    rx.el.span(GeneratorState.ordering_label, class_name="badge badge-orange"),
                    class_name="result-stats",
                ),
                rx.box(
                    rx.el.button(
                        rx.icon("server", size=16),
                        "Save to Plex",
                        class_name="btn btn-3 btn-blue",
                        on_click=GeneratorState.save_to_plex,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("hard-drive", size=16),
                        "Save Locally",
                        class_name="btn btn-3 btn-green",
                        on_click=GeneratorState.save_locally,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("download", size=16),
                        "Export M3U",
                        class_name="btn btn-3 btn-soft",
                        on_click=GeneratorState.export_m3u,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("refresh-cw", size=16),
                        "Regenerate",
                        class_name="btn btn-3 btn-outline",
                        on_click=GeneratorState.regenerate,
                        type="button",
                    ),
                    class_name="result-actions",
                ),
                class_name="result-meta",
            ),
            class_name="result-head",
        ),
        rx.box(
            rx.foreach(GeneratorState.generated_playlist, _trk_row),
            class_name="tbl-wrap",
            style={"padding": "6px"},
        ),
        rx.box(
            rx.el.button(
                rx.icon("plus", size=16),
                "New playlist",
                class_name="btn btn-3 btn-ghost",
                on_click=GeneratorState.new_playlist,
                type="button",
            ),
            style={"marginTop": "18px", "textAlign": "center"},
        ),
        class_name="gen-results fade-up",
    )


# ── Page ──────────────────────────────────────────────────────────────


def generator() -> rx.Component:
    stage = rx.box(
        rx.script(_THINKVIZ_JS),
        rx.script(_GENLOG_JS),
        rx.cond(
            GeneratorState.is_generating,
            _thinking_stage(),
            rx.cond(
                GeneratorState.generated_playlist.length() > 0,
                _results_stage(),
                _idle_hero(),
            ),
        ),
        class_name="gen-stage",
    )
    return layout(stage, full_bleed=True)
