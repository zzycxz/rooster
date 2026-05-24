"""Tests for SessionStore atomic write and session lifecycle."""

import json

import pytest

from sessions.store import SessionStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a SessionStore backed by a temp directory."""
    store = SessionStore(storage_dir=str(tmp_path))
    # Clear any sessions from __init__ (the global singleton may have pre-existing state)
    store._sessions.clear()
    return store


class TestAtomicWrite:
    """Verify session persistence uses atomic write (tmp + rename)."""

    def test_save_creates_json_file(self, tmp_store):
        session = tmp_store.create_session("test-1")
        session.add_message("user", "hello")

        file_path = tmp_store.storage_dir / "test-1.json"
        assert file_path.exists()

    def test_save_no_tmp_leftover(self, tmp_store):
        """After a successful save, no .json.tmp file should remain."""
        session = tmp_store.create_session("test-2")
        tmp_store.save_session("test-2")

        tmp_files = list(tmp_store.storage_dir.glob("*.json.tmp"))
        assert len(tmp_files) == 0, f"Leftover tmp files: {tmp_files}"

    def test_saved_data_is_valid_json(self, tmp_store):
        session = tmp_store.create_session("test-3")
        session.add_message("user", "test content")
        tmp_store.save_session("test-3")

        file_path = tmp_store.storage_dir / "test-3.json"
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["session_id"] == "test-3"
        assert len(data["history"]) == 1
        assert data["history"][0]["content"] == "test content"

    def test_reload_from_disk(self, tmp_store):
        """Session should survive a reload from disk."""
        session = tmp_store.create_session("test-4")
        session.add_message("user", "persistent")
        tmp_store.save_session("test-4")

        # Simulate restart: create a new store from the same directory
        store2 = SessionStore(storage_dir=str(tmp_store.storage_dir))
        restored = store2.get_session("test-4")
        assert restored is not None
        assert restored.session_id == "test-4"
        assert len(restored.history) == 1
        assert restored.history[0].content == "persistent"

    def test_tmp_cleanup_on_load(self, tmp_store):
        """Stale .json.tmp files should be cleaned up on load."""
        stale_tmp = tmp_store.storage_dir / "stale.json.tmp"
        stale_tmp.write_text("not valid json", encoding="utf-8")

        # Create a new store from the same dir — should clean up the stale tmp
        store2 = SessionStore(storage_dir=str(tmp_store.storage_dir))
        assert not stale_tmp.exists(), "Stale .json.tmp should be removed on load"


class TestSessionLifecycle:
    """Verify get_or_create and basic session operations."""

    def test_get_or_create_new(self, tmp_store):
        session = tmp_store.get_or_create("new-session")
        assert session.session_id == "new-session"
        assert session in tmp_store._sessions.values()

    def test_get_or_create_existing(self, tmp_store):
        original = tmp_store.get_or_create("dup-session")
        original.add_message("user", "first")

        # Second call should return the same session
        same = tmp_store.get_or_create("dup-session")
        assert same is original
        assert len(same.history) == 1

    def test_get_nonexistent_returns_none(self, tmp_store):
        assert tmp_store.get_session("nope") is None

    def test_list_sessions(self, tmp_store):
        tmp_store.create_session("s1")
        tmp_store.create_session("s2")
        sessions = tmp_store.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_clear_memory_only(self, tmp_store):
        """clear() should empty memory but keep disk files."""
        session = tmp_store.create_session("persist")
        file_path = tmp_store.storage_dir / "persist.json"
        assert file_path.exists()

        tmp_store.clear()
        assert len(tmp_store._sessions) == 0
        assert file_path.exists(), "Disk file should survive clear()"


class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_delete_removes_from_memory_and_disk(self, tmp_store):
        tmp_store.create_session("to-delete")
        file_path = tmp_store.storage_dir / "to-delete.json"
        assert file_path.exists()

        await tmp_store.delete_session("to-delete")
        assert "to-delete" not in tmp_store._sessions
        assert not file_path.exists()
