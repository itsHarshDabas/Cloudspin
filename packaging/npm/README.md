# cloudspin

A terminal music player under a living sky: a spinning record, real weather,
and a sky that changes with the song.

This npm package is a small launcher. Cloudspin itself is a Python app, so the
launcher runs it through whichever of these it finds: an installed `cloudspin`,
[`uv`](https://docs.astral.sh/uv/), or [`pipx`](https://pipx.pypa.io/).

```sh
npx cloudspin --demo
npm install -g cloudspin
```

You need `mpv` for sound. Full documentation:
https://github.com/itsHarshDabas/Cloudspin
