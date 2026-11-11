# Cloudspin

A music player for your terminal that lives under a sky. A record spins, the
weather for your city is written across the horizon, and the sky itself changes
with the song: phonk brings a thunderstorm, lo-fi settles into evening,
synthwave turns the stars on.

![Cloudspin playing a lo-fi song under an evening sky](docs/evening.png)

| Phonk gets a storm | Synthwave gets the night, in the cover's colours |
| --- | --- |
| ![Thunderstorm scene](docs/storm.png) | ![Night scene](docs/night.png) |

## What it does

- Plays local files, folders, radio streams and links through `mpv`.
- Paste a link while it's running (press `o`) and it starts playing.
- Shows live weather and a six-day forecast from [Open-Meteo](https://open-meteo.com) (free, no API key).
- Draws an animated sky: sun, moon with the real phase, stars, drifting clouds, rain, snow, fog and lightning.
- Picks the sky to suit the song, or follows the real weather, or stays on a scene you pin.
- Takes its colours from the sky, from the song's cover art, or from a theme you write.
- Spins a record whose label is the album cover, with a tonearm that tracks through the song.
- Works with the mouse: click the buttons, drag the progress line, click a song in Up next, scroll for volume.

## Install

Cloudspin is not on PyPI, the AUR or npm yet. Until it is, install it from this repository:

```sh
pipx install git+https://github.com/itsHarshDabas/Cloudspin
# or
uv tool install git+https://github.com/itsHarshDabas/Cloudspin
```

You also need `mpv` for sound. Two more tools are optional:

| Tool | Why |
| --- | --- |
| `mpv` | Plays the music. Without it Cloudspin runs silently. |
| `ffmpeg` | Lets Cloudspin listen to songs that have no genre tag, to guess a sky. |
| `yt-dlp` | Lets mpv play YouTube, SoundCloud and similar links. |

On Arch: `sudo pacman -S mpv ffmpeg yt-dlp`.

Needs Python 3.11 or newer, Linux or macOS, and a terminal with truecolor
(kitty, foot, Alacritty, WezTerm, Ghostty, iTerm2 and most others).

## Use

```sh
cloudspin                       # plays ~/Music
cloudspin ~/Music/phonk         # a folder
cloudspin song.flac other.mp3   # files
cloudspin https://example.com/stream.mp3   # a link
cloudspin --city "New Delhi"    # set the city once; it is remembered
cloudspin --demo                # look around without any music or sound
```

### Keys

| Key | Does |
| --- | --- |
| `space` | Play or pause |
| `n` `p` | Next song, previous song |
| `←` `→` | Skip 5 seconds (`Shift` for 30) |
| `+` `-` | Volume |
| `s` | Shuffle |
| `r` | Repeat: off, all, one |
| `o` | Open a link, file or folder and play it |
| `w` | Sky: follow the song, follow the weather, or pin a scene |
| `t` | Colours: follow the sky, follow the cover art, or a theme |
| `c` | Choose the city |
| `u` | Switch °C and °F |
| `?` | Show the keys |
| `q` | Quit |

## How the sky picks a scene

With the sky set to follow the song (the default), Cloudspin looks for a
scene in this order and uses the first one it finds:

1. **Your rules.** Words you map to scenes in the config file.
2. **The tags.** The genre tag, then sky words in the title or album.
   `phonk`, `metal` and `dubstep` storm; `lofi`, `jazz` and `acoustic` get the
   evening; `synthwave` and `r&b` get the night; `blues` rains; `dream pop`
   fogs over; `christmas` snows; `pop` and `dance` get a sunny day. A song
   called "Midnight Rain" needs no tag at all.
3. **A short listen.** If `ffmpeg` is installed, 30 seconds of the song are
   measured for loudness, tempo, bass and brightness. Loud, fast and
   bass-heavy becomes a storm; quiet and slow becomes night.
4. **The real weather**, if nothing above says anything.

The caption in the top right corner of the sky always says which scene is
showing and why, for example `Thunderstorm, tagged Drift phonk`.

The scenes are `storm`, `rain`, `evening`, `night`, `day`, `dawn`, `snow`,
`fog` and `overcast`.

## Settings

Settings live in `~/.config/cloudspin/config.toml`. Everything is optional.

```toml
city = "New Delhi"
units = "metric"      # or "imperial"
sky = "song"          # "song", "weather", or a scene name to pin
colors = "sky"        # "sky", "cover", a built-in theme, or one of yours
music = "~/Music"
volume = 70
fps = 24              # lower this on a slow terminal or over SSH

# Send particular artists, albums or words to a scene.
[moods]
"kordhell" = "storm"
"study mix" = "evening"

# Your own colours. Only `accent` is required.
[themes.neon]
accent = "#39ff88"
accent2 = "#00c2ff"
background = "#07100c"
disc = "#39ff88"      # the record label, when the song has no cover art
disc2 = "#ffffff"
```

Built-in themes: `sky`, `cover`, `amber`, `orchid`, `lagoon`, `moss`, `rose`, `mono`.

## Good to know

- **The bars are generative.** The spectrum moves with the play state, the
  volume and a tempo chosen per song. It is not a live analysis of the audio.
- **The listen is a guess.** Step 3 above is a heuristic that has only been
  checked against synthetic test audio. Tags and your own rules are exact;
  use `[moods]` when it gets a song wrong.
- **Links need mpv.** Plain audio URLs and radio streams work with mpv alone.
  Sites like YouTube also need `yt-dlp`.
- **Windows is not supported** except through WSL: playback control uses a
  Unix socket.
- **MPRIS** (media keys, `playerctl`, Waybar) works if you have the
  `mpv-mpris` plugin installed for mpv; Cloudspin loads your normal mpv config.

## Packaging status

| Channel | Status |
| --- | --- |
| Git (`pipx`, `uv`) | Works today |
| PyPI | Ready to publish: `python -m build && twine upload dist/*`. The name `cloudspin` was free when checked in November 2026. |
| AUR | `packaging/aur/PKGBUILD` is written but depends on the PyPI release |
| npm | `packaging/npm` is a small launcher that runs the Python app through `uv` or `pipx`; not published |
| Homebrew | Not started |

## Develop

```sh
git clone https://github.com/itsHarshDabas/Cloudspin && cd Cloudspin
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
python scripts/screenshot.py out.png --song 2   # render the app to a PNG, no terminal needed
```

The code is small and split by job:

| File | Job |
| --- | --- |
| `sky.py` | The procedural sky and landscape |
| `canvas.py` | Half-block pixel drawing |
| `vinyl.py`, `spectrum.py` | The record and the bars |
| `mood.py` | Choosing a scene for a song |
| `weather.py` | Open-Meteo and sun and moon maths |
| `player.py` | mpv control and the queue |
| `widgets.py`, `app.py` | The interface |

## Licence

MIT
