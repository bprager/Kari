#!/usr/bin/env python3
"""Extend the installed sensor monitor without changing its collector package."""
import kari.monitor as monitor
from kari.cli import main
from terminal_pilot import Reader, render

reader = Reader()
original = monitor.render_dashboard


def render_combined(snapshot, *, width=120, trend_hours=6):
    lines = original(snapshot, width=width, trend_hours=trend_hours)
    values, unreachable = reader.read()
    lines.extend(monitor.RenderLine(text[:max(40,width)], style)
                 for text, style in render(values, unreachable=unreachable))
    return lines


monitor.render_dashboard = render_combined
# cli imports the renderer directly in the installed release.
import kari.cli as cli
if hasattr(cli, 'render_dashboard'):
    cli.render_dashboard = render_combined

if __name__ == '__main__':
    raise SystemExit(main())
