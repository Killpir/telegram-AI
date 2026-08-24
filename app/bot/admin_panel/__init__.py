"""Telegram-native administration package.

Import router explicitly from ``app.bot.admin_panel.router`` so lightweight helpers can be tested
without importing aiogram in environments where runtime dependencies are intentionally absent.
"""
