import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.stat_tile import stat_tile as _stat_tile
from plexmix.ui.states.doctor_state import DoctorState


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


def _retag_confirm_modal() -> rx.Component:
    """Confirmation modal for regenerating all tags."""
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Regenerate All Tags?"),
            rx.alert_dialog.description(
                "This will regenerate AI tags for all tracks in your library, "
                "overwriting any existing tags. This may take a while and will "
                "use your configured AI provider.",
                size="2",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button(
                        "Cancel",
                        variant="soft",
                        color_scheme="gray",
                    ),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        "Regenerate All",
                        color_scheme="red",
                        on_click=DoctorState.confirm_retag,
                    ),
                ),
                spacing="3",
                justify="end",
                width="100%",
            ),
            max_width="450px",
        ),
        open=DoctorState.show_retag_confirm,
        on_open_change=lambda open: rx.cond(
            open,
            DoctorState.open_retag_confirm,
            DoctorState.close_retag_confirm,
        ),
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
                rx.cond(
                    DoctorState.doctor_untagged_tracks > 0,
                    # Untagged tracks exist — generate missing (no confirmation needed)
                    rx.button(
                        "Generate",
                        size="2",
                        color_scheme="blue",
                        variant="soft",
                        on_click=DoctorState.regenerate_missing_tags,
                        loading=DoctorState.tag_job_running,
                        disabled=DoctorState.is_fixing,
                        title=rx.cond(
                            DoctorState.is_fixing,
                            "Another operation is in progress",
                            "",
                        ),
                        flex_shrink="0",
                    ),
                    # All tagged — offer regenerate all (with confirmation)
                    rx.button(
                        "Regenerate All",
                        size="2",
                        color_scheme="blue",
                        variant="soft",
                        on_click=DoctorState.open_retag_confirm,
                        loading=DoctorState.tag_job_running,
                        disabled=DoctorState.is_fixing,
                        title=rx.cond(
                            DoctorState.is_fixing,
                            "Another operation is in progress",
                            "Regenerate tags for all tracks",
                        ),
                        flex_shrink="0",
                    ),
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


def _musicbrainz_item() -> rx.Component:
    """MusicBrainz enrichment issue card."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("disc", size=18, color="purple.9", flex_shrink="0"),
                rx.vstack(
                    rx.text("MusicBrainz Enrichment", size="3", weight="bold"),
                    rx.cond(
                        DoctorState.doctor_tracks_without_musicbrainz > 0,
                        rx.text(
                            DoctorState.missing_musicbrainz_label,
                            size="2",
                            color="yellow.11",
                        ),
                        rx.text(
                            "All tracks have been enriched with MusicBrainz metadata.",
                            size="2",
                            color="green.11",
                        ),
                    ),
                    spacing="1",
                    align="start",
                    flex="1",
                ),
                rx.button(
                    "Enrich",
                    size="2",
                    color_scheme="purple",
                    variant="soft",
                    on_click=DoctorState.enrich_missing_musicbrainz,
                    loading=DoctorState.musicbrainz_job_running,
                    disabled=DoctorState.is_fixing
                    | (DoctorState.doctor_tracks_without_musicbrainz == 0),
                    title=rx.cond(
                        DoctorState.is_fixing,
                        "Another operation is in progress",
                        rx.cond(
                            DoctorState.doctor_tracks_without_musicbrainz == 0,
                            "All tracks enriched",
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
                DoctorState.musicbrainz_job_running,
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
            _stat_tile(
                "MB Enriched",
                DoctorState.doctor_musicbrainz_enriched,
                "disc",
                icon_color="purple.9",
                icon_bg="purple.3",
                stagger="stagger-5",
            ),
            columns=rx.breakpoints(initial="2", md="5"),
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
            _musicbrainz_item(),
            _audio_analysis_item(),
            spacing="4",
            width="100%",
            class_name="animate-fade-in-up stagger-3",
        ),
        spacing="6",
        width="100%",
    )
    return layout(rx.fragment(content, _retag_confirm_modal(), _task_polling_trigger()))


def _task_polling_trigger() -> rx.Component:
    """Hidden button + JS interval for client-initiated TaskStore polling."""
    return rx.fragment(
        rx.el.button(
            id="plexmix-poll-doc",
            on_click=DoctorState.poll_task_progress,
            display="none",
        ),
        rx.cond(
            DoctorState.is_fixing,
            rx.script(
                "if (!window._pm_poll_doc) {"
                "  window._pm_poll_doc = setInterval(function() {"
                "    var b = document.getElementById('plexmix-poll-doc');"
                "    if (b) b.click();"
                "  }, 1500);"
                "}"
            ),
            rx.script(
                "if (window._pm_poll_doc) {"
                "  clearInterval(window._pm_poll_doc);"
                "  window._pm_poll_doc = null;"
                "}"
            ),
        ),
    )
