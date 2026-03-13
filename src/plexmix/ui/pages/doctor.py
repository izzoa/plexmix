import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.doctor_state import DoctorState


# ── Stat tile (dashboard-style) ──────────────────────────────────────

def _stat_tile(
    label: str,
    value,
    icon_name: str,
    icon_color: str = "accent.9",
    icon_bg: str = "accent.3",
    stagger: str = "",
) -> rx.Component:
    """Single metric tile -- large mono number, muted label, tinted icon."""
    return rx.hstack(
        rx.box(
            rx.icon(icon_name, size=20, color=icon_color),
            padding="8px",
            border_radius="var(--radius-md)",
            background_color=icon_bg,
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(
                value,
                size="6",
                weight="bold",
                style={"fontFamily": "var(--font-mono)"},
            ),
            rx.text(label, size="1", color="gray.9", weight="medium"),
            spacing="0",
            align="start",
        ),
        spacing="3",
        align="center",
        class_name=f"animate-fade-in-up {stagger}".strip(),
    )


# ── Health status banner ─────────────────────────────────────────────

def _health_banner() -> rx.Component:
    """Slim banner at the top: green if healthy, amber if issues found."""
    return rx.cond(
        DoctorState.check_message != "",
        rx.cond(
            DoctorState.is_checking,
            rx.hstack(
                rx.spinner(size="2"),
                rx.text(DoctorState.check_message, size="2", color="gray.11"),
                rx.spacer(),
                spacing="2",
                align="center",
                padding_x="16px",
                padding_y="10px",
                border_radius="var(--radius-md)",
                background_color="gray.3",
                width="100%",
                class_name="animate-fade-in-up",
            ),
            rx.cond(
                DoctorState.is_healthy,
                # ── Healthy banner (green) ──
                rx.hstack(
                    rx.icon("circle-check", size=16, color="green.9"),
                    rx.text("All systems healthy", size="2", weight="medium", color="green.11"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("refresh-cw", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="green",
                        on_click=DoctorState.run_health_check,
                        loading=DoctorState.is_checking,
                    ),
                    spacing="2",
                    align="center",
                    padding_x="16px",
                    padding_y="10px",
                    border_radius="var(--radius-md)",
                    background_color="green.3",
                    width="100%",
                    class_name="animate-fade-in-up",
                ),
                # ── Unhealthy banner (amber) ──
                rx.hstack(
                    rx.icon("triangle-alert", size=16, color="yellow.9"),
                    rx.text("Issues found", size="2", weight="medium", color="yellow.11"),
                    rx.spacer(),
                    rx.icon_button(
                        rx.icon("refresh-cw", size=14),
                        size="1",
                        variant="ghost",
                        color_scheme="orange",
                        on_click=DoctorState.run_health_check,
                        loading=DoctorState.is_checking,
                    ),
                    spacing="2",
                    align="center",
                    padding_x="16px",
                    padding_y="10px",
                    border_radius="var(--radius-md)",
                    background_color="yellow.3",
                    width="100%",
                    class_name="animate-fade-in-up",
                ),
            ),
        ),
        rx.fragment(),
    )


# ── Issue item card ──────────────────────────────────────────────────

def _progress_section() -> rx.Component:
    """Shared inline progress bar used by embedding / tag / audio jobs."""
    return rx.vstack(
        rx.progress(
            value=rx.cond(
                DoctorState.fix_total > 0,
                (DoctorState.fix_progress / DoctorState.fix_total) * 100,
                0,
            ),
            max=100,
        ),
        rx.text(
            DoctorState.fix_progress_label,
            size="2",
            color="gray.9",
        ),
        spacing="2",
        width="100%",
    )


def _orphaned_embeddings_item() -> rx.Component:
    """Orphaned embeddings issue card."""
    return rx.cond(
        DoctorState.doctor_orphaned_embeddings > 0,
        rx.card(
            rx.hstack(
                rx.icon("triangle-alert", size=18, color="yellow.9", flex_shrink="0"),
                rx.vstack(
                    rx.text(
                        DoctorState.orphaned_embeddings_label,
                        size="3",
                        weight="bold",
                    ),
                    rx.text(
                        "Embeddings for tracks that no longer exist in your library.",
                        size="2",
                        color="gray.9",
                    ),
                    spacing="1",
                    align="start",
                    flex="1",
                ),
                rx.button(
                    "Delete",
                    size="2",
                    color_scheme="red",
                    variant="soft",
                    on_click=DoctorState.delete_orphaned_embeddings,
                    loading=DoctorState.current_fix_target == "cleanup",
                    disabled=DoctorState.is_fixing,
                    title=rx.cond(
                        DoctorState.is_fixing,
                        "Another operation is in progress",
                        "",
                    ),
                    flex_shrink="0",
                ),
                spacing="4",
                align="center",
                width="100%",
            ),
            width="100%",
        ),
        rx.fragment(),
    )


def _missing_embeddings_item() -> rx.Component:
    """Missing embeddings issue card."""
    return rx.cond(
        DoctorState.doctor_tracks_needing_embeddings > 0,
        rx.card(
            rx.vstack(
                rx.hstack(
                    rx.icon("triangle-alert", size=18, color="yellow.9", flex_shrink="0"),
                    rx.vstack(
                        rx.text(
                            DoctorState.missing_embeddings_label,
                            size="3",
                            weight="bold",
                        ),
                        rx.text(
                            "These tracks can't be used for playlist generation without embeddings.",
                            size="2",
                            color="gray.9",
                        ),
                        spacing="1",
                        align="start",
                        flex="1",
                    ),
                    rx.hstack(
                        rx.button(
                            "Generate",
                            size="2",
                            color_scheme="blue",
                            on_click=DoctorState.generate_missing_embeddings,
                            loading=DoctorState.incremental_embedding_running,
                            disabled=DoctorState.is_fixing,
                            title=rx.cond(
                                DoctorState.is_fixing,
                                "Another operation is in progress",
                                "",
                            ),
                        ),
                        rx.button(
                            "Rebuild All",
                            size="2",
                            color_scheme="red",
                            variant="surface",
                            on_click=DoctorState.regenerate_all_embeddings,
                            loading=DoctorState.full_embedding_running,
                            disabled=DoctorState.is_fixing,
                            title=rx.cond(
                                DoctorState.is_fixing,
                                "Another operation is in progress",
                                "",
                            ),
                        ),
                        spacing="2",
                        flex_shrink="0",
                    ),
                    spacing="4",
                    align="center",
                    width="100%",
                ),
                rx.text(
                    "Full rebuild deletes every stored embedding and recreates the FAISS index. "
                    "Use this when switching providers or troubleshooting.",
                    size="1",
                    color="gray.9",
                ),
                rx.cond(
                    DoctorState.embedding_job_running,
                    _progress_section(),
                    rx.fragment(),
                ),
                spacing="3",
                width="100%",
            ),
            width="100%",
        ),
        rx.fragment(),
    )


def _tag_maintenance_item() -> rx.Component:
    """Tag regeneration issue card."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("info", size=18, color="blue.9", flex_shrink="0"),
                rx.vstack(
                    rx.text("Tag Maintenance", size="3", weight="bold"),
                    rx.text(
                        DoctorState.untagged_tracks_message,
                        size="2",
                        color="gray.9",
                    ),
                    spacing="1",
                    align="start",
                    flex="1",
                ),
                rx.button(
                    "Regenerate",
                    size="2",
                    color_scheme="blue",
                    variant="soft",
                    on_click=DoctorState.regenerate_missing_tags,
                    loading=DoctorState.tag_job_running,
                    disabled=DoctorState.is_fixing | (DoctorState.doctor_untagged_tracks == 0),
                    title=rx.cond(
                        DoctorState.is_fixing,
                        "Another operation is in progress",
                        rx.cond(
                            DoctorState.doctor_untagged_tracks == 0,
                            "No untagged tracks",
                            "",
                        ),
                    ),
                    flex_shrink="0",
                ),
                spacing="4",
                align="center",
                width="100%",
            ),
            rx.cond(
                DoctorState.tag_job_running,
                _progress_section(),
                rx.fragment(),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _audio_analysis_item() -> rx.Component:
    """Audio analysis issue card."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("audio-waveform", size=18, color="blue.9", flex_shrink="0"),
                rx.vstack(
                    rx.text("Audio Analysis", size="3", weight="bold"),
                    rx.cond(
                        DoctorState.doctor_tracks_without_audio > 0,
                        rx.text(
                            DoctorState.missing_audio_label,
                            size="2",
                            color="yellow.11",
                        ),
                        rx.text(
                            "All eligible tracks have been analyzed.",
                            size="2",
                            color="green.11",
                        ),
                    ),
                    spacing="1",
                    align="start",
                    flex="1",
                ),
                rx.button(
                    "Analyze",
                    size="2",
                    color_scheme="blue",
                    variant="soft",
                    on_click=DoctorState.analyze_missing_audio,
                    loading=DoctorState.audio_job_running,
                    disabled=DoctorState.is_fixing,
                    title=rx.cond(
                        DoctorState.is_fixing,
                        "Another operation is in progress",
                        "",
                    ),
                    flex_shrink="0",
                ),
                spacing="4",
                align="center",
                width="100%",
            ),
            rx.cond(
                DoctorState.audio_job_running,
                _progress_section(),
                rx.fragment(),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


# ══════════════════════════════════════════════════════════════════════
#  Doctor Page
# ══════════════════════════════════════════════════════════════════════

def doctor() -> rx.Component:
    content = rx.vstack(
        # ── Page header ───────────────────────────────────────────
        rx.vstack(
            rx.heading("Database Doctor", size="8"),
            rx.text(
                "Check database health and fix common issues",
                size="3",
                color="gray.9",
            ),
            spacing="1",
            align="start",
        ),

        # ── Health status banner ──────────────────────────────────
        _health_banner(),

        # ── Stats row ─────────────────────────────────────────────
        rx.grid(
            _stat_tile(
                "Total Tracks",
                DoctorState.doctor_total_tracks,
                "music",
                icon_color="accent.9",
                icon_bg="accent.3",
                stagger="stagger-1",
            ),
            _stat_tile(
                "With Embeddings",
                DoctorState.doctor_tracks_with_embeddings,
                "cpu",
                icon_color="blue.9",
                icon_bg="blue.3",
                stagger="stagger-2",
            ),
            _stat_tile(
                "Untagged",
                DoctorState.doctor_untagged_tracks,
                "tag",
                icon_color="yellow.9",
                icon_bg="yellow.3",
                stagger="stagger-3",
            ),
            _stat_tile(
                "Audio Analyzed",
                DoctorState.doctor_audio_features_count,
                "audio-waveform",
                icon_color="green.9",
                icon_bg="green.3",
                stagger="stagger-4",
            ),
            columns=rx.breakpoints(initial="2", md="4"),
            spacing="6",
            width="100%",
        ),

        # ── Divider ──────────────────────────────────────────────
        rx.separator(size="4", color_scheme="gray"),

        # ── Issues & Maintenance ─────────────────────────────────
        rx.vstack(
            rx.text("Issues & Maintenance", size="4", weight="bold"),
            _orphaned_embeddings_item(),
            _missing_embeddings_item(),
            _tag_maintenance_item(),
            _audio_analysis_item(),
            spacing="4",
            width="100%",
            class_name="animate-fade-in-up stagger-3",
        ),

        spacing="6",
        width="100%",
    )
    return layout(content)
