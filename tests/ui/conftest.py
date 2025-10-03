"""Pytest configuration and fixtures for UI tests."""
import sys
from unittest.mock import MagicMock, Mock
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# Mock Reflex before any imports
sys.modules['reflex'] = MagicMock()
rx_mock = sys.modules['reflex']

# Mock Reflex State class
class MockState:
    """Mock Reflex State base class."""
    def __init__(self):
        pass

    def on_load(self):
        pass

rx_mock.State = MockState

# Mock Reflex decorators
def mock_background(func):
    """Mock @rx.background decorator."""
    return func

rx_mock.background = mock_background

# Mock other Reflex components
rx_mock.Component = Mock
rx_mock.box = Mock
rx_mock.vstack = Mock
rx_mock.hstack = Mock
rx_mock.text = Mock
rx_mock.button = Mock
rx_mock.icon = Mock
rx_mock.card = Mock
rx_mock.grid = Mock
rx_mock.table = Mock
rx_mock.dialog = Mock
rx_mock.callout = Mock
rx_mock.input = Mock
rx_mock.select = Mock
rx_mock.checkbox = Mock
rx_mock.slider = Mock
rx_mock.progress = Mock
rx_mock.skeleton = Mock
rx_mock.foreach = Mock
rx_mock.cond = Mock
rx_mock.fragment = Mock
rx_mock.spacer = Mock
rx_mock.divider = Mock
rx_mock.link = Mock
rx_mock.image = Mock
rx_mock.heading = Mock
rx_mock.container = Mock
rx_mock.tabs = Mock
rx_mock.badge = Mock
rx_mock.tooltip = Mock
rx_mock.download = Mock
rx_mock.redirect = Mock

# Mock nested components
rx_mock.table.root = Mock
rx_mock.table.header = Mock
rx_mock.table.body = Mock
rx_mock.table.row = Mock
rx_mock.table.cell = Mock
rx_mock.table.column_header_cell = Mock

rx_mock.dialog.root = Mock
rx_mock.dialog.content = Mock
rx_mock.dialog.close = Mock

rx_mock.callout.root = Mock
rx_mock.callout.text = Mock
rx_mock.callout.icon = Mock

rx_mock.alert_dialog = Mock()
rx_mock.alert_dialog.root = Mock
rx_mock.alert_dialog.content = Mock
rx_mock.alert_dialog.title = Mock
rx_mock.alert_dialog.description = Mock
rx_mock.alert_dialog.cancel = Mock
rx_mock.alert_dialog.action = Mock

# Mock breakpoints
rx_mock.breakpoints = Mock(return_value="responsive")

# Mock theme
rx_mock.theme = Mock

# Mock App class
class MockApp:
    def __init__(self, **kwargs):
        self.theme = kwargs.get('theme')

    def add_page(self, component, route, on_load=None):
        pass

rx_mock.App = MockApp