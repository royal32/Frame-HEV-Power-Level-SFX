# Original Half-Life HEV audio

`manifest.json` records the original `sound/fvox/` WAV vocabulary used by the
battery announcer. Audio belongs to Valve and is separate from this project's
application code. Downloads are for the user's local use; do not publish the WAV
files as project-owned audio.

The user-supplied [101soundboards search](https://www.101soundboards.com/search/half-life-hev-suit)
was the starting reference. The downloadable files come from the
[Half-Life 1 Sound Effects catalog](https://hl1sfx.com/), using its public
`/download/fvox/<filename>.wav` links. The
[HEV Assistant collection](https://sounds.spriters-resource.com/pc_computer/halflife/asset/394678/)
also lists the original vocabulary. The game itself is the preferred source
when an installed copy is available.

Fetch the recorded clips:

```sh
python3 tools/fetch_sounds.py
python3 tools/fetch_sounds.py --check
```

The main installer does this automatically. It reuses verified audio from an
existing installation and downloads missing clips with up to four concurrent
requests. Use `--cache-dir /path/to/assets` to reuse a directory containing
`fvox/`, or `--offline` to forbid downloads. `--quiet` displays only the summary
and errors. A failed or changed download is never installed over a valid clip.

Or import the same clips from an installed game without downloading:

```sh
python3 tools/fetch_sounds.py --source-dir /path/to/Half-Life/valve
```

The source may be a `valve`, `sound`, or `fvox` directory with loose WAV files.
Packed game archives must first be extracted. Import and download both validate
the WAV's frame count, PCM format, byte count, and recorded SHA-256. If a game
version has different bytes, the helper reports the mismatch so it can be
reviewed rather than silently changing the voice assets. `--force` replaces
existing files only after the new bytes pass validation.

Clips retain their original filenames and PCM audio: mono, 11,025 Hz,
unsigned 8-bit samples. The unusual `fourty.wav` spelling is original.
The manifest's byte counts include the WAV headers and metadata chunks.

Exact battery readings from **1 through 100** can be assembled using the
original numbers: one through nineteen, tens, and one hundred. Compound
numbers such as 73 use `seventy.wav` followed by `three.wav`; 25 has its own
`twentyfive.wav` clip. Every reading can use `power_level_is.wav` and
`percent.wav`. Original Half-Life usually said a shorter “Power … percent”
at five-point battery increments, reserving “Power level is …” for 100;
this project's exact-percent phrase is a new assembly of original recordings.

There is **no HEV `zero.wav`** in this vocabulary. A 0% reading uses
`armor_gone.wav` (“Armor compromised”). No announcer/VOX or soldier voice is
substituted. Nearest-ten rounding is an optional presentation choice that
can use just the tens and one hundred, with the same warning at zero. The
complete downloaded vocabulary allows exact readings without rounding.

`fuzz.wav` is the original radio/static cue. `power_restored.wav` is preserved
for callers wanting the game's shorter power prefix; it is a full “Power
restored” clip, so reproducing the original shortened word requires trimming.
