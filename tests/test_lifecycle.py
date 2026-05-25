"""Lifecycle tests — read-only checks; don't actually kill WeChat in CI/dev."""
from wxextract import lifecycle


def test_wechat_running_returns_list_of_ints():
    pids = lifecycle.wechat_running()
    assert isinstance(pids, list)
    assert all(isinstance(p, int) and p > 0 for p in pids)


def test_main_pid_consistency():
    """If main pid exists, it's also in wechat_running()."""
    mp = lifecycle.main_wechat_pid()
    if mp is None:
        return
    assert mp in lifecycle.wechat_running()


def test_close_returns_true_when_nothing_to_close(monkeypatch):
    monkeypatch.setattr(lifecycle, "main_wechat_pid", lambda: None)
    monkeypatch.setattr(lifecycle, "wechat_running", lambda: [])
    assert lifecycle.close_wechat(timeout=0.1) is True
