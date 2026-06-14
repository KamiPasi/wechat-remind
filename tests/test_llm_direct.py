from wechat_remind.llm import normalize_openai_base_url


def test_normalize_openai_base_url_appends_v1():
    assert normalize_openai_base_url("https://vip.zen-ai.top/") == "https://vip.zen-ai.top/v1"
    assert normalize_openai_base_url("https://api.example.com/v1") == "https://api.example.com/v1"
