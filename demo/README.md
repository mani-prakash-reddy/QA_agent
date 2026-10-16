# Demo material

- `brds/*.md`: sample Business Requirements Documents (for the Juice Shop and the template app), written to include tables, native ids, unnumbered requirements, vague wording ("respond quickly", "TBD"), requirements the app does **not** implement, and app features the BRD **omits**.
- `brds/*.docx`, `brds/*.pdf`: the same documents rendered into real formats (`uv run python demo/build_sample_brds.py` from `backend/`). Upload any of them in the Requirements tab.
- `apps/` and `build_app_brds.py`: three purpose-built test applications (LeaveTrack, Stockroom, HelpDesk) with their BRDs and answer keys: see `apps/README.md`.
- `brds/*.gold.json`: the expected requirements, used by `backend/scripts/eval_brd_extraction.py` to measure extraction quality.

Juice Shop (`docker compose --profile demo-juice up -d juice-shop`, target URL `http://juice-shop:3000`) is intentionally vulnerable: keep it on the local compose network.
