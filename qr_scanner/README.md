# qr_scanner

The QR scanner service on the DRS Mac, copied read-only on 2026-09-23 so it is in git (signlab_signcollect-stack#35).

The running copy is still `qr/` in the DRS checkout (gitignored); `startupScript.py` starts `qr/qr_scanner_service.py`. To switch, point `startupScript.py` at `qr_scanner/` and restart.

- `qr_scanner_service.py`: watches new recordings and reads their QR codes
- `qrConvert.py`, `send_json_to_api.py`: convert results and post them to the server
- `rescan_dates.py`, `test_qr_local.py`: manual tools

The database password comes from `DB_PASS`. `scanned_files.json` (21 MB of scan state) is not copied.
