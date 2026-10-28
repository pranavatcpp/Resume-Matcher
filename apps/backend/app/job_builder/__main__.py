"""CLI handoff from Step 3; never changes the parent agent or master resume."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from app.job_builder.core import MODEL, Ollama, atomic_json, build_content, parse_master
from app.job_builder.docx import render_one_page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cv", "jd", "record", "template", "slots", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    record = json.loads(args.record.read_text())
    if (
        record.get("screening", {}).get("decision") != "pass"
        or record.get("status") == "rejected_code"
    ):
        parser.error("Step 3 must explicitly pass before building")
    output = args.output.resolve()
    sources = [
        p.resolve() for p in (args.cv, args.jd, args.record, args.template, args.slots)
    ]
    if any(output == p or output in p.parents for p in sources):
        parser.error("Output directory must not contain input files")
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("manifest.json", "resume-draft.docx")):
        parser.error(
            "Use a fresh output directory so a failed run cannot leave stale ready artifacts"
        )
    manifest: dict[str, Any] = {
        "status": "building",
        "model": args.model,
        "source_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
    }
    atomic_json(output / "manifest.json", manifest)
    client = Ollama(output / "cache", args.model)
    try:
        cv_text = args.cv.read_text()
        master = parse_master(cv_text)
        name = re.search(r"^# CV: (.+)$", cv_text, re.M)
        email = re.search(r"^\*\*Email:\*\* (.+)$", cv_text, re.M)
        location = re.search(r"^\*\*Location:\*\* (.+)$", cv_text, re.M)
        with ZipFile(args.template) as archive:
            template_xml = archive.read("word/document.xml").decode()
        if (
            not name
            or not email
            or name[1].upper() not in template_xml.upper()
            or email[1] not in template_xml
        ):
            raise ValueError("Template identity must match canonical CV name and email")
        home_location = location[1].split("(")[0].strip() if location else ""
        if "Python" not in master.skills.get("Programming Languages", "").split(", "):
            raise ValueError("This template profile requires canonical Python skill")
        data = build_content(master, args.jd.read_text(), client)
        atomic_json(output / "draft.json", data)
        layout = render_one_page(
            args.template,
            output / "resume-draft.docx",
            data,
            json.loads(args.slots.read_text()),
            home_location,
        )
        report = [
            "# Resume draft evidence",
            "",
            "This is a draft requiring manual factual and visual review.",
            "",
        ]
        for entry in data["entries"]:
            report += ["## " + entry["heading"], ""]
            for b in entry["bullets"]:
                report += [
                    "- Draft: " + b["text"],
                    "  - Source " + b["source_id"] + ": " + b["source_text"],
                    "  - Original retained: " + str(b["fallback"]),
                ]
        report += [
            "",
            "## Screening and H-1B context",
            "",
            json.dumps({k: record.get(k) for k in ("screening", "h1b")}, indent=2),
        ]
        (output / "evidence.md").write_text("\n".join(report))
        (output / "refine-prompt.txt").write_text(
            "Polish this draft resume for the saved job. Read all files first. Do not score or rank the job. "
            "Use only canonical CV facts; review every paraphrase against its source. Render and visually inspect the final one-page DOCX using the original template. Do not submit.\n"
            + "\n".join(
                f"{label}: {path}"
                for label, path in [
                    ("Canonical CV", args.cv.resolve()),
                    ("JD", args.jd.resolve()),
                    ("Step 3", args.record.resolve()),
                    ("Template", args.template.resolve()),
                    ("Draft", output / "resume-draft.docx"),
                    ("Evidence", output / "evidence.md"),
                ]
            )
        )
        manifest.update(
            status="draft_ready",
            layout=layout,
            manual_review_required=True,
            warnings=data["warnings"],
        )
        return_code = 0
    except Exception as exc:
        manifest.update(status="needs_review", error=f"{type(exc).__name__}: {exc}")
        return_code = 1
    manifest["calls"] = client.calls
    atomic_json(output / "manifest.json", manifest)
    print(json.dumps(manifest))
    return return_code


if __name__ == "__main__":
    sys.exit(main())
