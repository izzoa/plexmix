"""Doctor — diagnostics & system health as a list of check rows with fixes."""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.doctor_state import DoctorState


def _check_row(
    icon: str,
    name: str,
    detail,
    ok,
    action_label: str,
    action_event,
    running,
) -> rx.Component:
    return rx.box(
        rx.box(
            rx.cond(
                ok,
                rx.icon("circle-check", size=20, color="var(--pm-success)"),
                rx.icon(icon, size=20, color="var(--brand-9)"),
            ),
            class_name="ico",
            style={"background": rx.cond(ok, "var(--success-bg)", "var(--brand-3)")},
        ),
        rx.box(
            rx.box(name, class_name="cname"),
            rx.box(detail, class_name="cdetail"),
            class_name="cinfo",
        ),
        rx.cond(
            ok,
            rx.box(rx.text("Healthy"), class_name="badge badge-green"),
            rx.el.button(
                action_label,
                class_name="btn btn-sm btn-soft",
                on_click=action_event,
                disabled=running,
                type="button",
            ),
        ),
        class_name="check",
    )


def doctor() -> rx.Component:
    content = rx.vstack(
        # Health hero
        rx.box(
            rx.box(
                rx.cond(
                    DoctorState.is_healthy,
                    rx.icon("shield-check", size=26, color="var(--pm-success)"),
                    rx.icon("stethoscope", size=26, color="var(--brand-9)"),
                ),
                class_name="ico",
                style={
                    "background": rx.cond(
                        DoctorState.is_healthy, "var(--success-bg)", "var(--brand-3)"
                    ),
                    "width": "52px",
                    "height": "52px",
                    "borderRadius": "var(--radius-lg)",
                },
            ),
            rx.box(
                rx.box(
                    rx.cond(DoctorState.is_healthy, "All systems healthy", "Attention needed"),
                    style={
                        "fontSize": "18px",
                        "fontWeight": "700",
                        "fontFamily": "var(--font-display)",
                    },
                ),
                rx.box(
                    rx.cond(
                        DoctorState.check_message != "",
                        DoctorState.check_message,
                        "Run a health check to scan your library.",
                    ),
                    class_name="fg3",
                    style={"fontSize": "13px", "marginTop": "2px"},
                ),
                style={"flex": "1"},
            ),
            rx.el.button(
                rx.icon("refresh-cw", size=16),
                "Run check",
                class_name="btn btn-3 btn-primary",
                on_click=DoctorState.run_health_check,
                disabled=DoctorState.is_checking,
                type="button",
            ),
            class_name="card",
            style={
                "display": "flex",
                "alignItems": "center",
                "gap": "16px",
                "padding": "18px 20px",
                "width": "100%",
            },
        ),
        # Fix-in-progress callout
        rx.cond(
            DoctorState.is_fixing,
            rx.box(
                rx.box(rx.icon("loader", size=16, class_name="spin"), class_name="c-ico"),
                rx.box(DoctorState.fix_progress_label, class_name="c-body", style={"flex": "1"}),
                rx.el.button(
                    rx.icon("x", size=14),
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=AppState.request_cancel("doctor_fix"),
                    type="button",
                ),
                class_name="callout callout-info",
                style={"width": "100%", "alignItems": "center"},
            ),
            rx.fragment(),
        ),
        # Diagnostic checks
        rx.box(
            _check_row(
                "cpu",
                "Embeddings",
                DoctorState.missing_embeddings_label,
                DoctorState.doctor_tracks_needing_embeddings == 0,
                "Generate",
                DoctorState.generate_missing_embeddings,
                DoctorState.embedding_job_running,
            ),
            _check_row(
                "unlink",
                "Orphaned embeddings",
                DoctorState.orphaned_embeddings_label,
                DoctorState.doctor_orphaned_embeddings == 0,
                "Clean up",
                DoctorState.delete_orphaned_embeddings,
                DoctorState.embedding_job_running,
            ),
            _check_row(
                "tags",
                "Tags",
                DoctorState.untagged_tracks_message,
                DoctorState.doctor_untagged_tracks == 0,
                "Tag",
                DoctorState.regenerate_missing_tags,
                DoctorState.tag_job_running,
            ),
            _check_row(
                "audio-waveform",
                "Audio features",
                DoctorState.missing_audio_label,
                DoctorState.doctor_tracks_without_audio == 0,
                "Analyze",
                DoctorState.analyze_missing_audio,
                DoctorState.audio_job_running,
            ),
            _check_row(
                "disc",
                "MusicBrainz enrichment",
                DoctorState.missing_musicbrainz_label,
                DoctorState.doctor_tracks_without_musicbrainz == 0,
                "Enrich",
                DoctorState.enrich_missing_musicbrainz,
                DoctorState.musicbrainz_job_running,
            ),
            class_name="card",
            style={"width": "100%", "overflow": "hidden"},
        ),
        spacing="4",
        width="100%",
        on_mount=DoctorState.on_load,
    )
    return layout(content)
