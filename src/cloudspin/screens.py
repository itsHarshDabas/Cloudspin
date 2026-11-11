"""The two small dialogs: choosing a city and the list of keys."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

_CARD = """
    align: center middle;
    background: #05070c 55%;
"""


class Prompt(ModalScreen[str | None]):
    """A one-line question."""

    DEFAULT_CSS = (
        "Prompt {" + _CARD + "}"
        """
    Prompt #card {
        width: 64;
        max-width: 92%;
        height: auto;
        padding: 1 3;
        background: #121826;
        border: round #3a455e;
    }
    Prompt #title { text-style: bold; color: #eef1f6; }
    Prompt #lede { color: #9aa5ba; margin-bottom: 1; }
    Prompt #foot { color: #6f7b92; margin-top: 1; }
    Prompt Input, Prompt Input:focus {
        border: tall #2a3550;
        height: 3;
        padding: 0 1;
        background: #1e2638;
        color: #eef1f6;
    }
    """
    )
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, lede: str, placeholder: str = "", value: str = "", confirm: str = "save") -> None:
        super().__init__()
        self._title = title
        self._lede = lede
        self._placeholder = placeholder
        self._value = value
        self._confirm = confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static(self._title, id="title")
            yield Static(self._lede, id="lede")
            yield Input(value=self._value, placeholder=self._placeholder)
            yield Static(f"Enter to {self._confirm}, Esc to go back", id="foot")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def city_prompt(current: str = "") -> Prompt:
    return Prompt(
        "Where should the weather come from?",
        "Type a city. The forecast, sunrise and sunset will follow it.",
        placeholder="New Delhi",
        value=current,
    )


def link_prompt() -> Prompt:
    return Prompt(
        "Play from a link",
        "Paste a link to a song, a radio stream or a video. A file or folder path works too.",
        placeholder="https://",
        confirm="play",
    )


_KEYS = (
    ("space", "Play or pause"),
    ("n  p", "Next song, previous song"),
    ("←  →", "Skip back or ahead 5 seconds (hold Shift for 30)"),
    ("+  −", "Volume up or down"),
    ("s", "Shuffle on or off"),
    ("r", "Repeat: off, all, one"),
    ("w", "Sky: follow the song, follow the weather, or pin a scene"),
    ("t", "Colours: follow the sky, follow the cover art, or pick a theme"),
    ("o", "Open a link, file or folder and play it"),
    ("c", "Choose the city for the weather"),
    ("u", "Switch between °C and °F"),
    ("q", "Quit"),
)


class Help(ModalScreen[None]):
    DEFAULT_CSS = (
        "Help {" + _CARD + "}"
        """
    Help #card {
        width: 74;
        max-width: 94%;
        height: auto;
        padding: 1 3;
        background: #121826;
        border: round #3a455e;
    }
    Help #title { text-style: bold; color: #eef1f6; margin-bottom: 1; }
    Help .row { color: #9aa5ba; }
    Help #foot { color: #6f7b92; margin-top: 1; }
    """
    )
    BINDINGS = [("escape,question_mark,q,enter,space", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            yield Static("Keys", id="title")
            for keys, what in _KEYS:
                yield Static(f"[b #eef1f6]{keys:<7}[/]  {what}", classes="row")
            yield Static("Click the buttons, the progress line, the volume or a song in Up next too.", id="foot")

    def action_close(self) -> None:
        self.dismiss(None)
