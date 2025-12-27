import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.doctor_state import DoctorState


def stat_card(label: str, value) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(label, size="2", color_scheme="gray"),
            rx.heading(value, size="6"),
            spacing="2",
            align="start",
        ),
        width="100%",
    )


def doctor() -> rx.Component:
    content = rx.container(
        rx.vstack(
            rx.heading("Database Doctor", size="8", margin_bottom="2"),
            rx.text(
                "Check database health and fix common issues",
                size="3",
                color_scheme="gray",
                margin_bottom="6"
            ),
            
            # Health Check Status
            rx.card(
                rx.vstack(
                    rx.hstack(
                        rx.heading("Health Check Status", size="5"),
                        rx.button(
                            "Re-check",
                            size="2",
                            variant="soft",
                            on_click=DoctorState.run_health_check,
                            loading=DoctorState.is_checking,
                        ),
                        justify="between",
                        width="100%",
                    ),
                    rx.cond(
                        DoctorState.is_checking,
                        rx.hstack(
                            rx.spinner(size="3"),
                            rx.text(DoctorState.check_message, size="3"),
                            spacing="3",
                        ),
                        rx.cond(
                            DoctorState.check_message != "",
                            rx.cond(
                                DoctorState.is_healthy,
                                rx.callout(
                                    DoctorState.check_message,
                                    icon="circle_check",
                                    color_scheme="green",
                                    size="2",
                                ),
                                rx.callout(
                                    DoctorState.check_message,
                                    icon="triangle_alert",
                                    color_scheme="orange",
                                    size="2",
                                ),
                            ),
                            rx.box(),
                        ),
                    ),
                    spacing="4",
                    align="start",
                    width="100%",
                ),
                width="100%",
            ),
            
            # Database Statistics
            rx.heading("Database Statistics", size="6", margin_top="6", margin_bottom="3"),
            rx.grid(
                stat_card("Total Tracks", DoctorState.doctor_total_tracks),
                stat_card("Tracks with Embeddings", DoctorState.doctor_tracks_with_embeddings),
                stat_card("Untagged Tracks", DoctorState.doctor_untagged_tracks),
                columns=rx.breakpoints(initial="1", sm="2", lg="3"),
                spacing="4",
                width="100%",
            ),
            
            # Issues Section
            rx.cond(
                ~DoctorState.is_healthy,
                rx.vstack(
                    rx.heading("Issues Found", size="6", margin_top="6", margin_bottom="3"),
                    
                    # Orphaned Embeddings Issue
                    rx.cond(
                        DoctorState.doctor_orphaned_embeddings > 0,
                        rx.card(
                            rx.vstack(
                                rx.hstack(
                                    rx.icon("circle_alert", color="orange", size=20),
                                    rx.heading(DoctorState.orphaned_embeddings_label, size="4"),
                                    spacing="2",
                                ),
                                rx.text(
                                    "These are embeddings for tracks that no longer exist in your library.",
                                    size="2",
                                    color_scheme="gray",
                                ),
                                rx.button(
                                    "Delete Orphaned Embeddings",
                                    size="3",
                                    color_scheme="red",
                                    variant="soft",
                                    on_click=DoctorState.delete_orphaned_embeddings,
                                    loading=DoctorState.current_fix_target == "cleanup",
                                    disabled=DoctorState.is_fixing,
                                ),
                                spacing="3",
                                align="start",
                                width="100%",
                            ),
                            width="100%",
                        ),
                        rx.box(),
                    ),
                    
                    # Missing Embeddings Issue
                    rx.cond(
                        DoctorState.doctor_tracks_needing_embeddings > 0,
                        rx.card(
                            rx.vstack(
                                rx.hstack(
                                    rx.icon("circle_alert", color="orange", size=20),
                                    rx.heading(DoctorState.missing_embeddings_label, size="4"),
                                    spacing="2",
                                ),
                                rx.text(
                                    "These tracks don't have embeddings and can't be used for playlist generation.",
                                    size="2",
                                    color_scheme="gray",
                                ),
                                rx.hstack(
                                    rx.button(
                                        "Generate Missing Embeddings",
                                        size="3",
                                        color_scheme="blue",
                                        on_click=DoctorState.generate_missing_embeddings,
                                        loading=DoctorState.incremental_embedding_running,
                                        disabled=DoctorState.is_fixing,
                                    ),
                                    rx.button(
                                        "Rebuild All Embeddings",
                                        size="3",
                                        color_scheme="red",
                                        variant="surface",
                                        on_click=DoctorState.regenerate_all_embeddings,
                                        loading=DoctorState.full_embedding_running,
                                        disabled=DoctorState.is_fixing,
                                    ),
                                    spacing="3",
                                    width="100%",
                                    align="start",
                                ),
                                rx.text(
                                    "Full rebuild deletes every stored embedding and recreates the FAISS index. Use this when switching providers or troubleshooting.",
                                    size="1",
                                    color_scheme="gray",
                                ),
                                rx.cond(
                                    DoctorState.embedding_job_running,
                                    rx.vstack(
                                        rx.progress(
                                            value=(DoctorState.fix_progress / DoctorState.fix_total) * 100,
                                            max=100,
                                        ),
                                        rx.text(
                                            DoctorState.fix_progress_label,
                                            size="2",
                                            color_scheme="gray",
                                        ),
                                        spacing="2",
                                        width="100%",
                                    ),
                                    rx.box(),
                                ),
                                spacing="3",
                                align="start",
                                width="100%",
                            ),
                            width="100%",
                        ),
                        rx.box(),
                    ),
                    
                    # Fix Status Message
                    rx.cond(
                        DoctorState.fix_message != "",
                        rx.callout(
                            DoctorState.fix_message,
                            icon="info",
                            color_scheme="blue",
                            size="2",
                        ),
                        rx.box(),
                    ),
                    
                    spacing="4",
                    width="100%",
                ),
                rx.box(),
            ),
            
            # Tag Maintenance
            rx.heading("Tag Maintenance", size="6", margin_top="6", margin_bottom="3"),
            rx.card(
                rx.vstack(
                    rx.callout(
                        DoctorState.untagged_tracks_message,
                        icon="info",
                        color_scheme="blue",
                        size="2",
                    ),
                    rx.hstack(
                        rx.button(
                            "Regenerate Missing Tags",
                            size="3",
                            color_scheme="blue",
                            variant="soft",
                            on_click=DoctorState.regenerate_missing_tags,
                            loading=DoctorState.tag_job_running,
                            disabled=DoctorState.is_fixing,
                        ),
                        align="start",
                    ),
                    rx.cond(
                        DoctorState.tag_job_running,
                        rx.vstack(
                            rx.progress(
                                value=(DoctorState.fix_progress / DoctorState.fix_total) * 100,
                                max=100,
                            ),
                            rx.text(
                                DoctorState.fix_progress_label,
                                size="2",
                                color_scheme="gray",
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        rx.box(),
                    ),
                    spacing="3",
                    align="start",
                    width="100%",
                ),
                width="100%",
            ),
            
            spacing="4",
            width="100%",
        ),
        size="4",
    )
    return layout(content)
