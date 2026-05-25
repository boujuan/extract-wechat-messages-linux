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
