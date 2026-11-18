"""Tests for the process-local staged-secret registry (``plexmix.ui.secret_drafts``)."""

import copy
import pickle
import threading

import pytest

from plexmix.ui.secret_drafts import ACTION_CLEAR, ACTION_REPLACE, SecretDrafts, secret_drafts

SECRET_A = "SENTINEL-DRAFT-A-0000"
SECRET_B = "SENTINEL-DRAFT-B-0000"


class TestStageAndGet:
    def test_stage_then_get_and_action(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        assert drafts.get("tok-A", "plex_token") == SECRET_A
        assert drafts.action("tok-A", "plex_token") == ACTION_REPLACE

    def test_nothing_staged(self):
        drafts = SecretDrafts()
        assert drafts.get("tok-A", "plex_token") is None
        assert drafts.action("tok-A", "plex_token") == ""
        assert drafts.staged_fields("tok-A") == {}

    def test_mark_clear_has_no_value(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "ai_custom_api_key", SECRET_A)
        drafts.mark_clear("tok-A", "ai_custom_api_key")
        assert drafts.action("tok-A", "ai_custom_api_key") == ACTION_CLEAR
        assert drafts.get("tok-A", "ai_custom_api_key") is None

    def test_restage_replaces_value(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-A", "plex_token", SECRET_B)
        assert drafts.get("tok-A", "plex_token") == SECRET_B

    def test_staged_fields_reports_actions_only(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.mark_clear("tok-A", "ai_local_auth_token")
        fields = drafts.staged_fields("tok-A")
        assert fields == {"plex_token": ACTION_REPLACE, "ai_local_auth_token": ACTION_CLEAR}
        assert SECRET_A not in repr(fields)


class TestPerTokenIsolation:
    def test_tokens_do_not_see_each_other(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-B", "plex_token", SECRET_B)
        assert drafts.get("tok-A", "plex_token") == SECRET_A
        assert drafts.get("tok-B", "plex_token") == SECRET_B
        assert drafts.get("tok-C", "plex_token") is None

    def test_discard_one_token_keeps_the_other(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-B", "plex_token", SECRET_B)
        drafts.discard("tok-A")
        assert drafts.get("tok-A", "plex_token") is None
        assert drafts.get("tok-B", "plex_token") == SECRET_B


class TestDiscard:
    def test_discard_one_field(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-A", "ai_api_key", SECRET_B)
        drafts.discard("tok-A", "plex_token")
        assert drafts.action("tok-A", "plex_token") == ""
        assert drafts.get("tok-A", "ai_api_key") == SECRET_B

    def test_discard_all_fields(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.mark_clear("tok-A", "ai_custom_api_key")
        drafts.discard("tok-A")
        assert drafts.staged_fields("tok-A") == {}
        assert len(drafts) == 0

    def test_discard_unknown_is_a_noop(self):
        drafts = SecretDrafts()
        drafts.discard("nobody")
        drafts.discard("nobody", "plex_token")
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.discard("tok-A", "not_staged")
        assert drafts.get("tok-A", "plex_token") == SECRET_A


class TestReap:
    def test_reap_by_token_list(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-B", "plex_token", SECRET_B)
        drafts.stage("tok-C", "ai_api_key", SECRET_A)
        drafts.reap(["tok-A", "tok-C", "tok-unknown"])
        assert drafts.staged_fields("tok-A") == {}
        assert drafts.staged_fields("tok-C") == {}
        assert drafts.get("tok-B", "plex_token") == SECRET_B

    def test_reap_accepts_any_iterable(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.reap(t for t in ("tok-A",))
        assert len(drafts) == 0
        drafts.stage("tok-B", "plex_token", SECRET_B)
        drafts.reap(frozenset())
        assert len(drafts) == 1

    def test_module_singleton_has_reap(self):
        assert isinstance(secret_drafts, SecretDrafts)
        secret_drafts.reap([])  # the signature another component relies on

    def test_tokens_lists_sessions_holding_drafts(self):
        """The liveness sweep tracks these tokens, including ones that own no job."""
        drafts = SecretDrafts()
        assert drafts.tokens() == frozenset()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.mark_clear("tok-B", "ai_custom_api_key")
        drafts.stage("tok-C", "ai_api_key", SECRET_B)
        drafts.discard("tok-C", "ai_api_key")
        tokens = drafts.tokens()
        assert tokens == frozenset({"tok-A", "tok-B"})
        assert isinstance(tokens, frozenset)
        assert SECRET_A not in repr(tokens)


class TestRedaction:
    def test_repr_and_str_never_show_values(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        drafts.stage("tok-B", "ai_api_key", SECRET_B)
        for text in (repr(drafts), str(drafts), f"{drafts}", "%s" % drafts):
            assert SECRET_A not in text
            assert SECRET_B not in text
        assert "redacted" in repr(drafts)

    def test_internal_entries_are_redacted_too(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        assert SECRET_A not in repr(drafts._drafts)

    def test_refuses_pickling_and_copying(self):
        drafts = SecretDrafts()
        drafts.stage("tok-A", "plex_token", SECRET_A)
        with pytest.raises(TypeError):
            pickle.dumps(drafts)
        with pytest.raises(TypeError):
            pickle.dumps(drafts._drafts)
        with pytest.raises(TypeError):
            copy.deepcopy(drafts)


class TestNotReferencedFromState:
    """``pickle.dumps(secret_drafts)`` is never needed: no state references it."""

    def test_settings_state_holds_no_registry_or_value(self, monkeypatch, tmp_path):
        from tests.ui._secrets_support import isolate, make_state

        isolate(monkeypatch, tmp_path)
        state = make_state("tok-pickle")
        state.stage_secret("plex_token", SECRET_A)
        assert secret_drafts.get("tok-pickle", "plex_token") == SECRET_A
        try:
            for name, value in list(vars(state).items()) + [
                (n, getattr(type(state), n, None)) for n in dir(type(state))
            ]:
                assert not isinstance(value, SecretDrafts), name
            # What Reflex would persist: the whole instance dict, backend vars included.
            blob = pickle.dumps(dict(vars(state)))
            assert SECRET_A.encode() not in blob
        finally:
            secret_drafts.discard("tok-pickle")


class TestThreadSafety:
    def test_concurrent_stage_and_discard(self):
        drafts = SecretDrafts()

        def worker(token: str) -> None:
            for i in range(200):
                drafts.stage(token, f"f{i % 5}", SECRET_A)
                drafts.action(token, f"f{i % 5}")
                if i % 7 == 0:
                    drafts.discard(token)
            drafts.reap([token])

        threads = [threading.Thread(target=worker, args=(f"tok-{n}",)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(drafts) == 0
