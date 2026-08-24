from app.bot.utils import split_telegram_text


def test_long_response_is_split_under_telegram_limit() -> None:
    text = ("word " * 2000).strip()
    chunks = split_telegram_text(text, max_length=1000)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 1000 for chunk in chunks)
    assert "word" in chunks[0]
    assert "word" in chunks[-1]
