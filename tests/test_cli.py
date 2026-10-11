from pathlib import Path

from typer.testing import CliRunner

from siterag.cli import app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def write_settings(tmp_path: Path) -> Path:
    settings = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    data_root = (tmp_path / "data").as_posix()
    path = tmp_path / "settings.yaml"
    path.write_text(settings.replace("data_root: data", f"data_root: {data_root}"), "utf-8")
    return path


def test_db_upgrade_then_status(tmp_path: Path) -> None:
    opts = ["--site", "fnx", "--settings", str(write_settings(tmp_path)),
            "--sites-dir", str(ROOT / "sites")]
    result = runner.invoke(app, ["db", "status", *opts])
    assert result.exit_code == 1  # no database yet

    result = runner.invoke(app, ["db", "upgrade", *opts])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "fnx" / "state.db").is_file()

    result = runner.invoke(app, ["db", "status", *opts])
    assert result.exit_code == 0, result.output
    assert "Pages: 0 total, 0 active" in result.output
    assert "Chunks: 0" in result.output
    assert "Last run: none" in result.output


def test_unknown_site_reports_config_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["db", "upgrade", "--site", "nope", "--sites-dir", str(ROOT / "sites"),
              "--settings", str(write_settings(tmp_path))]
    )
    assert result.exit_code == 2
    assert "not found" in result.output
