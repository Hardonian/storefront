"""Jinja2 template configuration and rendering helpers."""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings

jinja_env = Environment(
    loader=FileSystemLoader(settings.templates_dir),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)


def render_template(template_name: str, **context) -> str:
    """Render a Jinja2 template with context."""
    template = jinja_env.get_template(template_name)
    return template.render(**context)
