"""Shared visual language for new UtaSyncNext UI surfaces.

Existing screens are intentionally not rewritten in one pass. New surfaces
should import these tokens instead of inventing another palette or spacing
system; this keeps the gallery redesign visually compatible with the main
workspace while allowing the current UI to remain reversible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesignTokens:
    # Surfaces: black workspace, cool navy panels, and quiet controls.
    surface_app: str = "#000000"
    surface_sidebar: str = "#0A0A0A"
    surface_card: str = "#151515"
    surface_panel: str = "#111722"
    surface_input: str = "#121212"
    surface_control: str = "#182231"
    surface_control_hover: str = "#24344A"
    surface_preview: str = "#050505"

    # Structure and state accents.
    border_subtle: str = "#252525"
    border_cool: str = "#27334A"
    border_control: str = "#3A4C61"
    accent_primary: str = "#1DB954"
    accent_primary_hover: str = "#1ED760"
    accent_info: str = "#38BDF8"
    accent_warning: str = "#EAB308"
    accent_danger: str = "#991B1B"
    accent_danger_hover: str = "#7F1D1D"

    # Text hierarchy.
    text_primary: str = "#FFFFFF"
    text_secondary: str = "#D9E1F2"
    text_muted: str = "#888888"
    text_hint: str = "#666666"

    # Typography families used by the existing UI.
    font_ui: str = "Microsoft YaHei"
    font_display: str = "Segoe UI Black"
    font_mono: str = "Consolas"

    # Geometry scale. New screens should use these values or multiples of them.
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 20
    space_6: int = 28
    space_7: int = 40
    radius_control: int = 7
    radius_card: int = 10
    radius_panel: int = 12


UTASYNC_TOKENS = DesignTokens()

