from app import config


def test_token_is_cleaned_of_paste_damage():
    good = "123456789:AAH_example-Token"
    assert config.clean_token(good) == good
    assert config.clean_token(f"  {good}\n") == good
    assert config.clean_token(f'"{good}"') == good
    assert config.clean_token(f"<{good}>") == good
    assert config.clean_token(f"bot{good}") == good
    assert config.clean_token(f"{good}\u200b") == good  # invisible zero-width character
    assert config.clean_token("") == ""


def test_secret_url_and_plain_cleaning():
    assert config.clean_secret(" ab-C_9 \n") == "ab-C_9"
    assert config.clean_url("https://stick.onrender.com/") == "https://stick.onrender.com"
    assert config.clean_url("https://stick.onrender.com/tick?key=abc") == "https://stick.onrender.com"
    assert config.clean_url('  "https://stick.onrender.com"  ') == "https://stick.onrender.com"
    assert config.clean_url("stick.onrender.com") == ""  # no scheme: refuse rather than guess
    assert config.clean_plain(' "abc123"\n') == "abc123"
