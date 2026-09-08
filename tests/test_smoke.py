def test_imports():
    import main

    assert main.main


async def test_all_telegram_plugins_load(users):
    from app.telegram import load_plugins_telethon

    await load_plugins_telethon()
