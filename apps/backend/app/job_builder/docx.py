"""Preserve a user's OOXML template and fit a reviewed draft to one page."""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}


def write_docx(
    template: Path,
    output: Path,
    data: dict[str, Any],
    slots: dict[str, Any],
    font: float,
    location: str,
) -> None:
    with ZipFile(template) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    # lxml preserves namespace prefixes used in OOXML compatibility attributes.
    from lxml import etree

    root = etree.fromstring(parts["word/document.xml"])
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("Template has no document body")
    paragraphs = body.findall("w:p", NS)
    for index, expected in slots["expected"].items():
        actual = "".join(paragraphs[int(index)].itertext())
        if actual != expected:
            raise ValueError(
                f"Template slot {index} does not match configured structure"
            )

    def para(slot: str, text: str | None = None, body_font: bool = True) -> Any:
        p = copy.deepcopy(paragraphs[slots[slot]])
        if text is not None:
            run = p.find("w:r", NS)
            run = copy.deepcopy(run) if run is not None else etree.Element(W + "r")
            for child in list(p):
                if child.tag != W + "pPr":
                    p.remove(child)
            for child in list(run):
                if child.tag != W + "rPr":
                    run.remove(child)
            etree.SubElement(run, W + "t").text = text
            p.append(run)
        if body_font:
            for run in p.findall(".//w:r", NS):
                props = run.find("w:rPr", NS)
                if props is None:
                    props = etree.SubElement(run, W + "rPr")
                for tag in ("sz", "szCs"):
                    node = props.find("w:" + tag, NS)
                    if node is None:
                        node = etree.SubElement(props, W + tag)
                    node.set(W + "val", str(int(font * 2)))
        return p

    contact = para("contact", body_font=False)
    if location:
        # Only replace the plain location prefix; retain actual link runs and relationships.
        prefix_nodes = []
        for child in contact:
            if child.tag == W + "hyperlink":
                break
            prefix_nodes.extend(child.findall(".//w:t", NS))
        prefix = "".join(node.text or "" for node in prefix_nodes)
        if not prefix_nodes or "|" not in prefix:
            raise ValueError(
                "Contact slot must start with a plain location followed by |"
            )
        prefix_nodes[0].text = location + " |" + prefix.split("|", 1)[1]
        prefix_nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for node in prefix_nodes[1:]:
            node.text = ""
    new = [
        para("name", body_font=False),
        contact,
        para("skills_heading", body_font=False),
        para("skill", "Programming Languages: Python"),
        para("skill", "Relevant Skills: " + ", ".join(data["skills"])),
        para("experience_heading", body_font=False),
    ]
    for group in ("experience", "projects"):
        if group == "projects":
            new.append(para("projects_heading", body_font=False))
        for entry in data["entries"]:
            if entry["group"] != group:
                continue
            meta = entry["metadata"]
            if group == "experience":
                if len(meta) < 2:
                    raise ValueError(
                        "Experience needs canonical role and date metadata"
                    )
                company, _, place = entry["heading"].partition(" - ")
                new += [
                    para("entry_heading", company + " | " + meta[0]),
                    para("metadata", (place + " | " if place else "") + meta[1]),
                ]
            else:
                new += [
                    para("project_heading", entry["heading"]),
                    para("metadata", meta[0] if meta else ""),
                ]
            new += [para("bullet", b["text"]) for b in entry["bullets"]]
    new.append(para("education_heading", body_font=False))
    for education in data["education"]:
        new.append(para("education_name", education["heading"]))
        meta = [
            x for x in education["metadata"] if not x.startswith("Relevant Coursework:")
        ]
        new.append(para("metadata", " | ".join(meta)))
    for p in paragraphs:
        body.remove(p)
    for i, p in enumerate(new):
        body.insert(i, p)
    parts["word/document.xml"] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    with ZipFile(output, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    with ZipFile(template) as before, ZipFile(output) as after:
        if any(
            before.read(n) != after.read(n)
            for n in before.namelist()
            if n != "word/document.xml"
        ):
            raise ValueError("Preserve-only template part changed")


def render_one_page(
    template: Path,
    output: Path,
    data: dict[str, Any],
    slots: dict[str, Any],
    location: str,
) -> dict[str, Any]:
    soffice = shutil.which("soffice")
    pdfinfo = shutil.which("pdfinfo")
    if not soffice or not pdfinfo:
        raise RuntimeError(
            "Install LibreOffice and Poppler before building; no unverified ready state is emitted"
        )
    for size in (11.5, 11, 10.5, 10, 9.5, 9):
        write_docx(template, output, data, slots, size, location)
        with tempfile.TemporaryDirectory(prefix="resume-fit-") as temp:
            directory = Path(temp)
            result = subprocess.run(
                [
                    soffice,
                    "-env:UserInstallation=" + (directory / "profile").as_uri(),
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(directory),
                    str(output.resolve()),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            pdf = directory / (output.stem + ".pdf")
            if result.returncode or not pdf.exists():
                raise RuntimeError(
                    "LibreOffice rendering failed: "
                    + (result.stderr or result.stdout)[-300:]
                )
            info = subprocess.run(
                [pdfinfo, str(pdf)],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            found = re.search(r"^Pages:\s+(\d+)", info.stdout, re.M)
            if not found:
                raise RuntimeError("Unable to verify rendered page count")
            if int(found[1]) == 1:
                shutil.copy2(pdf, output.with_suffix(".pdf"))
                return {"pages": 1, "font_pt": size, "visual_review_required": True}
    raise ValueError(
        "Draft exceeds one page at 9pt; content review required. No bullets silently deleted."
    )
