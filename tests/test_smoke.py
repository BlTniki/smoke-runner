"""Smoke tests for the project scaffold."""

from __future__ import annotations

import pytest

import smoke_runner
from smoke_runner.__main__ import main


def test_package_imports() -> None:
    assert smoke_runner.__version__ == "0.1.0"


def test_cli_help_does_not_require_bot_token(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 0
    assert "usage: smoke-runner" in captured.out
    assert captured.err == ""
