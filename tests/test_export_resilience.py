"""Regression coverage: exports must not silently write an incomplete snapshot
when one or more ``recall`` calls fail, and project sync must fail clearly
instead of publishing incomplete context.

Before this fix, a partial outage was indistinguishable from a genuinely empty
memory category. The exporter swallowed the per-type exception, substituted an
empty list, and replaced the good snapshot without warning.
"""

from unittest.mock import MagicMock

import pytest

import memanto.cli.client.direct_client as direct_mod
import memanto.cli.client.sdk_client as sdk_mod
from memanto.app.services.memory_export_service import MEMORY_TYPE_ORDER

DirectClient = direct_mod.DirectClient
SdkClient = sdk_mod.SdkClient


def _build_client(client_cls, monkeypatch, tmp_path):
    """Construct *client_cls* with session validation stubbed out and
    ``Path.home()`` redirected to *tmp_path*. ``Path`` is the same class
    object everywhere it's imported, so this one patch also covers
    ``MemoryExportService``'s default ``exports_dir`` — export writes and
    ``sync_memory_to_project``'s cache lookup end up at the same
    ``tmp_path/.memanto/exports/`` regardless of which module reads
    ``Path.home()``."""
    module = direct_mod if client_cls is DirectClient else sdk_mod
    monkeypatch.setattr(module.Path, "home", classmethod(lambda cls: tmp_path))

    client = client_cls(api_key="test-key")
    monkeypatch.setattr(
        client, "_get_validated_session_for_agent", lambda agent_id: None
    )
    return client


class TestExportRefusesIncompleteRecall:
    @pytest.mark.parametrize("client_cls", [SdkClient, DirectClient])
    def test_raises_when_every_recall_fails(self, client_cls, monkeypatch, tmp_path):
        client = _build_client(client_cls, monkeypatch, tmp_path)
        monkeypatch.setattr(
            client, "recall", MagicMock(side_effect=ConnectionError("backend down"))
        )

        with pytest.raises(ConnectionError, match="unreachable"):
            client.export_memory_md(agent_id="test-agent")

    @pytest.mark.parametrize("client_cls", [SdkClient, DirectClient])
    def test_partial_failure_refuses_incomplete_export(
        self, client_cls, monkeypatch, tmp_path
    ):
        """One failed type must not be represented as a genuinely empty type."""
        client = _build_client(client_cls, monkeypatch, tmp_path)

        def fake_recall(agent_id, query, limit, type):
            if type == [MEMORY_TYPE_ORDER[0]]:
                raise ConnectionError("flaky")
            return {"memories": [{"content": "ok"}]}

        monkeypatch.setattr(client, "recall", MagicMock(side_effect=fake_recall))

        with pytest.raises(
            ConnectionError,
            match=f"incomplete.*{MEMORY_TYPE_ORDER[0]}|{MEMORY_TYPE_ORDER[0]}.*incomplete",
        ):
            client.export_memory_md(agent_id="test-agent")


class TestSyncUsesCacheFastPath:
    """A cached export is copied without requiring a reachable backend."""

    def test_cache_used_when_backend_down(self, monkeypatch, tmp_path):
        client = _build_client(SdkClient, monkeypatch, tmp_path)

        cache_file = tmp_path / ".memanto" / "exports" / "test-agent_memory.md"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("### Some Memory\n\ngood content\n", encoding="utf-8")

        monkeypatch.setattr(
            client, "recall", MagicMock(side_effect=ConnectionError("backend down"))
        )

        project_dir = tmp_path / "project"
        result = client.sync_memory_to_project(
            agent_id="test-agent", project_dir=str(project_dir)
        )

        client.recall.assert_not_called()
        assert result["source"] == "cache"
        assert result["total_memories"] == 1
        written = (project_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "good content" in written

    def test_raises_when_no_cache_and_backend_down(self, monkeypatch, tmp_path):
        client = _build_client(SdkClient, monkeypatch, tmp_path)
        monkeypatch.setattr(
            client, "recall", MagicMock(side_effect=ConnectionError("backend down"))
        )

        with pytest.raises(ConnectionError):
            client.sync_memory_to_project(
                agent_id="test-agent", project_dir=str(tmp_path / "project")
            )

    @pytest.mark.parametrize("client_cls", [SdkClient, DirectClient])
    def test_okf_sync_raises_on_partial_failure(
        self, client_cls, monkeypatch, tmp_path
    ):
        client = _build_client(client_cls, monkeypatch, tmp_path)
        cache_memory = (
            tmp_path
            / ".memanto"
            / "exports"
            / "test-agent_okf"
            / "memories"
            / "instruction"
            / "keep-this.md"
        )
        cache_memory.parent.mkdir(parents=True)
        cache_memory.write_text(
            "---\ntype: instruction\ntitle: Keep this\n---\n"
            "Never silently discard this instruction.\n",
            encoding="utf-8",
        )

        def fake_recall(agent_id, query, limit, type):
            if type == [MEMORY_TYPE_ORDER[0]]:
                raise ConnectionError("one category is unavailable")
            return {"memories": [{"content": "fresh"}]}

        monkeypatch.setattr(client, "recall", MagicMock(side_effect=fake_recall))

        project_dir = tmp_path / "project"
        with pytest.raises(ConnectionError, match="incomplete"):
            client.sync_okf_to_project(agent_id="test-agent", project_dir=str(project_dir))

        assert not (project_dir / "okf").exists()

    @pytest.mark.parametrize("client_cls", [SdkClient, DirectClient])
    def test_rejects_path_traversal_before_cache_lookup(
        self, client_cls, monkeypatch, tmp_path
    ):
        client = _build_client(client_cls, monkeypatch, tmp_path)

        # Patch get_data_dir in the specific client module to prove it's never reached
        mock_get_data_dir = MagicMock()
        monkeypatch.setattr(f"{client_cls.__module__}.get_data_dir", mock_get_data_dir)

        with pytest.raises(ValueError, match="invalid characters"):
            client.sync_memory_to_project(
                agent_id="../outside", project_dir=str(tmp_path / "project")
            )

        mock_get_data_dir.assert_not_called()
