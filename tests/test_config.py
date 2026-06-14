import os

from wechat_remind.config import load_env_file, parse_env_lines


def test_parse_env_lines():
    parsed = parse_env_lines(
        [
            "\ufeffOPENAI_API_KEY=sk-test",
            "# comment",
            "OPENAI_MODEL=gpt-5.5",
            "export OPENAI_BASE_URL='https://example.test/'",
            'BOT_TIMEZONE="Asia/Shanghai"',
            "INVALID",
        ]
    )

    assert parsed == {
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "gpt-5.5",
        "OPENAI_BASE_URL": "https://example.test/",
        "BOT_TIMEZONE": "Asia/Shanghai",
    }


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_MODEL=gpt-5.5\nOPENAI_BASE_URL=https://example.test/\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "already-set")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    loaded = load_env_file(env_file)

    assert loaded == env_file
    assert os.environ["OPENAI_MODEL"] == "already-set"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.test/"


def test_load_env_file_can_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_MODEL=gpt-5.5\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "bad-model")

    load_env_file(env_file, override=True)

    assert os.environ["OPENAI_MODEL"] == "gpt-5.5"
