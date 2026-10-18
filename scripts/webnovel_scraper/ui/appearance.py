"""Light/Dark appearance: one compact ttk style catalogue, classic-Tk colouring and a
state-preserving refresh path.

Provenance: adapted from PDF-Splitter ``scripts/pdf_splitter/appearance.py`` (the
author's local working copy supplied 2026-10-17, newer than GitHub ``edcbd4f``; this
file is identical in both), which was itself adapted from the Audiobook Creation Tool
v0.6.6 ``scripts/Universal/shared/appearance.py``. Changes for the scraper:

* the appearance constants come from :mod:`webnovel_scraper.ui.settings`;
* removed the Treeview, Spinbox and Inline-button styles (the scraper has none of
  those widgets) and the Treeview row-height metric;
* added ``Compact.TMenubutton``, field-styled like the Combobox, for the Website picker
  (a Menubutton + Menu, because a Combobox cannot grey out single entries);
* :func:`style_tk_widget` gained a ``menu`` role for that picker's classic ``tk.Menu``.

Why a clam clone under our own prefix
-------------------------------------
Windows' native ``vista`` theme ignores ``-background``/``-foreground`` on its native
elements. The ``Compact.*`` styles therefore clone ``clam``'s recolourable elements
under this module's own prefix and give every style a layout built from the clones.
The base theme and every generic style (``TButton``, ``TEntry`` ...) are never
touched, and ``style.theme_use()`` is never called.

State-preserving refresh
------------------------
``ttk.Style.configure`` mutates a *named style* process-wide, so calling
:func:`build_bundle` again with a new appearance reconfigures the same names in place
and every existing widget repaints without being rebuilt: values, selections, focus
and a running scrape are untouched. Two things ttk styling cannot reach are handled
explicitly: classic Tk widgets (:func:`style_tk_widget`) and a raw ``Toplevel``
background (:func:`register_listener`, or the owner re-colouring it directly).

macOS keeps native Aqua controls and layouts: only semantic surface/label colours are
configured, and the window's native appearance is set to match (``aqua``/``darkaqua``).
Nothing here imports a Windows-only module or imitates Windows window chrome.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

from .settings import APPEARANCES, DARK, DEFAULT_APPEARANCE, LIGHT

__all__ = [
    "LIGHT", "DARK", "APPEARANCES", "DEFAULT_APPEARANCE", "STYLE_PREFIX",
    "LIGHT_COLORS", "DARK_COLORS", "METRICS",
    "normalize_appearance", "toggled", "build_bundle", "style_tk_widget",
    "style_combobox_popdown", "apply_native_appearance", "register_listener",
    "unregister_listener", "notify_listeners", "toggle_appearance",
    "contrast_ratio", "style_name",
]

#: Every ttk style registered on Windows begins with this prefix.
STYLE_PREFIX = "Compact"
_P = STYLE_PREFIX
_AQUA_STYLE_PREFIX = "CompactAqua"


def normalize_appearance(value: object) -> str:
    """``value`` if it is a known appearance, otherwise the documented default."""
    return value if value in APPEARANCES else DEFAULT_APPEARANCE


def toggled(value: object) -> str:
    """The other appearance. Pure data; persistence is the caller's job."""
    return DARK if normalize_appearance(value) == LIGHT else LIGHT


# ---------------------------------------------------------------------------
# palettes and metrics
# ---------------------------------------------------------------------------

#: Close to the classic Windows presentation: a #f0f0f0 panel, near-white bordered
#: buttons, white fields. The accent keeps >= 4.5:1 against the grey surface.
LIGHT_COLORS: dict[str, object] = {
    "window": "#f0f0f0", "surface": "#f0f0f0", "elevated": "#fdfdfd",
    "muted": "#e9edf3", "border": "#c9c9c9", "divider": "#d9d9d9",
    "text": "#1a1a1a", "secondary": "#555555", "disabled": "#6b6b6b",
    "inverse": "#ffffff",
    "accent": "#1860c0", "accent_hover": "#2f74d0", "accent_pressed": "#134f9e",
    "accent_soft": "#dce8fb", "focus": "#1860c0", "link": "#1860c0",
    "success": "#166b2e", "warning": "#744300", "danger": "#b42424",
    "danger_hover": "#c83a3a", "danger_pressed": "#931c1c",
    "field": "#ffffff", "field_disabled": "#f4f4f4", "field_border": "#adadad",
    "selection": "#1860c0", "selection_text": "#ffffff",
    "selection_inactive": "#cfe0f7",
    "scroll_trough": "#eeeeee", "scroll_thumb": "#c4c4c4",
    "scroll_thumb_hover": "#adadad",
    "is_dark": False,
}

#: The same UI with a dark appearance, not a second design system.
DARK_COLORS: dict[str, object] = {
    "window": "#1e1f22", "surface": "#26282c", "elevated": "#2d2f34",
    "muted": "#20242b", "border": "#3a3c41", "divider": "#33353a",
    "text": "#e7e8ea", "secondary": "#a6a9ae", "disabled": "#8c9096",
    "inverse": "#101113",
    "accent": "#5b9bf7", "accent_hover": "#7bacf8", "accent_pressed": "#3f80dd",
    "accent_soft": "#233551", "focus": "#5b9bf7", "link": "#7bacf8",
    "success": "#57b975", "warning": "#d3a94a", "danger": "#e57a72",
    "danger_hover": "#ec8f88", "danger_pressed": "#c24f48",
    "field": "#1c1d20", "field_disabled": "#26282c", "field_border": "#44474d",
    "selection": "#3f6fb0", "selection_text": "#ffffff",
    "selection_inactive": "#33475f",
    "scroll_trough": "#232427", "scroll_thumb": "#42454b",
    "scroll_thumb_hover": "#54585f",
    "is_dark": True,
}

_PALETTES = {LIGHT: LIGHT_COLORS, DARK: DARK_COLORS}

#: Spacing tokens (appearance-independent: only colours differ between Light and Dark).
#: Entries listed in ``_PIXEL_METRICS`` are multiplied by the display scaling factor.
METRICS: dict[str, object] = {
    "gap_xs": 2, "gap_sm": 4, "gap_md": 8, "gap_lg": 12, "gap_xl": 16,
    "card_pad": 6, "card_gap": 6, "section_gap": 8,
    "field_pad": (2, 1), "button_pad": (2, 2), "tab_pad": (8, 2),
    "border_width": 1, "focus_width": 1, "scroll_width": 11,
    "progress_thickness": 8,
    "content_pad": 10, "status_pad": (10, 4),
}
_PIXEL_METRICS = ("scroll_width", "progress_thickness")

#: Semantic name -> registered ttk style name.
_STYLES: dict[str, str] = {
    "window": f"{_P}.Window.TFrame",
    "surface": f"{_P}.TFrame",
    "card": f"{_P}.Card.TFrame",
    "elevated": f"{_P}.Elevated.TFrame",
    "muted": f"{_P}.Muted.TFrame",
    "divider": f"{_P}.Divider.TFrame",
    "label": f"{_P}.TLabel",
    "title": f"{_P}.Title.TLabel",
    "heading": f"{_P}.Heading.TLabel",
    "subheading": f"{_P}.Subheading.TLabel",
    "section": f"{_P}.Section.TLabel",
    "secondary_label": f"{_P}.Secondary.TLabel",
    "status_label": f"{_P}.Status.TLabel",
    "link_label": f"{_P}.Link.TLabel",
    "success_label": f"{_P}.Success.TLabel",
    "warning_label": f"{_P}.Warning.TLabel",
    "danger_label": f"{_P}.Danger.TLabel",
    "muted_label": f"{_P}.Muted.TLabel",
    "button": f"{_P}.TButton",
    "primary_button": f"{_P}.Primary.TButton",
    "danger_button": f"{_P}.Danger.TButton",
    "ghost_button": f"{_P}.Ghost.TButton",
    "menubutton": f"{_P}.TMenubutton",
    "entry": f"{_P}.TEntry",
    "combobox": f"{_P}.TCombobox",
    "checkbutton": f"{_P}.TCheckbutton",
    "radiobutton": f"{_P}.TRadiobutton",
    "labelframe": f"{_P}.TLabelframe",
    "notebook": f"{_P}.TNotebook",
    "progressbar": f"{_P}.Horizontal.TProgressbar",
    "vscrollbar": f"{_P}.Vertical.TScrollbar",
    "hscrollbar": f"{_P}.Horizontal.TScrollbar",
    "separator": f"{_P}.TSeparator",
}

_AQUA_STYLES = {key: name.replace(_P + ".", _AQUA_STYLE_PREFIX + ".", 1)
                for key, name in _STYLES.items()}

#: ``clam`` elements cloned under this module's prefix.
_CLONED_ELEMENTS: tuple[str, ...] = (
    "Button.border", "Button.focus", "Entry.field", "Combobox.field",
    "Combobox.downarrow", "Checkbutton.indicator", "Radiobutton.indicator",
    "Notebook.client", "Notebook.tab", "Horizontal.Progressbar.trough",
    "Horizontal.Progressbar.pbar", "Vertical.Scrollbar.trough",
    "Vertical.Scrollbar.thumb", "Vertical.Scrollbar.uparrow",
    "Vertical.Scrollbar.downarrow", "Horizontal.Scrollbar.trough",
    "Horizontal.Scrollbar.thumb", "Horizontal.Scrollbar.leftarrow",
    "Horizontal.Scrollbar.rightarrow", "Labelframe.border",
    "Separator.separator",
)

_BUTTON_LAYOUT = [
    (f"{_P}.Button.border", {"sticky": "nswe", "border": "1", "children": [
        (f"{_P}.Button.focus", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"})]})]})]})]

_LAYOUTS: dict[str, list] = {
    f"{_P}.TButton": _BUTTON_LAYOUT,
    f"{_P}.TEntry": [
        (f"{_P}.Entry.field", {"sticky": "nswe", "border": "1", "children": [
            ("Entry.padding", {"sticky": "nswe", "children": [
                ("Entry.textarea", {"sticky": "nswe"})]})]})],
    f"{_P}.TCombobox": [
        (f"{_P}.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        (f"{_P}.Combobox.field", {"sticky": "nswe", "children": [
            ("Combobox.padding", {"sticky": "nswe", "children": [
                ("Combobox.textarea", {"sticky": "nswe"})]})]})],
    # The Website picker is built from the Combobox's own field and arrow clones, so it
    # is drawn exactly like the read-only Novel dropdown above it. The label fills the
    # padding (no ``side``) so a wide column shows the text left-aligned.
    f"{_P}.TMenubutton": [
        (f"{_P}.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        (f"{_P}.Combobox.field", {"sticky": "nswe", "children": [
            ("Menubutton.padding", {"sticky": "nswe", "children": [
                ("Menubutton.label", {"sticky": "nswe"})]})]})],
    f"{_P}.TCheckbutton": [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            (f"{_P}.Checkbutton.check", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})]})],
    f"{_P}.TRadiobutton": [
        ("Radiobutton.padding", {"sticky": "nswe", "children": [
            (f"{_P}.Radiobutton.radio", {"side": "left", "sticky": ""}),
            ("Radiobutton.focus", {"side": "left", "sticky": "", "children": [
                ("Radiobutton.label", {"sticky": "nswe"})]})]})],
    f"{_P}.TNotebook": [(f"{_P}.Notebook.client", {"sticky": "nswe"})],
    f"{_P}.TNotebook.Tab": [
        (f"{_P}.Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.focus", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""})]})]})]})],
    f"{_P}.Horizontal.TProgressbar": [
        (f"{_P}.Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
            (f"{_P}.Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})],
    f"{_P}.Vertical.TScrollbar": [
        (f"{_P}.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            (f"{_P}.Vertical.Scrollbar.uparrow", {"side": "top", "sticky": ""}),
            (f"{_P}.Vertical.Scrollbar.downarrow", {"side": "bottom", "sticky": ""}),
            (f"{_P}.Vertical.Scrollbar.thumb", {"sticky": "nswe"})]})],
    f"{_P}.Horizontal.TScrollbar": [
        (f"{_P}.Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
            (f"{_P}.Horizontal.Scrollbar.leftarrow", {"side": "left", "sticky": ""}),
            (f"{_P}.Horizontal.Scrollbar.rightarrow", {"side": "right", "sticky": ""}),
            (f"{_P}.Horizontal.Scrollbar.thumb", {"sticky": "nswe"})]})],
    f"{_P}.TLabelframe": [(f"{_P}.Labelframe.border", {"sticky": "nswe"})],
    f"{_P}.TLabelframe.Label": [
        ("Label.fill", {"sticky": "nswe", "children": [
            ("Label.text", {"sticky": "nswe"})]})],
    f"{_P}.TSeparator": [(f"{_P}.Separator.separator", {"sticky": "nswe"})],
}

# A variant with no layout of its own falls back to the native generic style, not to
# its base, so each variant is given its base's layout explicitly.
for _variant in (f"{_P}.Primary.TButton", f"{_P}.Danger.TButton", f"{_P}.Ghost.TButton"):
    _LAYOUTS[_variant] = _BUTTON_LAYOUT
del _variant


# ---------------------------------------------------------------------------
# fonts and scaling
# ---------------------------------------------------------------------------

def _classic_font_family(platform: str) -> str:
    if platform == "win32":
        return "Segoe UI"
    if platform == "darwin":
        return "Helvetica Neue"
    return "TkDefaultFont"


def _mono_font_family(platform: str) -> str:
    if platform == "win32":
        return "Consolas"
    if platform == "darwin":
        return "Menlo"
    return "TkFixedFont"


def _mac_font_family(root: tk.Misc) -> str:
    try:
        probe = tkfont.Font(root=root, family=".AppleSystemUIFont", size=13)
        if probe.actual("family") == ".AppleSystemUIFont":
            return ".AppleSystemUIFont"
    except tk.TclError:
        pass
    return "Helvetica Neue"


#: Body size when no live root can be asked: ``TkDefaultFont``'s size on Windows.
_FALLBACK_BODY_SIZE = 9


def _default_font_size(root: tk.Misc | None) -> int:
    """``TkDefaultFont``'s point size, so compact controls match native ones at any
    scaling. A pixel size (negative) or an unreadable font falls back to 9."""
    if root is None:
        return _FALLBACK_BODY_SIZE
    try:
        size = int(tkfont.nametofont("TkDefaultFont", root=root).actual("size"))
    except (tk.TclError, ValueError, RuntimeError):
        return _FALLBACK_BODY_SIZE
    return size if size > 0 else _FALLBACK_BODY_SIZE


def _scaling_factor(root: tk.Misc | None) -> float:
    """Display scaling relative to 100% (Tk reports 1.333 pixels/point at 96 dpi)."""
    if root is None:
        return 1.0
    try:
        factor = float(root.tk.call("tk", "scaling")) / (96.0 / 72.0)
    except (tk.TclError, ValueError):
        return 1.0
    return min(max(factor, 1.0), 3.0)


def _fonts(family: str, body: int = _FALLBACK_BODY_SIZE, mono: str = "Consolas") -> dict[str, tuple]:
    return {
        "title": (family, body + 6, "bold"), "heading": (family, body + 4, "bold"),
        "subheading": (family, body + 1, "bold"), "section": (family, body),
        "section_title": (family, body, "bold"),
        "body": (family, body), "body_bold": (family, body, "bold"),
        "small": (family, body), "status": (family, body),
        "button": (family, body), "mono": (mono, body),
    }


def _metrics(root: tk.Misc | None) -> dict[str, object]:
    """``METRICS`` with the pixel sizes scaled for the display."""
    factor = _scaling_factor(root)
    metrics = dict(METRICS)
    for key in _PIXEL_METRICS:
        metrics[key] = max(1, round(int(METRICS[key]) * factor))
    return metrics


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two ``#rrggbb`` colours."""
    def channel(value: int) -> float:
        v = value / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def luminance(color: str) -> float:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# cloned elements and drawn indicators
# ---------------------------------------------------------------------------

def _clone_elements(style: ttk.Style) -> None:
    """Clone the recolourable clam elements under ``Compact.*``, once."""
    try:
        existing = set(style.element_names())
    except tk.TclError:
        existing = set()
    for element in _CLONED_ELEMENTS:
        target = f"{_P}.{element}"
        if target in existing:
            continue
        try:
            style.element_create(target, "from", "clam", element)
        except tk.TclError:
            continue


#: Side, in pixels, of the drawn check/radio indicators (native size at 100% scaling).
_INDICATOR_SIZE = 13
#: Transparent columns right of each indicator: the gap before its label.
_INDICATOR_GAP = 5
_CHECK_MARK = ((3, 6), (4, 7), (5, 8), (6, 7), (7, 6), (8, 5), (9, 4),
               (3, 7), (4, 8), (5, 9), (6, 8), (7, 7), (8, 6), (9, 5))
_INDICATOR_STATES = ("off", "on", "disabled_off", "disabled_on")


def _indicator_pixels(kind: str, state: str, c: dict) -> dict[tuple[int, int], str]:
    """Pixel -> colour for one indicator image; unlisted pixels stay transparent."""
    disabled = state.startswith("disabled")
    selected = state.endswith("on")
    size = _INDICATOR_SIZE
    pixels: dict[tuple[int, int], str] = {}
    if kind == "check":
        if selected:
            edge = fill = c["disabled"] if disabled else c["accent"]
        else:
            edge = c["border"] if disabled else c["field_border"]
            fill = c["field_disabled"] if disabled else c["field"]
        for y in range(size):
            for x in range(size):
                border = x in (0, size - 1) or y in (0, size - 1)
                pixels[(x, y)] = edge if border else fill
        if selected:
            mark = c["field_disabled"] if disabled else c["inverse"]
            for point in _CHECK_MARK:
                pixels[point] = mark
        return pixels
    centre = (size - 1) / 2
    if selected:
        ring = dot = c["disabled"] if disabled else c["accent"]
    else:
        ring = c["border"] if disabled else c["field_border"]
        dot = None
    fill = c["field_disabled"] if disabled else c["field"]
    for y in range(size):
        for x in range(size):
            distance = ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
            if distance < 5.0:
                pixels[(x, y)] = dot if dot is not None and distance <= 2.6 else fill
            elif distance < 6.3:
                pixels[(x, y)] = ring
    return pixels


def _indicator_images(style: ttk.Style, c: dict) -> dict[str, tk.PhotoImage]:
    """Create (once per interpreter) and redraw the indicator images in place, so a
    Light/Dark toggle repaints every existing Checkbutton/Radiobutton."""
    # Held by the Tk root, never by a panel or dialog: the elements are created once per
    # interpreter and outlive any one window, and a collected PhotoImage deletes its Tcl
    # image out from under them.
    holder = style.master if style.master is not None else tk._default_root
    try:
        holder = holder._root()
    except (AttributeError, tk.TclError):
        pass
    images = getattr(holder, "_compact_indicator_images", None)
    if images is None:
        images = {f"{kind}_{state}": tk.PhotoImage(
                      master=holder, width=_INDICATOR_SIZE + _INDICATOR_GAP,
                      height=_INDICATOR_SIZE)
                  for kind in ("check", "radio") for state in _INDICATOR_STATES}
        holder._compact_indicator_images = images
    for key, image in images.items():
        kind, state = key.split("_", 1)
        image.blank()
        for (x, y), color in _indicator_pixels(kind, state, c).items():
            image.put(color, to=(x, y, x + 1, y + 1))
    return images


def _ensure_indicator_elements(style: ttk.Style, c: dict) -> None:
    images = _indicator_images(style, c)
    try:
        existing = set(style.element_names())
    except tk.TclError:
        existing = set()
    for kind, element in (("check", f"{_P}.Checkbutton.check"),
                          ("radio", f"{_P}.Radiobutton.radio")):
        if element in existing:
            continue
        try:
            style.element_create(
                element, "image", images[f"{kind}_off"],
                ("disabled", "selected", images[f"{kind}_disabled_on"]),
                ("disabled", images[f"{kind}_disabled_off"]),
                ("selected", images[f"{kind}_on"]),
                sticky="")
        except tk.TclError:
            continue


# ---------------------------------------------------------------------------
# style registration
# ---------------------------------------------------------------------------

def _button_states(style: ttk.Style, name: str, fill: str, hover: str, pressed: str,
                   text: str, c: dict, f: dict, m: dict) -> None:
    """A solid (primary-style) button: fill with hover/pressed shifts, a neutral
    disabled look, and a focus border distinct from the fill."""
    style.configure(
        name, background=fill, foreground=text, bordercolor=fill, lightcolor=fill,
        darkcolor=fill, focuscolor=text, font=f["button"], padding=m["button_pad"],
        relief="raised", borderwidth=m["border_width"], anchor="center")
    style.map(
        name,
        background=[("disabled", c["surface"]), ("pressed", pressed), ("active", hover)],
        foreground=[("disabled", c["disabled"])],
        bordercolor=[("disabled", c["border"]), ("focus", c["focus"]),
                     ("pressed", pressed), ("active", hover)],
        lightcolor=[("disabled", c["surface"]), ("pressed", pressed), ("active", hover)],
        darkcolor=[("disabled", c["surface"]), ("pressed", pressed), ("active", hover)])


def _register_ttk_styles(style: ttk.Style, c: dict, m: dict, f: dict) -> None:
    """Define every ``Compact.*`` style. No generic style is touched."""
    _clone_elements(style)
    _ensure_indicator_elements(style, c)

    for name, layout in _LAYOUTS.items():
        try:
            style.layout(name, layout)
        except tk.TclError:
            continue

    style.configure(f"{_P}.TFrame", background=c["surface"])
    style.configure(f"{_P}.Window.TFrame", background=c["window"])
    style.configure(f"{_P}.Card.TFrame", background=c["surface"])
    style.configure(f"{_P}.Elevated.TFrame", background=c["elevated"])
    style.configure(f"{_P}.Muted.TFrame", background=c["muted"])
    style.configure(f"{_P}.Divider.TFrame", background=c["border"])

    style.configure(f"{_P}.TLabel", background=c["surface"], foreground=c["text"], font=f["body"])
    style.configure(f"{_P}.Title.TLabel", background=c["window"], foreground=c["text"], font=f["title"])
    style.configure(f"{_P}.Heading.TLabel", background=c["surface"], foreground=c["text"], font=f["heading"])
    style.configure(f"{_P}.Subheading.TLabel", background=c["surface"], foreground=c["text"], font=f["subheading"])
    style.configure(f"{_P}.Section.TLabel", background=c["surface"], foreground=c["secondary"], font=f["section"])
    style.configure(f"{_P}.Secondary.TLabel", background=c["surface"], foreground=c["secondary"], font=f["body"])
    style.configure(f"{_P}.Status.TLabel", background=c["window"], foreground=c["secondary"], font=f["status"])
    style.configure(f"{_P}.Link.TLabel", background=c["surface"], foreground=c["link"], font=f["body"])
    style.configure(f"{_P}.Success.TLabel", background=c["surface"], foreground=c["success"], font=f["body"])
    style.configure(f"{_P}.Warning.TLabel", background=c["surface"], foreground=c["warning"], font=f["body"])
    style.configure(f"{_P}.Danger.TLabel", background=c["surface"], foreground=c["danger"], font=f["body"])
    style.configure(f"{_P}.Muted.TLabel", background=c["muted"], foreground=c["text"], font=f["body"])
    for name in (f"{_P}.TLabel", f"{_P}.Heading.TLabel", f"{_P}.Subheading.TLabel",
                 f"{_P}.Secondary.TLabel", f"{_P}.Muted.TLabel"):
        style.map(name, foreground=[("disabled", c["disabled"])])

    # A bordered box in every state: the border stays visible when disabled so a greyed
    # action still reads as a button. ``alternate`` is ttk's ``default="active"`` cue.
    style.configure(
        f"{_P}.TButton", background=c["elevated"], foreground=c["text"],
        bordercolor=c["field_border"], lightcolor=c["elevated"], darkcolor=c["elevated"],
        focuscolor=c["focus"], font=f["button"], padding=m["button_pad"],
        relief="raised", borderwidth=m["border_width"], anchor="center")
    style.map(
        f"{_P}.TButton",
        background=[("disabled", c["field_disabled"]), ("pressed", c["accent_soft"]),
                    ("active", c["accent_soft"]), ("selected", c["accent_soft"])],
        foreground=[("disabled", c["disabled"])],
        bordercolor=[("disabled", c["border"]), ("focus", c["focus"]),
                     ("alternate", c["accent"]), ("active", c["accent"])],
        lightcolor=[("disabled", c["field_disabled"]), ("pressed", c["accent_soft"]),
                    ("active", c["accent_soft"])],
        darkcolor=[("disabled", c["field_disabled"]), ("pressed", c["accent_soft"]),
                   ("active", c["accent_soft"])])
    _button_states(style, f"{_P}.Primary.TButton", c["accent"], c["accent_hover"],
                   c["accent_pressed"], c["inverse"], c, f, m)
    # Danger: neutral surface with a red accent in text and border; fills on hover/press.
    style.configure(
        f"{_P}.Danger.TButton", background=c["surface"], foreground=c["danger"],
        bordercolor=c["danger"], lightcolor=c["surface"], darkcolor=c["surface"],
        focuscolor=c["focus"], font=f["button"], padding=m["button_pad"],
        relief="raised", borderwidth=m["border_width"], anchor="center")
    style.map(
        f"{_P}.Danger.TButton",
        background=[("disabled", c["surface"]), ("pressed", c["danger_pressed"]),
                    ("active", c["danger"])],
        foreground=[("disabled", c["disabled"]), ("pressed", c["selection_text"]),
                    ("active", c["selection_text"])],
        bordercolor=[("disabled", c["border"]), ("focus", c["focus"]),
                     ("pressed", c["danger_pressed"]), ("active", c["danger_hover"])],
        lightcolor=[("disabled", c["surface"]), ("pressed", c["danger_pressed"]),
                    ("active", c["danger"])],
        darkcolor=[("disabled", c["surface"]), ("pressed", c["danger_pressed"]),
                   ("active", c["danger"])])
    style.configure(
        f"{_P}.Ghost.TButton", background=c["window"], foreground=c["secondary"],
        bordercolor=c["window"], lightcolor=c["window"], darkcolor=c["window"],
        focuscolor=c["focus"], font=f["button"], padding=m["button_pad"],
        relief="flat", borderwidth=0, anchor="center")
    style.map(
        f"{_P}.Ghost.TButton",
        background=[("disabled", c["window"]), ("pressed", c["surface"]), ("active", c["surface"])],
        foreground=[("disabled", c["disabled"]), ("active", c["text"])],
        bordercolor=[("focus", c["focus"])],
        lightcolor=[("pressed", c["surface"]), ("active", c["surface"])],
        darkcolor=[("pressed", c["surface"]), ("active", c["surface"])])

    style.configure(
        f"{_P}.TEntry", fieldbackground=c["field"], background=c["field"],
        foreground=c["text"], bordercolor=c["field_border"], lightcolor=c["field"],
        darkcolor=c["field"], insertcolor=c["text"], padding=m["field_pad"],
        borderwidth=m["border_width"], relief="flat",
        selectbackground=c["selection"], selectforeground=c["selection_text"])
    style.map(
        f"{_P}.TEntry",
        fieldbackground=[("disabled", c["field_disabled"]), ("readonly", c["field_disabled"])],
        foreground=[("disabled", c["disabled"])],
        bordercolor=[("focus", c["focus"]), ("hover", c["border"])],
        lightcolor=[("focus", c["focus"])], darkcolor=[("focus", c["focus"])])
    style.configure(
        f"{_P}.TCombobox", fieldbackground=c["field"], background=c["field"],
        foreground=c["text"], bordercolor=c["field_border"], lightcolor=c["field"],
        darkcolor=c["field"], arrowcolor=c["secondary"], arrowsize=12,
        padding=m["field_pad"], borderwidth=m["border_width"], relief="flat",
        selectbackground=c["selection"], selectforeground=c["selection_text"],
        insertcolor=c["text"])
    style.map(
        f"{_P}.TCombobox",
        fieldbackground=[("disabled", c["field_disabled"]), ("readonly", c["field"])],
        foreground=[("disabled", c["disabled"])],
        arrowcolor=[("disabled", c["disabled"]), ("active", c["text"])],
        bordercolor=[("focus", c["focus"]), ("active", c["border"])],
        lightcolor=[("focus", c["focus"])], darkcolor=[("focus", c["focus"])])
    # The Website picker: the same colours and states as the read-only Combobox above.
    style.configure(
        f"{_P}.TMenubutton", fieldbackground=c["field"], background=c["field"],
        foreground=c["text"], bordercolor=c["field_border"], lightcolor=c["field"],
        darkcolor=c["field"], arrowcolor=c["secondary"], arrowsize=12, font=f["body"],
        padding=m["field_pad"], borderwidth=m["border_width"], relief="flat", anchor="w")
    style.map(
        f"{_P}.TMenubutton",
        fieldbackground=[("disabled", c["field_disabled"])],
        foreground=[("disabled", c["disabled"])],
        arrowcolor=[("disabled", c["disabled"]), ("active", c["text"])],
        bordercolor=[("focus", c["focus"]), ("active", c["border"])],
        lightcolor=[("focus", c["focus"])], darkcolor=[("focus", c["focus"])])

    for name in (f"{_P}.TCheckbutton", f"{_P}.TRadiobutton"):
        style.configure(
            name, background=c["surface"], foreground=c["text"], font=f["body"],
            focuscolor=c["focus"], indicatorbackground=c["field"],
            indicatorforeground=c["accent"], indicatormargin=(0, 0, 6, 0),
            bordercolor=c["field_border"], lightcolor=c["field"], darkcolor=c["field"],
            padding=(0, 2))
        style.map(
            name,
            background=[("active", c["surface"])],
            foreground=[("disabled", c["disabled"])],
            indicatorbackground=[("disabled", c["field_disabled"]), ("selected", c["accent"]),
                                 ("pressed", c["accent_pressed"]), ("active", c["field"])],
            indicatorforeground=[("disabled", c["disabled"]), ("selected", c["inverse"])],
            bordercolor=[("focus", c["focus"]), ("selected", c["accent"]),
                         ("active", c["border"])])

    # A numbered control section: a neutral bordered frame whose caption is bold, in the
    # ordinary text colour (never a green/blue/red heading).
    style.configure(f"{_P}.TLabelframe", background=c["surface"], bordercolor=c["border"],
                    lightcolor=c["surface"], darkcolor=c["surface"],
                    borderwidth=m["border_width"], relief="solid", padding=m["card_pad"])
    style.configure(f"{_P}.TLabelframe.Label", background=c["surface"],
                    foreground=c["text"], font=f["section_title"])

    style.configure(f"{_P}.TNotebook", background=c["surface"], bordercolor=c["border"],
                    lightcolor=c["surface"], darkcolor=c["surface"], borderwidth=0,
                    tabmargins=(0, 0, 0, 0))
    style.configure(f"{_P}.TNotebook.Tab", background=c["surface"], foreground=c["secondary"],
                    font=f["body"], padding=m["tab_pad"], bordercolor=c["border"],
                    lightcolor=c["surface"], darkcolor=c["surface"],
                    focuscolor=c["focus"], borderwidth=0)
    style.map(
        f"{_P}.TNotebook.Tab",
        background=[("selected", c["elevated"]), ("active", c["elevated"])],
        foreground=[("selected", c["text"]), ("active", c["text"]), ("disabled", c["disabled"])],
        lightcolor=[("selected", c["elevated"]), ("active", c["elevated"])],
        darkcolor=[("selected", c["elevated"]), ("active", c["elevated"])])

    style.configure(f"{_P}.Horizontal.TProgressbar", background=c["accent"],
                    troughcolor=c["field"], bordercolor=c["border"], lightcolor=c["accent"],
                    darkcolor=c["accent"], borderwidth=0, thickness=m["progress_thickness"])
    for name in (f"{_P}.Vertical.TScrollbar", f"{_P}.Horizontal.TScrollbar"):
        style.configure(name, background=c["scroll_thumb"], troughcolor=c["scroll_trough"],
                        bordercolor=c["scroll_trough"], lightcolor=c["scroll_thumb"],
                        darkcolor=c["scroll_thumb"], arrowcolor=c["secondary"],
                        borderwidth=0, arrowsize=m["scroll_width"], gripcount=0,
                        width=m["scroll_width"])
        style.map(
            name,
            background=[("disabled", c["scroll_trough"]), ("pressed", c["accent"]),
                        ("active", c["scroll_thumb_hover"])],
            arrowcolor=[("disabled", c["disabled"]), ("active", c["text"])],
            lightcolor=[("active", c["scroll_thumb_hover"])],
            darkcolor=[("active", c["scroll_thumb_hover"])])

    style.configure(f"{_P}.TSeparator", background=c["border"])


def _register_aqua_styles(style: ttk.Style, c: dict, f: dict) -> dict[str, str]:
    """Native control inheritance with named surface/label colours only. No control
    padding, fonts, indicator images or native layouts change."""
    for key in ("window", "surface", "card", "elevated", "muted", "divider"):
        colour = {"window": "window", "divider": "border"}.get(key, "surface" if key == "card" else key)
        style.configure(_AQUA_STYLES[key], background=c[colour])
    for key, foreground in {
        "label": "text", "title": "text", "heading": "text", "subheading": "text",
        "section": "text", "secondary_label": "secondary", "status_label": "secondary",
        "link_label": "link", "success_label": "success", "warning_label": "warning",
        "danger_label": "danger", "muted_label": "secondary",
    }.items():
        style.configure(_AQUA_STYLES[key], foreground=c[foreground], background=c["surface"])
        style.map(_AQUA_STYLES[key], foreground=[("disabled", c["disabled"])])
    style.configure(_AQUA_STYLES["danger_button"], foreground=c["danger"])
    # Section captions are semantic labels, not native controls. Keep Aqua's frame
    # layout while supplying the same neutral bold heading used on other platforms.
    style.configure(_AQUA_STYLES["labelframe"] + ".Label", foreground=c["text"],
                    background=c["surface"], font=f["section_title"])
    return dict(_AQUA_STYLES)


# ---------------------------------------------------------------------------
# the bundle
# ---------------------------------------------------------------------------

def apply_native_appearance(window: tk.Misc, bundle: dict) -> bool:
    """Match one app-owned Aqua window to the app setting, keeping native controls.

    Tk 8.6's ``MacWindowStyle appearance`` operates per window (macOS 10.14+).
    Unsupported Tk builds and other window systems are harmless no-ops.
    """
    try:
        if window.tk.call("tk", "windowingsystem") != "aqua":
            return False
        native = "darkaqua" if bundle.get("appearance") == DARK else "aqua"
        top = window.winfo_toplevel()
        pending = getattr(top, "_compact_native_map_binding", None)
        if pending is not None:
            top.unbind("<Map>", pending)
            top._compact_native_map_binding = None
        previous = window.tk.call("tk::unsupported::MacWindowStyle", "appearance",
                                  str(top), native)
        if previous == "":
            # Tk silently returns empty before an NSWindow exists: retry once on <Map>.
            def on_map(event):
                if event.widget is top:
                    apply_native_appearance(top, bundle)

            top._compact_native_map_binding = top.bind("<Map>", on_map, add="+")
            return False
        return True
    except tk.TclError:
        return False


def _refresh_native_windows(root: tk.Misc, bundle: dict) -> None:
    """Refresh the root and every already-open app Toplevel on Aqua."""
    apply_native_appearance(root, bundle)

    def visit(widget):
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, tk.Toplevel):
                apply_native_appearance(child, bundle)
            visit(child)

    visit(root)


def build_bundle(style: ttk.Style, appearance: str | None = None, *,
                 platform: str | None = None, root: tk.Misc | None = None) -> dict:
    """Build (or refresh in place) the bundle for *appearance*.

    Never calls ``style.theme_use()`` or touches a generic style name. Calling it again
    with the same ``style`` is the refresh path: every ``Compact.*`` style is
    reconfigured in place and existing widgets repaint without being destroyed.
    """
    resolved = normalize_appearance(appearance)
    branch = sys.platform if platform is None else platform
    colors = dict(_PALETTES[resolved])
    body = _default_font_size(root)

    if branch == "darwin":
        family = _mac_font_family(root) if root is not None else "Helvetica Neue"
    else:
        family = _classic_font_family(branch)
    fonts = _fonts(family, body, _mono_font_family(branch))
    metrics = _metrics(root)

    if branch == "darwin":
        styles = _register_aqua_styles(style, colors, fonts)
    else:
        _register_ttk_styles(style, colors, metrics, fonts)
        styles = dict(_STYLES)

    bundle = {
        "mode": "compact",
        "appearance": resolved,
        "platform": branch,
        "family": family,
        "fonts": fonts,
        "colors": colors,
        "metrics": metrics,
        "styles": styles,
        "style_prefix": _AQUA_STYLE_PREFIX if branch == "darwin" else _P,
        "ttk_active": branch != "darwin",
    }
    if branch == "darwin" and root is not None:
        _refresh_native_windows(root, bundle)
    return bundle


def style_name(bundle: dict, key: str) -> str:
    """The registered ttk style name for a semantic *key* (``"button"``, ``"entry"`` ...)."""
    return bundle["styles"][key]


# ---------------------------------------------------------------------------
# classic Tk widget colouring (Text/Menu: ttk cannot style these)
# ---------------------------------------------------------------------------

_TK_WIDGET_ROLES: dict[str, tuple[str, str]] = {
    "window": ("window", "text"), "surface": ("surface", "text"),
    "elevated": ("elevated", "text"), "muted": ("muted", "text"),
    "field": ("field", "text"), "list": ("field", "text"), "text": ("field", "text"),
    "log": ("field", "text"), "canvas": ("surface", "text"), "divider": ("border", "text"),
    "menu": ("field", "text"),
}


def style_tk_widget(widget, bundle: dict, role: str = "surface", **overrides):
    """Colour a classic Tk widget from *bundle*. Returns the options applied.

    Covers background, foreground, caret, selection (active and inactive), disabled
    text and the focus highlight ring. A no-op (``{}``) when *bundle* has no colours.
    The ``menu`` role highlights the hovered entry with the selection colours and draws
    radio/check marks in the text colour, so both stay visible in Dark mode.
    """
    colors = (bundle or {}).get("colors")
    if not colors:
        return {}
    if role not in _TK_WIDGET_ROLES:
        raise ValueError(
            f"unknown appearance role {role!r}; expected one of {sorted(_TK_WIDGET_ROLES)}")
    bg_key, fg_key = _TK_WIDGET_ROLES[role]
    opts = {
        "background": colors[bg_key], "foreground": colors[fg_key],
        "highlightbackground": colors["border"], "highlightcolor": colors["focus"],
        "highlightthickness": 0, "borderwidth": 0, "relief": "flat",
        "insertbackground": colors["text"], "selectbackground": colors["selection"],
        "selectforeground": colors["selection_text"],
        "inactiveselectbackground": colors["selection_inactive"],
        "disabledforeground": colors["disabled"], "troughcolor": colors["scroll_trough"],
        "activebackground": colors["elevated"], "activeforeground": colors["text"],
    }
    if role == "menu":
        opts.update(activebackground=colors["selection"],
                    activeforeground=colors["selection_text"],
                    selectcolor=colors["text"], borderwidth=1)
    opts.update(overrides)
    try:
        supported = set(widget.keys())
    except tk.TclError:
        supported = set()
    applied = {k: v for k, v in opts.items() if k in supported}
    if applied:
        widget.configure(**applied)
    return applied


def style_combobox_popdown(combo, bundle: dict) -> bool:
    """Colour a ``Compact.TCombobox``'s drop-down list (a hidden classic Listbox).

    Only where the compact ttk styles are active; on Aqua the menu is native.
    """
    colors = (bundle or {}).get("colors")
    if not colors or not bundle.get("ttk_active"):
        return False
    try:
        popdown = combo.tk.eval(f"ttk::combobox::PopdownWindow {combo}")
        combo.tk.call(
            f"{popdown}.f.l", "configure",
            "-background", colors["field"], "-foreground", colors["text"],
            "-selectbackground", colors["selection"],
            "-selectforeground", colors["selection_text"],
            "-highlightthickness", 0, "-borderwidth", 0)
    except tk.TclError:
        return False
    return True


# ---------------------------------------------------------------------------
# live refresh notification
# ---------------------------------------------------------------------------

#: Callbacks told about every appearance change, so an open app-owned dialog can
#: re-apply the one thing ttk style mutation cannot reach: a Toplevel's own background.
_listeners: list[Callable[[dict], None]] = []


def register_listener(callback: Callable[[dict], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def unregister_listener(callback: Callable[[dict], None]) -> None:
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def notify_listeners(bundle: dict) -> None:
    """Tell every registered listener about *bundle*. Never raises."""
    for callback in list(_listeners):
        try:
            callback(bundle)
        except Exception:
            continue


def toggle_appearance(style: ttk.Style, current: str, *, platform: str | None = None,
                      root: tk.Misc | None = None) -> dict:
    """Refresh the styles for the other appearance, notify listeners, return the bundle.

    Persistence is the caller's job (see :class:`webnovel_scraper.ui.settings.SettingsStore`).
    """
    bundle = build_bundle(style, toggled(current), platform=platform, root=root)
    notify_listeners(bundle)
    return bundle
