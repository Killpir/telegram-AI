from pathlib import Path


def test_bot_service_exists_in_compose() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "  bot:\n" in compose
    assert 'command: ["python", "-m", "app.bot.main"]' in compose


def test_stage2_handlers_are_present() -> None:
    for relative in (
        "app/bot/handlers/start.py",
        "app/bot/handlers/profile.py",
        "app/bot/handlers/help.py",
    ):
        assert Path(relative).is_file()
