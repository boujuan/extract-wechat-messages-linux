from wxextract import __version__
from wxextract.cli import build_parser


def test_version_is_set():
    assert __version__


def test_parser_help_does_not_crash():
    parser = build_parser()
    help_text = parser.format_help()
    assert "wxextract" in help_text


def test_status_subcommand_runs(capsys):
    from wxextract.cli import main
    rc = main(["status"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "wxextract" in captured.out
