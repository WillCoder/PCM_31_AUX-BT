# Verification tooling — look at the screen with software, not with your eyes

These three tools exist because the alternative is asking a human to stare at a head unit
and describe what they saw, which is slow, imprecise, and burns their patience. Every UI
question that can be answered from the composited frame should be answered here instead.

| file | what it does |
|---|---|
| `pcmshot.c` | On-device. `gf_display_snapshot()` grabs the **composited** frame (stock UI + your overlay) into a surface and dumps a region as P5HEX text. Build with the same `build.sh` recipe as the engine |
| `shotdiff.py` | Host side. Diffs a "hidden" capture against a "shown" capture; the bounding box of changed pixels is the overlay's **true** on-screen extent, compared against what you asked for |
| `push_uidef.py` | Host side. Pushes a local `ui.def` over the 57600 serial line so layout/colour iterations cost seconds instead of a recompile |

## Typical loop

```bash
# capture with the popup hidden, then with it shown
/tmp/pcmshot /tmp/bg.hex 200 190 400 100
/tmp/pcmshot /tmp/fg.hex 200 190 400 100
python3 ser_pull.py /tmp/bg.hex bg_raw.txt
python3 ser_pull.py /tmp/fg.hex fg_raw.txt

python3 shotdiff.py bg_raw.txt fg_raw.txt 200 190 372 76
#   actual: 372 x 76   expected: 372 x 76   width OK / height OK
```

## Two things worth knowing

- The snapshot comes back **BGRA8888** (`fmt 0x1420`, stride 3200) no matter what format
  the layers themselves use. Hex text is deliberate: binary over a 57600 serial line loses
  bytes.
- **A snapshot cannot show hardware-scanout artefacts** — e.g. the alpha-plane banding
  caused by a misaligned stride. Those exist in the display controller, not in any buffer.
  That one class of defect still needs a photograph; say so explicitly when you ask for one.

`push_uidef.py` chunks its writes: one over-long serial command is **silently truncated**,
which once left the engine parsing a `ui.def` that was missing its last three lines.
