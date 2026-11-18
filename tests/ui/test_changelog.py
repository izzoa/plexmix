"""Tests for the server-side changelog loader (plexmix.ui.changelog)."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

import plexmix
from plexmix.ui import changelog
from plexmix.ui.changelog import ChangelogView, load_changelog

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE = """# Changelog

All notable changes to PlexMix will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Add a thing with `code`

## [1.0.0] - 2026-01-01

### Fixed
- Fix another thing

[Unreleased]: https://github.com/izzoa/plexmix/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/izzoa/plexmix/releases/tag/v1.0.0
"""


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    changelog.clear_cache()
    yield
    changelog.clear_cache()


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A fake installed package dir and a fake source checkout, both initially empty.

    ``packaged`` is what ``importlib.resources.files("plexmix")`` returns; ``checkout``
    is the repo root that ``plexmix.__file__`` (``<checkout>/src/plexmix/__init__.py``)
    resolves to via ``parents[2]``.
    """
    packaged = tmp_path / "site-packages" / "plexmix"
    packaged.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    (checkout / "src" / "plexmix").mkdir(parents=True)
    init = checkout / "src" / "plexmix" / "__init__.py"
    init.write_text("")

    monkeypatch.setattr(changelog, "resources", SimpleNamespace(files=lambda package: packaged))
    monkeypatch.setattr(plexmix, "__file__", str(init))
    elsewhere = tmp_path / "unrelated-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return SimpleNamespace(packaged=packaged, checkout=checkout, cwd=elsewhere)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestResolution:
    def test_packaged_file_wins_over_checkout(self, layout: SimpleNamespace) -> None:
        _write(layout.packaged / "CHANGELOG.md", "## [9.9.9]\n\n- PACKAGED-MARKER\n")
        _write(layout.checkout / "CHANGELOG.md", "## [0.0.1]\n\n- CHECKOUT-MARKER\n")
        view = load_changelog()
        assert view.available
        assert "PACKAGED-MARKER" in view.html
        assert "CHECKOUT-MARKER" not in view.html

    def test_checkout_fallback_from_unrelated_cwd(self, layout: SimpleNamespace) -> None:
        _write(layout.checkout / "CHANGELOG.md", "## [0.0.1]\n\n- CHECKOUT-MARKER\n")
        # A CHANGELOG.md in the cwd must not be picked up (no cwd-relative lookup).
        _write(layout.cwd / "CHANGELOG.md", "## [6.6.6]\n\n- CWD-MARKER\n")
        assert Path.cwd() == layout.cwd
        view = load_changelog()
        assert view.available
        assert "CHECKOUT-MARKER" in view.html
        assert "CWD-MARKER" not in view.html

    def test_real_checkout_resolves_repo_changelog_from_unrelated_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_pkg = tmp_path / "pkg"
        empty_pkg.mkdir()
        monkeypatch.setattr(
            changelog, "resources", SimpleNamespace(files=lambda package: empty_pkg)
        )
        monkeypatch.chdir(tmp_path)
        assert changelog.find_changelog() == REPO_ROOT / "CHANGELOG.md"
        view = load_changelog()
        assert view.available
        assert view.version == plexmix.__version__
        assert view.html.startswith("<h2>")

    def test_missing_returns_unavailable_with_version(self, layout: SimpleNamespace) -> None:
        view = load_changelog()
        assert view == ChangelogView(version=plexmix.__version__, html="", available=False)

    def test_version_is_running_version(self, layout: SimpleNamespace) -> None:
        _write(layout.packaged / "CHANGELOG.md", SAMPLE)
        assert load_changelog().version == plexmix.__version__


class TestRendering:
    def test_h1_and_preamble_are_stripped(self, layout: SimpleNamespace) -> None:
        _write(layout.packaged / "CHANGELOG.md", SAMPLE)
        html = load_changelog().html
        assert html.startswith("<h2>")
        assert "<h1>" not in html
        assert "All notable changes" not in html
        assert "Keep a Changelog" not in html
        assert "Unreleased" in html
        assert "<h3>Added</h3>" in html
        assert "<code>code</code>" in html
        # Reference-style links at the bottom still resolve.
        assert 'href="https://github.com/izzoa/plexmix/compare/v1.0.0...HEAD"' in html

    def test_without_release_heading_renders_whole_file(self) -> None:
        assert changelog.release_notes_markdown("just text\n") == "just text\n"

    def test_script_and_raw_html_are_escaped(self, layout: SimpleNamespace) -> None:
        _write(
            layout.packaged / "CHANGELOG.md",
            "## [1.0]\n\n<script>alert(1)</script>\n\n"
            '- inline <img src=x onerror="alert(1)"> and <b>bold</b>\n\n'
            "<div onclick=alert(1)>block</div>\n",
        )
        html = load_changelog().html
        assert "<script" not in html
        assert "&lt;script&gt;" in html
        assert "<img" not in html
        assert "<b>" not in html
        assert "<div" not in html
        assert "&lt;div" in html

    @pytest.mark.parametrize(
        "markdown",
        [
            "[click](javascript:alert(1))",
            "[click](JaVaScRiPt:alert(1))",
            "[click](  javascript:alert(1))",
            "<javascript:alert(1)>",
            "[click][ref]\n\n[ref]: javascript:alert(1)",
            "[click](vbscript:msgbox(1))",
        ],
    )
    def test_javascript_link_is_not_rendered_as_link(
        self, layout: SimpleNamespace, markdown: str
    ) -> None:
        _write(layout.packaged / "CHANGELOG.md", f"## [1.0]\n\n{markdown}\n")
        html = load_changelog().html
        assert "<a " not in html
        assert "href" not in html

    def test_safe_links_still_render(self, layout: SimpleNamespace) -> None:
        _write(layout.packaged / "CHANGELOG.md", "## [1.0]\n\n[docs](https://example.com/x)\n")
        assert '<a href="https://example.com/x">docs</a>' in load_changelog().html

    def test_tables_are_enabled(self, layout: SimpleNamespace) -> None:
        _write(layout.packaged / "CHANGELOG.md", "## [1.0]\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        assert "<table>" in load_changelog().html


class TestCache:
    def test_cached_until_mtime_changes(
        self, layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        real_render = changelog.render_markdown

        def counting_render(text: str) -> str:
            calls.append(text)
            return real_render(text)

        monkeypatch.setattr(changelog, "render_markdown", counting_render)
        path = _write(layout.packaged / "CHANGELOG.md", "## [1.0]\n\n- first\n")
        first = load_changelog()
        second = load_changelog()
        assert len(calls) == 1
        assert second is first

        _write(path, "## [1.1]\n\n- second\n")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        third = load_changelog()
        assert len(calls) == 2
        assert "second" in third.html
