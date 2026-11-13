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


def test_prompts_keep_only_text_for_builtin_personalities(tmp_path):  # M3 settings page
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"prompts": {"fun": "Pirate.", "evil": "x", "straight": 5}}), encoding="utf-8")
    assert config.load(path)["prompts"] == {"fun": "Pirate."}


def test_defaults_are_not_shared_between_loads(tmp_path):
    a = config.load(tmp_path / "none.json")
    a["prompts"]["fun"] = "changed"
    assert config.load(tmp_path / "none.json")["prompts"] == {}


def test_emoji_avatar_saved_readable(tmp_path):  # F-PERS-4, N-MAINT-1
    path = tmp_path / "config.json"
    config.save({"avatar": "🦊"}, path)
    assert "🦊" in path.read_text(encoding="utf-8")
