"""config.json loading must survive hand edits (N-MAINT-1, review 2 finding 5)."""

import json

from hub import config


def test_wrong_types_fall_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"custom_prompt": None, "web_enabled": "false", "port": True, "model": "haiku"}))
    cfg = config.load(p)
    assert cfg["custom_prompt"] == "" and cfg["web_enabled"] is True and cfg["port"] == 8765
    assert cfg["model"] == "haiku"  # valid keys still load


def test_non_object_json_gives_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[1, 2]")
    assert config.load(p) == config.DEFAULTS


def test_broken_json_gives_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    assert config.load(p) == config.DEFAULTS
