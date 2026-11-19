"""Tests for the indexer startup trigger (REINDEX_ON_START)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import scrapper_legislatie_just.scheduler as scheduler


class _FakeScheduler:
    def __init__(self, timezone):
        self.timezone = timezone
        self.jobs: list[dict] = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})

    def start(self):
        pass

    def shutdown(self, wait=True):
        pass


@pytest.fixture
def stub_indexer(monkeypatch):
    calls: list[dict] = []

    def fake_reindex(**kwargs):
        calls.append(kwargs)
        return {"documents": 1}

    monkeypatch.setattr(scheduler, "reindex", fake_reindex)
    monkeypatch.setattr(scheduler, "BlockingScheduler", _FakeScheduler)
    monkeypatch.setattr(scheduler, "CronTrigger", lambda **kwargs: kwargs)
    return calls


def test_reindex_on_start_triggers_once(stub_indexer, monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: SimpleNamespace(reindex_on_start=True, reindex_ocr=False),
    )
    scheduler.main()
    assert stub_indexer == [{"force": False, "ocr": False}]


def test_reindex_on_start_defaults_to_off(stub_indexer, monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: SimpleNamespace(reindex_on_start=False, reindex_ocr=True),
    )
    scheduler.main()
    assert stub_indexer == []
