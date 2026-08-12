from __future__ import annotations

import pandas as pd

import app


def test_load_data_reuses_session_snapshot_during_editing(monkeypatch):
    state = {}
    calls = []

    def fetch(sheet_name):
        calls.append(sheet_name)
        return pd.DataFrame([{"Status": "New"}])

    monkeypatch.setattr(app.st, "session_state", state)
    monkeypatch.setattr(app, "_fetch_data", fetch)

    first = app.load_data(app.SHEET_GOOGLE)
    first.loc[0, "Status"] = "Reviewing"
    second = app.load_data(app.SHEET_GOOGLE)

    assert calls == [app.SHEET_GOOGLE]
    assert second.loc[0, "Status"] == "New"


def test_invalidate_data_cache_discards_snapshots_and_increments_revision(monkeypatch):
    state = {
        app._snapshot_key(app.SHEET_GOOGLE): pd.DataFrame([{"Status": "New"}]),
        app._snapshot_key(app.SHEET_X): pd.DataFrame([{"Status": "New"}]),
        app._snapshot_key(app.SHEET_HISTORY): pd.DataFrame(),
        "_qoo10_data_revision": 4,
        "unrelated": "keep",
    }
    clear_calls = []

    monkeypatch.setattr(app.st, "session_state", state)
    monkeypatch.setattr(app.st.cache_data, "clear", lambda: clear_calls.append(True))

    app.invalidate_data_cache()

    assert all(app._snapshot_key(sheet) not in state for sheet in (
        app.SHEET_GOOGLE,
        app.SHEET_X,
        app.SHEET_HISTORY,
    ))
    assert state["_qoo10_data_revision"] == 5
    assert state["unrelated"] == "keep"
    assert clear_calls == [True]
