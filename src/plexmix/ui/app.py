import reflex as rx
from plexmix.ui.pages.index import index
from plexmix.ui.pages.dashboard import dashboard
from plexmix.ui.pages.settings import settings
from plexmix.ui.pages.library import library
from plexmix.ui.pages.generator import generator
from plexmix.ui.pages.tagging import tagging
from plexmix.ui.pages.history import history
from plexmix.ui.pages.doctor import doctor
from plexmix.ui.states.dashboard_state import DashboardState
from plexmix.ui.states.settings_state import SettingsState
from plexmix.ui.states.library_state import LibraryState
from plexmix.ui.states.generator_state import GeneratorState
from plexmix.ui.states.tagging_state import TaggingState
from plexmix.ui.states.history_state import HistoryState
from plexmix.ui.states.doctor_state import DoctorState

app = rx.App(
    theme=rx.theme(
        appearance="dark",
        accent_color="orange",
        radius="medium",
    ),
    stylesheets=[
        "/styles.css",
    ],
)

app.add_page(index, route="/")
app.add_page(dashboard, route="/dashboard", on_load=DashboardState.on_load)
app.add_page(settings, route="/settings", on_load=SettingsState.on_load)
app.add_page(library, route="/library", on_load=LibraryState.on_load)
app.add_page(generator, route="/generator", on_load=GeneratorState.on_load)
app.add_page(tagging, route="/tagging", on_load=TaggingState.on_load)
app.add_page(history, route="/history", on_load=HistoryState.on_load)
app.add_page(doctor, route="/doctor", on_load=DoctorState.on_load)
