"""v0.3.0 Checkpoint 01: the Light/Dark appearance module (adapted from PDF-Splitter).

Pure checks (palettes, contrast, toggling) need no display. The style-registration
checks build a real Tk interpreter and skip automatically when no display exists.
"""

from __future__ import annotations

import pytest

from webnovel_scraper.ui import appearance
from webnovel_scraper.ui.appearance import (
    DARK,
    DARK_COLORS,
    LIGHT,
    LIGHT_COLORS,
    contrast_ratio,
)


# ── Pure data ────────────────────────────────────────────────────────────────
def test_normalize_and_toggle() -> None:
    assert appearance.normalize_appearance("dark") == DARK
    assert appearance.normalize_appearance("purple") == LIGHT
    assert appearance.normalize_appearance(None) == LIGHT
    assert appearance.toggled(LIGHT) == DARK
    assert appearance.toggled(DARK) == LIGHT
    assert appearance.toggled("garbage") == DARK  # garbage normalizes to Light first


def test_palettes_define_the_same_tokens() -> None:
    assert set(LIGHT_COLORS) == set(DARK_COLORS)
    assert LIGHT_COLORS["is_dark"] is False and DARK_COLORS["is_dark"] is True


@pytest.mark.parametrize("palette", [LIGHT_COLORS, DARK_COLORS], ids=["light", "dark"])
def test_text_meets_wcag_contrast(palette) -> None:
    for background in ("window", "surface", "field"):
        for foreground in ("text", "secondary"):
            assert contrast_ratio(palette[foreground], palette[background]) >= 4.5, (
                foreground, background)
    for semantic in ("warning", "danger", "success", "accent", "link"):
        assert contrast_ratio(palette[semantic], palette["surface"]) >= 4.5, semantic
    # Text on the solid primary (Start) button and on a selection.
    assert contrast_ratio(palette["inverse"], palette["accent"]) >= 4.5
    assert contrast_ratio(palette["selection_text"], palette["selection"]) >= 4.5
    # Disabled text stays legible (WCAG exempts it; we still keep 3:1).
    assert contrast_ratio(palette["disabled"], palette["field_disabled"]) >= 3.0


# ── Style registration (needs a Tk interpreter) ──────────────────────────────
@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display (headless CI)
        pytest.skip(f"no Tk display available: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


def test_bundle_registers_compact_styles_without_touching_the_theme(tk_root) -> None:
    from tkinter import ttk

    style = ttk.Style(tk_root)
    theme = style.theme_use()
    generic_button = style.layout("TButton")
    bundle = appearance.build_bundle(style, LIGHT, platform="win32", root=tk_root)

    assert style.theme_use() == theme  # theme_use() is never called
    assert style.layout("TButton") == generic_button  # generic styles untouched
    assert bundle["ttk_active"] is True
    for key, name in bundle["styles"].items():
        assert name.startswith("Compact."), key
    # Every widget style the window uses has a layout built from the clam clones.
    for key in ("button", "primary_button", "danger_button", "ghost_button", "entry",
                "combobox", "menubutton", "checkbutton", "radiobutton", "labelframe",
                "progressbar", "vscrollbar"):
        assert style.layout(bundle["styles"][key]), key
    # The Website picker is drawn from the same elements as the Novel dropdown.
    picker = str(style.layout(bundle["styles"]["menubutton"]))
    assert "Compact.Combobox.field" in picker and "Compact.Combobox.downarrow" in picker


def test_rebuilding_the_bundle_recolours_the_same_styles_in_place(tk_root) -> None:
    from tkinter import ttk

    style = ttk.Style(tk_root)
    light = appearance.build_bundle(style, LIGHT, platform="win32", root=tk_root)
    images = tk_root._compact_indicator_images
    assert style.lookup("Compact.TFrame", "background") == LIGHT_COLORS["surface"]
    assert style.lookup("Compact.TEntry", "fieldbackground") == LIGHT_COLORS["field"]

    dark = appearance.build_bundle(style, DARK, platform="win32", root=tk_root)
    assert dark["styles"] == light["styles"]  # same names, reconfigured
    assert style.lookup("Compact.TFrame", "background") == DARK_COLORS["surface"]
    assert style.lookup("Compact.TEntry", "fieldbackground") == DARK_COLORS["field"]
    assert style.lookup("Compact.TMenubutton", "fieldbackground") == DARK_COLORS["field"]
    # The check/radio images are redrawn, not replaced, so existing boxes repaint.
    assert tk_root._compact_indicator_images is images


def test_aqua_branch_keeps_native_controls(tk_root) -> None:
    from tkinter import ttk

    style = ttk.Style(tk_root)
    bundle = appearance.build_bundle(style, DARK, platform="darwin", root=tk_root)
    assert bundle["ttk_active"] is False
    assert all(name.startswith("CompactAqua.") for name in bundle["styles"].values())
    assert style.lookup("CompactAqua.TFrame", "background") == DARK_COLORS["surface"]
    combo = ttk.Combobox(tk_root, style=bundle["styles"]["combobox"])
    assert appearance.style_combobox_popdown(combo, bundle) is False  # native list
    # Not an Aqua window system here: the native-appearance call is a safe no-op.
    assert appearance.apply_native_appearance(tk_root, bundle) is False


def test_classic_widgets_are_coloured_by_role(tk_root) -> None:
    import tkinter as tk
    from tkinter import ttk

    bundle = appearance.build_bundle(ttk.Style(tk_root), DARK, platform="win32", root=tk_root)
    text = tk.Text(tk_root)
    applied = appearance.style_tk_widget(text, bundle, "log")
    assert applied["background"] == DARK_COLORS["field"]
    # cget may return a Tcl object rather than a str, so compare the text form.
    assert str(text.cget("foreground")) == DARK_COLORS["text"]
    assert str(text.cget("insertbackground")) == DARK_COLORS["text"]

    menu = tk.Menu(tk_root, tearoff=0)
    appearance.style_tk_widget(menu, bundle, "menu")
    assert str(menu.cget("background")) == DARK_COLORS["field"]
    assert str(menu.cget("activebackground")) == DARK_COLORS["selection"]
    assert str(menu.cget("disabledforeground")) == DARK_COLORS["disabled"]

    with pytest.raises(ValueError):
        appearance.style_tk_widget(text, bundle, "no-such-role")
    assert appearance.style_tk_widget(text, {}, "log") == {}


def test_listeners_are_notified_and_never_raise(tk_root) -> None:
    from tkinter import ttk

    seen: list[str] = []

    def good(bundle):
        seen.append(bundle["appearance"])

    def bad(_bundle):
        raise RuntimeError("a broken listener must not stop the toggle")

    appearance.register_listener(good)
    appearance.register_listener(bad)
    try:
        bundle = appearance.toggle_appearance(
            ttk.Style(tk_root), LIGHT, platform="win32", root=tk_root
        )
    finally:
        appearance.unregister_listener(good)
        appearance.unregister_listener(bad)
    assert bundle["appearance"] == DARK
    assert seen == [DARK]
