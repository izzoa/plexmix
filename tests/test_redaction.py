"""Tests for masking credentials in server addresses and error texts.

A Plex address can hold a user name and a password, and PlexAPI repeats the address
in its error texts. ``plexmix.utils.redaction`` hides them wherever PlexMix prints
or logs an address. The only "secrets" used here are the project's dummy values.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.plex.sync import SyncEngine
from plexmix.utils.redaction import (
    mask_credentials_of,
    mask_url_credentials,
    mask_urls_in_text,
)

PASSWORD = "dummy-ui-password-0000"
TOKEN = "dummy-plex-token-0000"


# ---------------------------------------------------------------------------
# mask_url_credentials: one address
# ---------------------------------------------------------------------------


class TestMaskUrlCredentials:
    @pytest.mark.parametrize(
        "address, expected",
        [
            # user name and password, with a scheme
            (f"http://user:{PASSWORD}@plexhost:32400", "http://***@plexhost:32400"),
            (f"https://user:{PASSWORD}@plex.example.com", "https://***@plex.example.com"),
            # no scheme (the address does not parse as a URL)
            (f"user:{PASSWORD}@plexhost:32400", "***@plexhost:32400"),
            # user name only
            ("http://user@plexhost:32400", "http://***@plexhost:32400"),
            ("user@plexhost:32400", "***@plexhost:32400"),
            # a password that contains ":" or "@"
            ("http://user:dummy:ui-password-0000@plexhost:32400", "http://***@plexhost:32400"),
            ("http://user:dummy@ui-password-0000@plexhost:32400", "http://***@plexhost:32400"),
            ("user:dummy@ui:password-0000@plexhost:32400", "***@plexhost:32400"),
            # percent-encoded characters
            ("http://us%20er:dummy%40ui%3Apassword-0000@plexhost", "http://***@plexhost"),
            # an IPv6 host
            (f"http://user:{PASSWORD}@[::1]:32400", "http://***@[::1]:32400"),
            (f"user:{PASSWORD}@[fe80::1]:32400", "***@[fe80::1]:32400"),
            # a password with "/", "?" or "#" that was not percent-encoded
            ("http://user:dummy/ui?password#0000@plexhost:32400", "http://***@plexhost:32400"),
        ],
    )
    def test_credentials_are_masked(self, address: str, expected: str) -> None:
        assert mask_url_credentials(address) == expected

    @pytest.mark.parametrize(
        "address, expected",
        [
            # A password with a space, a tab or a line break. The address works:
            # the HTTP library percent-encodes them and sends the credentials.
            ("http://user:dummy ui password-0000@plexhost:32400", "http://***@plexhost:32400"),
            ("http://user:dummy\tui password-0000@plexhost:32400", "http://***@plexhost:32400"),
            ("http://user:dummy\nui-password-0000@plexhost:32400", "http://***@plexhost:32400"),
            ("user:dummy ui password-0000@plexhost:32400", "***@plexhost:32400"),
            ("the user:dummy ui password-0000@plexhost", "***@plexhost"),
            # ... together with a "/" in the password
            ("http://user:dummy ui/pass word-0000@plexhost:32400", "http://***@plexhost:32400"),
            # whitespace around the address is kept
            (f"  http://user:{PASSWORD}@plexhost:32400\n", "  http://***@plexhost:32400\n"),
            # a password of any length
            (f"http://user:{'x' * 5000}@plexhost:32400", "http://***@plexhost:32400"),
        ],
    )
    def test_credentials_with_whitespace_or_any_length_are_masked(
        self, address: str, expected: str
    ) -> None:
        masked = mask_url_credentials(address)
        assert masked == expected
        assert "dummy" not in masked and "user" not in masked

    @pytest.mark.parametrize(
        "address, expected",
        [
            # a mistyped scheme
            (f"http:/user:{PASSWORD}@plexhost:32400", "http:/***@plexhost:32400"),
            (f"https:/user:{PASSWORD}@plexhost:32400", "https:/***@plexhost:32400"),
            (f"http//user:{PASSWORD}@plexhost:32400", "http//***@plexhost:32400"),
            (f"http:///user:{PASSWORD}@plexhost:32400", "http:///***@plexhost:32400"),
            (f"//user:{PASSWORD}@plexhost:32400", "//***@plexhost:32400"),
            (f"htp://user:{PASSWORD}@plexhost:32400", "htp://***@plexhost:32400"),
            # not recognised as a scheme, so it is masked with the rest
            (f"htp:/user:{PASSWORD}@plexhost:32400", "***@plexhost:32400"),
            (f"http:user:{PASSWORD}@plexhost:32400", "***@plexhost:32400"),
            # nothing after the "@"
            (f"http://user:{PASSWORD}@", "http://***@"),
            (f"http://user:{PASSWORD}@/web", "http://***@/web"),
            (f"user:{PASSWORD}@", "***@"),
            (f"http://user:{PASSWORD}@ \n", "http://***@ \n"),
        ],
    )
    def test_credentials_in_a_mistyped_address_are_masked(
        self, address: str, expected: str
    ) -> None:
        masked = mask_url_credentials(address)
        assert masked == expected
        assert PASSWORD not in masked and "user" not in masked

    @pytest.mark.parametrize(
        "address, expected",
        [
            # Plex's own "View XML" links have this form.
            (
                f"http://plexhost:32400/?X-Plex-Token={TOKEN}",
                "http://plexhost:32400/?X-Plex-Token=***",
            ),
            (
                f"http://plexhost:32400/library/sections?type=10&x-plex-token={TOKEN}&x=1#top",
                "http://plexhost:32400/library/sections?type=10&x-plex-token=***&x=1#top",
            ),
            (
                f"http://user:{PASSWORD}@plexhost:32400/?X-Plex-Token={TOKEN}",
                "http://***@plexhost:32400/?X-Plex-Token=***",
            ),
        ],
    )
    def test_token_parameter_in_the_address_is_masked(self, address: str, expected: str) -> None:
        masked = mask_url_credentials(address)
        assert masked == expected
        assert TOKEN not in masked

    def test_value_that_is_not_text_is_returned_as_it_is(self) -> None:
        # A display helper must not make a command fail on an odd settings value.
        for value in (None, 32400, MagicMock()):
            assert mask_url_credentials(value) is value  # type: ignore[arg-type]

    def test_host_port_and_path_are_kept(self) -> None:
        masked = mask_url_credentials(f"https://user:{PASSWORD}@plexhost:32400/web/index?x=1#top")
        assert masked == "https://***@plexhost:32400/web/index?x=1#top"

    @pytest.mark.parametrize(
        "address",
        [
            "http://localhost:32400",
            "https://plex.example.com:32400/web/index.html",
            "192.168.1.10:32400",
            "plexhost",
            "http://[::1]:32400/",
            "[::1]:32400",
            # an "@" after the host is part of the path or query, not a user name
            "http://plexhost:32400/library?contact=someone@example.com",
            "http://plexhost:32400/a@b",
            "plexhost:32400/a@b",
        ],
    )
    def test_address_without_credentials_is_unchanged(self, address: str) -> None:
        assert mask_url_credentials(address) == address

    @pytest.mark.parametrize("value", ["", "Not set", "not an address", "5 tracks @ 320 kbps", "@"])
    def test_string_that_is_not_an_address_is_unchanged(self, value: str) -> None:
        assert mask_url_credentials(value) == value

    def test_masking_twice_changes_nothing(self) -> None:
        once = mask_url_credentials(f"http://user:{PASSWORD}@plexhost:32400")
        assert mask_url_credentials(once) == once


# ---------------------------------------------------------------------------
# mask_urls_in_text: addresses inside a longer text
# ---------------------------------------------------------------------------


class TestMaskUrlsInText:
    def test_address_inside_an_error_text(self) -> None:
        text = (
            f"(401) unauthorized; http://user:{PASSWORD}@plexhost:32400/library/sections "
            "<html><head><title>Unauthorized</title></head></html>"
        )
        assert mask_urls_in_text(text) == (
            "(401) unauthorized; http://***@plexhost:32400/library/sections "
            "<html><head><title>Unauthorized</title></head></html>"
        )

    def test_two_addresses_in_one_text(self) -> None:
        text = (
            f"tried http://user:{PASSWORD}@plexhost:32400/ then "
            f"https://other:{PASSWORD}@backup.example.com:32400/library and gave up"
        )
        assert mask_urls_in_text(text) == (
            "tried http://***@plexhost:32400/ then "
            "https://***@backup.example.com:32400/library and gave up"
        )

    def test_quoted_address_without_a_scheme(self) -> None:
        text = f"No connection adapters were found for 'user:{PASSWORD}@plexhost:32400/'"
        assert mask_urls_in_text(text) == (
            "No connection adapters were found for '***@plexhost:32400/'"
        )

    @pytest.mark.parametrize(
        "text, expected",
        [
            # What the HTTP library says about an address with a mistyped scheme.
            (
                f"Invalid URL 'http:/user:{PASSWORD}@plexhost:32400/': No host supplied",
                "Invalid URL 'http:/***@plexhost:32400/': No host supplied",
            ),
            (
                f"Invalid URL 'http:///user:{PASSWORD}@plexhost:32400/': No host supplied",
                "Invalid URL 'http:///***@plexhost:32400/': No host supplied",
            ),
            (
                f"Invalid URL 'http//user:{PASSWORD}@plexhost/': No scheme supplied. "
                f"Perhaps you meant https://http//user:{PASSWORD}@plexhost/?",
                "Invalid URL 'http//***@plexhost/': No scheme supplied. "
                "Perhaps you meant https://http//***@plexhost/?",
            ),
            (
                f"No connection adapters were found for '//user:{PASSWORD}@plexhost:32400/'",
                "No connection adapters were found for '//***@plexhost:32400/'",
            ),
            # A scheme right after a character that cannot start a scheme name.
            (f"-http://user:{PASSWORD}@plexhost:32400/", "-http://***@plexhost:32400/"),
            (f"9http://user:{PASSWORD}@plexhost", "9http://***@plexhost"),
            (f"x.https://user:{PASSWORD}@plexhost", "x.https://***@plexhost"),
            (f"url=http://user:{PASSWORD}@plexhost", "url=http://***@plexhost"),
        ],
    )
    def test_mistyped_and_glued_schemes_are_masked(self, text: str, expected: str) -> None:
        masked = mask_urls_in_text(text)
        assert masked == expected
        assert PASSWORD not in masked and "user:" not in masked

    def test_token_parameter_value_is_masked(self) -> None:
        text = f"GET http://plexhost:32400/library/sections?X-Plex-Token={TOKEN}&type=10 failed"
        assert mask_urls_in_text(text) == (
            "GET http://plexhost:32400/library/sections?X-Plex-Token=***&type=10 failed"
        )

    def test_token_parameter_and_credentials_together(self) -> None:
        text = f"http://user:{PASSWORD}@plexhost:32400/?X-Plex-Token={TOKEN}"
        masked = mask_urls_in_text(text)
        assert masked == "http://***@plexhost:32400/?X-Plex-Token=***"
        assert PASSWORD not in masked
        assert TOKEN not in masked

    def test_token_parameter_name_in_any_case(self) -> None:
        assert mask_urls_in_text(f"/?x-plex-token={TOKEN}#top") == "/?x-plex-token=***#top"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not an address",
            "Connection attempt 1 failed: timed out. Retrying...",
            "Track has empty title (key: 123)",
            "HTTPConnectionPool(host='plexhost', port=32400): Max retries exceeded with url: / "
            "(Caused by NewConnectionError('<HTTPConnection object at 0x10f3b2d50>: "
            "[Errno 61] Connection refused'))",
            "5 tracks @ 320 kbps (@mention) at 12:30",
            "see http://plexhost:32400/web/index.html and /music/Artist@Home/track.flac",
            # an "@" in a path stays a path, also after a mistyped or glued scheme
            "-http://plexhost:32400/a@b",
            "http:/plexhost:32400/a@b",
            "/music//Artist@Home/x",
            "file:///Artist@Home/track.flac",
            "file:///music/Artist@Home/track.flac",
            "C:/Users/me@home/x",
            "https://plex.tv/users/sign_in.json and https://clients.plex.tv/api/v2/user?a=b",
        ],
    )
    def test_ordinary_text_is_unchanged(self, text: str) -> None:
        assert mask_urls_in_text(text) == text

    def test_email_address_is_masked_too(self) -> None:
        # Accepted side effect: an e-mail address has the shape "user@host".
        assert mask_urls_in_text("contact someone@example.com now") == (
            "contact ***@example.com now"
        )

    def test_multi_line_text(self) -> None:
        text = f"first line\nhttp://user:{PASSWORD}@plexhost:32400\nlast line"
        assert mask_urls_in_text(text) == "first line\nhttp://***@plexhost:32400\nlast line"

    def test_long_text_without_spaces_is_handled(self) -> None:
        # One long word (a minified error body) with an address at its end.
        blob = '{"key":"value",' * 20000
        masked = mask_urls_in_text(f"{blob} http://user:{PASSWORD}@plexhost")
        assert masked == f"{blob} http://***@plexhost"


# ---------------------------------------------------------------------------
# mask_credentials_of: the credentials of a known address, wherever they stand
# ---------------------------------------------------------------------------


class TestMaskCredentialsOf:
    # A raw "/" in the password: an HTTP library reads "leakuser:dummy-ui" as host
    # and port, and the rest as the path.
    ADDRESS = "http://leakuser:dummy-ui/password-0000@plexhost:32400"

    @pytest.mark.parametrize(
        "text, expected",
        [
            # the part that was read as host and port, quoted without an "@"
            (
                "Failed to parse: 'leakuser:dummy-ui' is not a valid host or port",
                "Failed to parse: '***' is not a valid host or port",
            ),
            # single pieces
            (
                "HTTPConnectionPool(host='leakuser', port=80): Max retries exceeded with url: "
                "/password-0000@plexhost:32400/",
                "HTTPConnectionPool(host='***', port=80): Max retries exceeded with url: "
                "/***@plexhost:32400/",
            ),
            # the whole credentials and the whole password
            ("tried leakuser:dummy-ui/password-0000 first", "tried *** first"),
            ("password was dummy-ui/password-0000.", "password was ***."),
        ],
    )
    def test_credentials_are_replaced_whole_and_in_pieces(self, text: str, expected: str) -> None:
        assert mask_credentials_of(self.ADDRESS, text) == expected

    def test_piece_is_replaced_only_as_a_whole_word(self) -> None:
        text = "leakusers and xleakuser are other words; leakuser is not"
        assert mask_credentials_of(self.ADDRESS, text) == (
            "leakusers and xleakuser are other words; *** is not"
        )

    def test_piece_that_the_address_shows_anyway_is_kept(self) -> None:
        # The user name is also the host name: masking it would hide the host.
        address = f"http://plexhost:{PASSWORD}@plexhost:32400"
        text = f"HTTPConnectionPool(host='plexhost', port=32400): no route ({PASSWORD})"
        assert mask_credentials_of(address, text) == (
            "HTTPConnectionPool(host='plexhost', port=32400): no route (***)"
        )

    def test_short_password_is_replaced_only_as_a_whole_word(self) -> None:
        # A password that is also part of ordinary words must not garble them.
        text = "plexapi could not reach plexserver (tried 'plex', got: plex)"
        assert mask_credentials_of("http://admin:plex@127.0.0.1:32400", text) == (
            "plexapi could not reach plexserver (tried '***', got: ***)"
        )

    def test_short_pieces_are_left_alone(self) -> None:
        # Replacing "me" or "pw1" everywhere would garble the text and hide nothing.
        text = "tell me why pw1 came back"
        assert mask_credentials_of("http://me:pw1@plexhost:32400", text) == text

    @pytest.mark.parametrize(
        "address",
        ["http://plexhost:32400", "plexhost:32400/a@b", "", "not an address"],
    )
    def test_address_without_credentials_changes_nothing(self, address: str) -> None:
        text = "HTTPConnectionPool(host='plexhost', port=32400): Max retries exceeded"
        assert mask_credentials_of(address, text) == text

    def test_empty_text_and_odd_address_values(self) -> None:
        assert mask_credentials_of(self.ADDRESS, "") == ""
        assert mask_credentials_of(None, "some text") == "some text"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The failed-sync log line and history row
# ---------------------------------------------------------------------------


def test_failed_sync_masks_address_in_log_and_history(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed sync stores and logs the error text with credentials masked."""
    db = SQLiteManager(str(tmp_path / "plexmix.db"))
    db.connect()
    try:
        plex = MagicMock()
        plex.validate_token.return_value = (True, "OK")
        plex.get_all_artists.side_effect = OSError(
            f"(502) bad_gateway; http://user:{PASSWORD}@plexhost:32400/library/sections/1/all"
            f"?X-Plex-Token={TOKEN}"
        )
        engine = SyncEngine(plex_client=plex, db_manager=db)

        with caplog.at_level(logging.DEBUG, logger="plexmix.plex.sync"):
            with pytest.raises(OSError):
                engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status, error_message FROM sync_history ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
    finally:
        db.close()

    assert row["status"] == "failed"
    assert row["error_message"] == (
        "(502) bad_gateway; http://***@plexhost:32400/library/sections/1/all?X-Plex-Token=***"
    )
    assert "Incremental sync failed: (502) bad_gateway; http://***@plexhost:32400" in caplog.text
    for secret in (PASSWORD, TOKEN, "user:"):
        assert secret not in caplog.text
        assert secret not in row["error_message"]
