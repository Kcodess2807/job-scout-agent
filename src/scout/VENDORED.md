# Vendored: the job search (Sid's jobs-scout)

From https://github.com/Sid20rathi/Job-scout-agent at commit 1753f02 ("job scout agent seacrh feature"), 2026-11-03:
all of `src/`, with his `agent.py` saved as `standalone.py` (the only name that collides with a file in `src/`), and
`tests/` without `conftest.py` and `test_output_graph.py` (they test his graph, which does not run here), under
`tests/scout/`.

The files are unchanged, so a newer version of his repo can be copied over this folder the same way (`agent.py` to
`standalone.py` again). His modules import each other by bare name (`from engine import scout`), so `src/scout` is
put on `sys.path` by `src/agent.py` and by `tests/conftest.py`. Here, `src/agent.py`'s `discover` node calls his
`engine.scout` directly, as his README's integration guide says, and reuses `standalone.py`'s helpers (the compact
profile kept between runs, the rejections remembered) and `output.py`'s "why it fits" line.
