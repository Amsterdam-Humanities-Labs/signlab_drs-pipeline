# CLAUDE.md

Agent hints. Overview, services and config: README.md. Operator manual: docs/manual.md.

- Runs on the DRS Mac as `/Users/signlab/drs`; paths in `startupScript.py` are absolute to that checkout.
- Entry point: `/usr/bin/python3 startupScript.py` (supervises everything in `services/`).
- Active code lives in `services/` and `shared/`. `variants/` and `tools/` are run by hand; `old/` is archive.
- `lib/` and `bin/` are a committed venv (numpy etc.). Do not edit or grep through it.
- `qr/` (the running QR scanner) is gitignored; `qr_scanner/` is a read-only copy.
- DaVinci Resolve is driven through `shared/python_get_resolve.py`; it only works on a machine with Resolve running.
- Render claims between machines: `shared/drs_render_client.py` -> `signcollect.nl/drs_ep/api.php` (see docs/drs_ep_api_guide.md).
- `python3 -m pytest test/` runs anywhere: `test/conftest.py` stubs the Mac-only clients. `manual_*.py` need hardware.
- Production server `signcollect.nl` is read-only for agents.
