"""CLI argument parsing regression tests."""
from wxextract.cli import _inject_default_subcommand, build_parser


def _parse(argv):
    return build_parser().parse_args(_inject_default_subcommand(argv))


def test_alias_without_subcommand_dispatches_to_run():
    args = _parse(["--alias", "foo_42", "--format", "txt-b"])
    assert args.command == "run"
    assert args.alias == "foo_42"
    assert args.format == "txt-b"


def test_top_level_workspace_preserved_before_run_flags():
    args = _parse(["--workspace", "/tmp/ws", "--alias", "x"])
    assert args.workspace == "/tmp/ws"
    assert args.command == "run"
    assert args.alias == "x"


def test_explicit_subcommand_passes_through():
    for sub in ("status", "list", "resnap", "run", "render"):
        args = _parse([sub])
        assert args.command == sub


def test_render_subcommand_with_alias():
    args = _parse(["render", "--alias", "alice"])
    assert args.command == "render"
    assert args.alias == "alice"


def test_resnap_force_flag():
    args = _parse(["resnap", "--force"])
    assert args.command == "resnap"
    assert args.force is True


def test_run_force_flag():
    args = _parse(["--force", "--alias", "x"])
    assert args.command == "run"
    assert args.force is True


def test_squash_and_redact_flags():
    args = _parse(["--alias", "x", "--squash-emoji", "--redact"])
    assert args.squash_emoji is True
    assert args.redact is True


def test_chunk_token_format():
    args = _parse(["--alias", "x", "--chunk", "tokens:5000"])
    assert args.chunk == "tokens:5000"


def test_bare_invocation_dispatches_to_run():
    """`wxextract` with no args at all defaults to `run`."""
    args = _parse([])
    assert args.command == "run"
    assert args.alias is None
    assert args.all_contacts is False


def test_top_level_only_flag_defaults_to_run():
    """Regression: `wxextract -v` used to crash with AttributeError."""
    args = _parse(["-v"])
    assert args.command == "run"
    assert args.verbose is True
    # all the run-specific defaults must be available so _cmd_render doesn't AttributeError
    assert args.alias is None
    assert args.format == "txt-b,jsonl,xml"
    assert args.all_contacts is False


def test_quiet_only_defaults_to_run():
    args = _parse(["-q"])
    assert args.command == "run"
    assert args.quiet is True


def test_workspace_only_defaults_to_run():
    args = _parse(["--workspace", "/tmp/x"])
    assert args.command == "run"
    assert args.workspace == "/tmp/x"


# ── `wxextract keys` subcommand (issue #1: elevate only the key step) ──────

def test_parser_has_keys_subcommand():
    from wxextract.cli import build_parser

    args = build_parser().parse_args(["keys", "--force"])
    assert args.command == "keys"
    assert args.force is True


def test_cmd_keys_discover_failure_returns_3(monkeypatch, tmp_path):
    import logging
    from types import SimpleNamespace

    import wxextract.discover as disc
    from wxextract import cli

    def boom(*a, **k):
        raise RuntimeError("no WeChat data root found")

    monkeypatch.setattr(disc, "discover", boom)
    rc = cli._cmd_keys(SimpleNamespace(force=False, account_dir=None),
                       tmp_path, logging.getLogger("t"))
    assert rc == 3


def test_cmd_keys_success_prints_next_step(monkeypatch, tmp_path, capsys):
    import logging
    from types import SimpleNamespace

    from wxextract import cli

    monkeypatch.setattr(cli, "_recover_keys", lambda d, ws, ui=None, force=False: {"a.db": "00" * 32})
    monkeypatch.setattr("wxextract.discover.discover", lambda *a, **k: SimpleNamespace(
        install_kind="aur", binary_path="/opt/wechat/wechat", launch_cmd=["/usr/bin/wechat"]))
    rc = cli._cmd_keys(SimpleNamespace(force=False, account_dir=None),
                       tmp_path, logging.getLogger("t"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 keys recovered" in out
    assert "without sudo" in out
