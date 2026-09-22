"""`argus pricing refresh` writes into the user's data dir, not the package."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import argus.cli as cli
from argus.pricing.types import ModelPricing, PricingTable


def test_refresh_writes_to_data_dir_and_takes_effect(tmp_path: Path, monkeypatch):
    """REGRESSION (H3): the refresh wrote into site-packages/argus/pricing,
    which an upgrade wipes and a read-only install rejects."""
    fresh = PricingTable(
        version="2099-01-01",
        models={"claude-new": ModelPricing(input=1, output=2, cache_read=0.1)},
    )
    monkeypatch.setattr(cli, "fetch_litellm_table", lambda: fresh)
    before = sorted(p.name for p in Path(cli.__file__).parent.joinpath("pricing").glob("*.json"))

    res = CliRunner().invoke(
        cli.app, ["pricing", "refresh", "--data-dir", str(tmp_path)], input="y\n"
    )
    assert res.exit_code == 0, res.output
    assert (tmp_path / "pricing" / "2099-01-01.json").is_file()
    after = sorted(p.name for p in Path(cli.__file__).parent.joinpath("pricing").glob("*.json"))
    assert after == before  # nothing written into the package

    from argus.pricing.load import load_pricing_table
    assert load_pricing_table(user_dir=tmp_path / "pricing").version == "2099-01-01"


def test_refresh_reports_unwritable_data_dir(tmp_path: Path, monkeypatch):
    fresh = PricingTable(version="2099-01-01", models={"m": ModelPricing(input=1, output=1, cache_read=0)})
    monkeypatch.setattr(cli, "fetch_litellm_table", lambda: fresh)
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where the data dir should be -> mkdir fails

    res = CliRunner().invoke(cli.app, ["pricing", "refresh", "--data-dir", str(blocker)], input="y\n")
    assert res.exit_code == 1
    assert "Could not write" in res.output
    assert "Traceback" not in res.output
