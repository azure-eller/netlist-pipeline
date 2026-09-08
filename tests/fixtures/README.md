Fixtures are KiCad's own files, used as oracles:

- `rpi_hat/`: the RaspberryPi-HAT project template shipped with KiCad 10 (`/usr/share/kicad/template`).
- `pic_programmer/`: the `demos/pic_programmer` project from gitlab.com/kicad/code/kicad, master, 2026-09-07.

Both are CC-BY-SA 4.0 per the KiCad project. They are used unmodified.

`rpi_hat/constraints.json` is ours: the template leaves the footprint field empty on five
symbols, so it assigns standard-library footprints and rail currents, the way a client would.
