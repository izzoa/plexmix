"""Tests for the pure Web UI helpers: readout formatting and plain-language errors.

``plexmix.ui.utils.helpers`` and ``plexmix.ui.utils.errors`` import no Reflex, so
these run without the ``ui`` extra and outside ``tests/ui``'s mocked Reflex.
"""

import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from plexmix.ui.utils.errors import (
    INVALID_API_KEY_MESSAGE,
    INVALID_REQUEST_MESSAGE,
    _friendly_error,
    friendly_error,
)
from plexmix.ui.utils.helpers import (
    format_count,
    format_percent,
    format_relative,
    format_short_datetime,
    pluralize,
)

# Fixed reference time for relative formatting (naive local, like the models).
NOW = datetime(2026, 11, 17, 12, 0, 0)

# The body Gemini returns for a rejected key, as google-genai's APIError renders
# it (``f"{code} {status}. {response_json}"``). The audit screenshot
# shots/review-edge/settings-embed-raw-json-and-false-unsaved.png shows its first
# 117 characters in the Settings embeddings test status.
GEMINI_400_INVALID_KEY = (
    "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. "
    "Please pass a valid API key.', 'status': 'INVALID_ARGUMENT', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'API_KEY_INVALID', "
    "'domain': 'googleapis.com', 'metadata': {'service': "
    "'generativelanguage.googleapis.com'}}, {'@type': "
    "'type.googleapis.com/google.rpc.LocalizedMessage', 'locale': 'en-US', "
    "'message': 'API key not valid. Please pass a valid API key.'}]}}"
)
# Exactly the text the audit screenshot shows (the old fallback's 117-char cut).
GEMINI_400_AS_SHOWN = (
    "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. "
    "Please pass a valid API key.', 'status':..."
)

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------------------
# format_count
# ---------------------------------------------------------------------------


class TestFormatCount:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (5943, "5,943"),
            (0, "0"),
            (7, "7"),
            (1234567, "1,234,567"),
            (-1200, "-1,200"),
            (5943.0, "5,943"),
            (5943.5, "5,944"),
            ("5943", "5,943"),
            (" 5,943 ", "5,943"),
        ],
    )
    def test_groups_thousands(self, value, expected):
        assert format_count(value) == expected

    @pytest.mark.parametrize(
        "value", [None, "", "   ", "abc", "12abc", True, False, float("nan"), float("inf")]
    )
    def test_missing_or_bad_input_is_empty(self, value):
        assert format_count(value) == ""


# ---------------------------------------------------------------------------
# format_percent
# ---------------------------------------------------------------------------


class TestFormatPercent:
    def test_spec_example(self):
        # webui-dashboard-overview: 4,120 of 5,943 tagged reads "69%".
        assert format_percent(4120, 5943) == "69%"

    @pytest.mark.parametrize(
        "part, total, expected",
        [
            (0, 5943, "0%"),
            (5943, 5943, "100%"),
            (1, 2, "50%"),
            (1, 8, "13%"),  # 12.5 rounds half up
            ("4120", "5943", "69%"),
            (0, 0, "0%"),  # empty library: no share yet, not an error
        ],
    )
    def test_whole_percent(self, part, total, expected):
        assert format_percent(part, total) == expected

    def test_tiny_share_is_not_zero(self):
        assert format_percent(1, 5943) == "<1%"

    def test_incomplete_share_never_reads_100(self):
        assert format_percent(5942, 5943) == ">99%"

    def test_part_above_total_is_capped(self):
        assert format_percent(6000, 5943) == "100%"

    @pytest.mark.parametrize(
        "part, total",
        [(None, 5943), (4120, None), ("abc", 5943), (4120, "x"), (-1, 10), (1, -10)],
    )
    def test_missing_or_bad_input_is_empty(self, part, total):
        assert format_percent(part, total) == ""


# ---------------------------------------------------------------------------
# pluralize
# ---------------------------------------------------------------------------


class TestPluralize:
    @pytest.mark.parametrize(
        "count, expected",
        [
            (1, "1 track"),
            (2, "2 tracks"),
            (0, "0 tracks"),
            (5943, "5,943 tracks"),
            ("1", "1 track"),
            (1.0, "1 track"),
            (-1, "-1 track"),
        ],
    )
    def test_count_and_noun(self, count, expected):
        assert pluralize(count, "track") == expected

    def test_irregular_plural(self):
        assert pluralize(1, "match", "matches") == "1 match"
        assert pluralize(3, "match", "matches") == "3 matches"

    @pytest.mark.parametrize("count", [None, "", "abc", float("nan")])
    def test_missing_or_bad_count_is_empty(self, count):
        assert pluralize(count, "track") == ""


# ---------------------------------------------------------------------------
# format_relative
# ---------------------------------------------------------------------------


class TestFormatRelative:
    def test_two_hours(self):
        assert format_relative(NOW - timedelta(hours=2), NOW) == "2 h ago"

    def test_db_string_as_stored(self):
        assert format_relative("2026-11-17 09:45:19.327064", NOW) == "2 h ago"
        assert format_relative("2026-11-17T10:00:00", NOW) == "2 h ago"
        # Whole units, rounded down: 1 h 59 min is still "1 h ago".
        assert format_relative("2026-11-17 10:00:00.327064", NOW) == "1 h ago"

    @pytest.mark.parametrize(
        "delta, expected",
        [
            (timedelta(seconds=0), "just now"),
            (timedelta(seconds=59), "just now"),
            (timedelta(seconds=60), "1 min ago"),
            (timedelta(minutes=5, seconds=40), "5 min ago"),
            (timedelta(minutes=59, seconds=59), "59 min ago"),
            (timedelta(hours=1), "1 h ago"),
            (timedelta(hours=2, minutes=59), "2 h ago"),
            (timedelta(hours=23, minutes=59), "23 h ago"),
            (timedelta(days=1), "1 d ago"),
            (timedelta(days=29, hours=23), "29 d ago"),
        ],
    )
    def test_steps(self, delta, expected):
        assert format_relative(NOW - delta, NOW) == expected

    def test_older_than_thirty_days_shows_the_date(self):
        assert format_relative(datetime(2026, 10, 3, 9, 0), NOW) == "Oct 3"

    def test_other_year_includes_the_year(self):
        assert format_relative("2025-12-27 20:35:19.327064", NOW) == "Dec 27, 2025"

    def test_small_clock_skew_is_just_now(self):
        assert format_relative(NOW + timedelta(seconds=30), NOW) == "just now"

    def test_future_time_shows_absolute_time(self):
        assert format_relative(NOW + timedelta(hours=2), NOW) == "Nov 17 · 14:00"

    def test_default_now_is_local_time(self):
        assert format_relative(datetime.now() - timedelta(hours=2)) == "2 h ago"

    def test_aware_on_both_sides(self):
        now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
        assert format_relative("2026-11-17T10:00:00Z", now) == "2 h ago"
        assert format_relative("2026-11-17T10:00:00+00:00", now) == "2 h ago"
        assert format_relative("2026-11-17T10:00:00+0000", now) == "2 h ago"
        assert format_relative("2026-11-17T11:00:00+01:00", now) == "2 h ago"
        assert format_relative("2026-11-17T11:00:00+00:00", now) == "1 h ago"

    def test_aware_value_with_naive_now(self):
        aware = (NOW - timedelta(hours=2)).astimezone(timezone.utc)
        assert format_relative(aware, NOW) == "2 h ago"
        assert format_relative(aware.isoformat(), NOW) == "2 h ago"

    def test_naive_value_with_aware_now(self):
        aware_now = NOW.astimezone()  # the same instant, local and aware
        assert format_relative("2026-11-17 10:00:00", aware_now) == "2 h ago"

    def test_date_value(self):
        assert format_relative(date(2026, 11, 15), NOW) == "2 d ago"

    @pytest.mark.parametrize(
        "fraction", [".3", ".32", ".327", ".3270", ".32706", ".327064", ".3270641"]
    )
    def test_any_fraction_length_parses(self, fraction):
        assert format_relative(f"2026-11-17 09:59:00{fraction}", NOW) == "2 h ago"

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "not a date", "2025-13-45 10:00:00", "yesterday", 12345, object()],
    )
    def test_missing_or_bad_input_is_empty(self, value):
        assert format_relative(value, NOW) == ""


# ---------------------------------------------------------------------------
# format_short_datetime
# ---------------------------------------------------------------------------


class TestFormatShortDatetime:
    def test_db_string_from_the_spec(self):
        # webui-dashboard-overview "Recent playlist date".
        assert format_short_datetime("2025-12-27 20:35:19.327064") == "Dec 27 · 20:35"

    @pytest.mark.parametrize(
        "value",
        [
            datetime(2025, 12, 27, 20, 35, 19, 327064),
            "2025-12-27T20:35:19",
            "2025-12-27 20:35",
            " 2025-12-27 20:35:19.327 ",
        ],
    )
    def test_no_seconds_or_microseconds(self, value):
        assert format_short_datetime(value) == "Dec 27 · 20:35"

    def test_single_digit_day_and_padded_time(self):
        assert format_short_datetime(datetime(2026, 1, 5, 9, 5)) == "Jan 5 · 09:05"

    def test_with_year(self):
        assert (
            format_short_datetime("2025-12-27 20:35:19.327064", with_year=True)
            == "Dec 27, 2025 · 20:35"
        )

    def test_date_value_is_midnight(self):
        assert format_short_datetime(date(2025, 12, 27)) == "Dec 27 · 00:00"

    def test_aware_value_is_shown_in_local_time(self):
        aware = datetime(2025, 12, 27, 20, 35, tzinfo=timezone.utc)
        local = aware.astimezone()
        months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
        expected = f"{months[local.month - 1]} {local.day} · {local:%H:%M}"
        assert format_short_datetime(aware) == expected
        assert format_short_datetime("2025-12-27T20:35:00Z") == expected

    @pytest.mark.parametrize(
        "value", [None, "", "garbage", "2025-02-30 10:00:00", "12/27/2025 20:35", 42]
    )
    def test_missing_or_bad_input_is_empty(self, value):
        assert format_short_datetime(value) == ""


# ---------------------------------------------------------------------------
# friendly_error (moved from ui/states/_settings_testing.py)
# ---------------------------------------------------------------------------


class TestFriendlyError:
    def test_old_import_path_still_works(self):
        from plexmix.ui.states import _settings_testing

        assert _settings_testing._friendly_error is friendly_error
        assert _friendly_error is friendly_error

    def test_raw_gemini_400_invalid_key(self):
        result = friendly_error(Exception(GEMINI_400_INVALID_KEY))
        assert result.startswith("Invalid API key")
        assert result == INVALID_API_KEY_MESSAGE
        assert "{" not in result and "INVALID_ARGUMENT" not in result

    def test_gemini_text_from_the_audit_screenshot(self):
        assert friendly_error(Exception(GEMINI_400_AS_SHOWN)).startswith("Invalid API key")

    def test_screenshot_text_is_a_prefix_of_the_full_body(self):
        # The audit text is the full body cut at 117 characters plus "...".
        assert len(GEMINI_400_AS_SHOWN) == 120
        assert GEMINI_400_INVALID_KEY.startswith(GEMINI_400_AS_SHOWN[:-3])

    def test_real_google_genai_client_error(self):
        errors = pytest.importorskip("google.genai.errors")
        body = {
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "status": "INVALID_ARGUMENT",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "API_KEY_INVALID",
                        "domain": "googleapis.com",
                    }
                ],
            }
        }
        exc = errors.ClientError(400, body)
        assert str(exc).startswith("400 INVALID_ARGUMENT. {'error'")
        assert friendly_error(exc).startswith("Invalid API key")

    @pytest.mark.parametrize(
        "message",
        [
            "Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}",
            "Unauthorized",
            "Invalid API key",
            "INVALID API KEY supplied",
            "authentication_error: invalid x-api-key",
            "api key not valid",
            "reason: API_KEY_INVALID",
        ],
    )
    def test_key_rejections_are_plain(self, message):
        assert friendly_error(Exception(message)) == INVALID_API_KEY_MESSAGE

    def test_regex_is_a_real_pattern_not_a_literal(self):
        # The old check looked for the literal text "invalid.*key".
        assert friendly_error(Exception("Invalid credentials: key rejected")) == (
            INVALID_API_KEY_MESSAGE
        )

    def test_other_400_invalid_argument_is_plain(self):
        raw = (
            "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': '* "
            "GenerateContentRequest.model: unexpected model name format', "
            "'status': 'INVALID_ARGUMENT'}}"
        )
        result = friendly_error(Exception(raw))
        assert result == INVALID_REQUEST_MESSAGE
        assert "{" not in result and "INVALID_ARGUMENT" not in result

    def test_400_without_invalid_argument_is_not_rewritten_as_invalid_request(self):
        result = friendly_error(Exception("400 Bad Request: model is required"))
        assert result != INVALID_REQUEST_MESSAGE

    @pytest.mark.parametrize(
        "message, expected_start",
        [
            ("403 Forbidden", "Access denied"),
            ("404 Not Found", "Endpoint not found"),
            ("429 Too Many Requests", "Rate limited"),
            ("Read timed out", "Connection timed out"),
            ("Connection refused", "Could not connect to the server"),
            ("Name or service not known", "Could not resolve the hostname"),
            ("SSL: CERTIFICATE_VERIFY_FAILED", "SSL/TLS error"),
            ("No module named 'cohere'", "Missing Python package: cohere"),
        ],
    )
    def test_existing_mappings_are_unchanged(self, message, expected_start):
        assert friendly_error(Exception(message)).startswith(expected_start)

    @pytest.mark.parametrize(
        "message",
        [
            "Rate limit exceeded",
            "Error code: 429 - rate_limit_error",
            "RateLimitError: slow down",
            "Too Many Requests",
            "429 RESOURCE_EXHAUSTED",
        ],
    )
    def test_rate_limits_are_plain(self, message):
        assert friendly_error(Exception(message)).startswith("Rate limited")

    @pytest.mark.parametrize(
        "message",
        [
            "Unknown error generating embedding",
            "Failed to generate playlist",
            "Could not separate the tracks",
        ],
    )
    def test_words_containing_rate_are_not_rate_limits(self, message):
        # The old check matched the substring "rate" anywhere (gene-rate, sepa-rate)
        # and reported a rate limit that never happened.
        assert friendly_error(Exception(message)) == message

    def test_secrets_are_redacted_before_display(self):
        secret = "sk-fake-sentinel-0000"
        result = friendly_error(Exception(f"boom while using {secret} here"), [secret])
        assert secret not in result
        assert "[redacted]" in result

    def test_short_or_empty_secrets_are_ignored(self):
        assert friendly_error(Exception("abc boom"), ["", None, "abc"]) == "abc boom"

    def test_long_fallback_is_truncated(self):
        result = friendly_error(Exception("x" * 300))
        assert len(result) == 120 and result.endswith("...")

    def test_empty_message_has_a_fallback(self):
        assert friendly_error(Exception("")) == "An unexpected error occurred."


def test_helpers_and_errors_do_not_import_reflex():
    """The pure helpers stay importable by services and the CLI without Reflex."""
    code = (
        "import sys\n"
        "import plexmix.ui.utils.errors, plexmix.ui.utils.helpers\n"
        "assert 'reflex' not in sys.modules, 'reflex imported'\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH")]))
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
