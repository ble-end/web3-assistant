from assistant.memory.safety import looks_like_secret

SEED = "abandon ability able about above absent absorb abstract absurd abuse access accident"


def test_seed_phrase_is_blocked():
    assert looks_like_secret(SEED)


def test_private_key_is_blocked():
    assert looks_like_secret("0x" + "a" * 64)
    assert looks_like_secret("a" * 64)


def test_api_keys_and_bot_tokens_are_blocked():
    assert looks_like_secret("sk-ant-abc123def456ghi789xyz")
    assert looks_like_secret("123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789")


def test_normal_project_text_is_allowed():
    assert not looks_like_secret("Contract is 0x" + "a" * 40)
    assert not looks_like_secret("We launch on Base next month and need 5k Telegram members")
    assert not looks_like_secret("short lowercase sentence")
