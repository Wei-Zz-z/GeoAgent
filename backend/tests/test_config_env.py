from __future__ import annotations

from geoagent.config import load_dotenv


def test_load_dotenv_preserves_quotes_inside_table_name(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GEOAGENT_PG_WHITELIST", raising=False)
    source = tmp_path / ".env"
    source.write_text('GEOAGENT_PG_WHITELIST=data."2026_1_change_landuse"\n', encoding="utf-8")

    load_dotenv(source)

    import os

    assert os.environ["GEOAGENT_PG_WHITELIST"] == 'data."2026_1_change_landuse"'
