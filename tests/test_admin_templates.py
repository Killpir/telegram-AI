from __future__ import annotations

from pathlib import Path

from app.admin.templating import templates


def test_all_admin_templates_compile() -> None:
    base = Path(__file__).resolve().parents[1] / "app" / "admin" / "templates" / "admin"
    names = sorted(path.name for path in base.glob("*.html"))
    assert "dashboard.html" in names
    assert "login.html" in names
    for name in names:
        templates.env.get_template(f"admin/{name}")
