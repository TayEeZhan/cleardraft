"""Run the pipeline once and write everything the UI needs as one JSON file.

    python scripts/export_ui_data.py

Why this exists: the web UI reads `web/public/data.json` so it renders with no
backend at all. When the API is live the UI prefers it and falls back to this
file, which means the demo cannot break in front of a judge because a free-tier
server was cold.

The shape here matches `docs/API.md` exactly, so swapping the fetch URL from
this file to `/api/...` needs no change in the UI.

The row/detail/stats building itself lives in `adapters/ui_rows.py`, shared
with `api/_dataset.py` (the live "upload a dataset" endpoint) so the batch
snapshot and a live per-request run can never drift into two different row
shapes for the same email.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import LocalInbox                          # noqa: E402
from adapters.ui_rows import build_rows                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Hand-authored amended drafts. See data/demo/README.md - demo only, never scored.
_manifest = os.path.join(ROOT, "data", "demo", "amendments.json")
AMENDMENTS: dict = (
    json.load(open(_manifest, encoding="utf-8")).get("amendments", {})
    if os.path.exists(_manifest) else {}
)


def _rel(path: str) -> str:
    """Path relative to data/, forward slashes. The snapshot is published, so
    it must never carry the absolute path of the machine that built it."""
    if not path:
        return ""
    return os.path.relpath(path, os.path.join(ROOT, "data")).replace(os.sep, "/")


def main() -> int:
    inbox = LocalInbox(os.path.join(ROOT, "data"))

    payload = build_rows(
        inbox,
        rel=_rel,
        amendments=AMENDMENTS,
        amendment_root=os.path.join(ROOT, "data"),
    )

    board = payload["board"]
    counts = payload["counts"]
    total = len(board)
    compared = payload["stats"]["totals"]["compared"]
    runtime = payload["generated_runtime_seconds"]

    # Held-out evidence for the model tier, if scripts/run_challenge.py has run.
    challenge_path = os.path.join(ROOT, "out", "challenge_report.json")
    if os.path.exists(challenge_path):
        ch = json.load(open(challenge_path, encoding="utf-8"))
        e = ch["emails"]
        payload["stats"]["challenge"] = {
            "model": ch["model"],
            "emails": e["n"],
            "category_accuracy": e["category_accuracy"],
            "intent_accuracy": e["intent_accuracy"],
            "decided_by_model": e["decided_by_model"],
            "rescued_by_model": e["rescued_by_model"],
            "model_wrong": e["model_wrong"],
            "doc_pairs": [{
                "pair": p["pair"],
                "fields_rules": p["rules_only"]["fields_found"]["si"] + p["rules_only"]["fields_found"]["bl"],
                "fields_model": p["with_model"]["fields_found"]["si"] + p["with_model"]["fields_found"]["bl"],
                "fields_present": p["with_model"]["fields_present"]["si"] + p["with_model"]["fields_present"]["bl"],
                "defects_found": p["with_model"]["defects"],
                "defects_gold": p["gold_defects"],
            } for p in ch["docs"]["pairs"]],
            "calls": ch["model_stats"]["calls"],
            "tokens": ch["model_stats"]["input_tokens"] + ch["model_stats"]["output_tokens"],
            "gate_rejections": ch["model_stats"]["gate_rejections"],
            "placement_rejections": ch["model_stats"].get("placement_rejections", 0),
        }

    out = os.path.join(ROOT, "web", "public", "data.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    size_mb = os.path.getsize(out) / 1_048_576
    print(f"wrote {out}")
    print(f"  {total} emails, {compared} compared, {runtime:.2f}s, {size_mb:.2f} MB")
    print(f"  board tabs: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
