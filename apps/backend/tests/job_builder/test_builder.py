"""Adapter checks run without cloud SDKs or a model server."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.job_builder.core import (
    Entry,
    Master,
    Ollama,
    build_content,
    rewrite_issues,
    selected_ids,
)
from app.job_builder.docx import render_one_page


class FakeClient:
    def ask(self, task: str, payload: dict) -> dict:
        if "projects" in payload:
            return {"ids": ["p1", "p2"]}
        if "allowed" in payload:
            return {"skills": ["Python"]}
        return {
            "edits": [
                {"source_id": k, "text": v.replace("90%", "99%")}
                for k, v in payload["entry"]["bullets"].items()
            ]
        }


class BuilderTests(unittest.TestCase):
    def test_unknown_and_repeated_ids_fail(self) -> None:
        for ids in (["x"], ["a", "a"]):
            with self.assertRaises(ValueError):
                selected_ids(ids, {"a": "source"}, len(ids))

    def test_numbers_and_qualifiers(self) -> None:
        self.assertIn(
            "new numeric claim",
            rewrite_issues("Achieved 90% accuracy", "Achieved 99% accuracy", []),
        )
        self.assertIn(
            "dropped qualifier: simulated",
            rewrite_issues("Tested simulated traffic", "Tested traffic", []),
        )
        self.assertTrue(
            rewrite_issues("Set a 90% ship gate", "Achieved 90% accuracy", [])
        )
        self.assertIn(
            "added skill: Docker",
            rewrite_issues("Built an API", "Built an API with Docker", ["Docker"]),
        )

    def test_fallback_keeps_evidence_and_metadata(self) -> None:
        def entry(key: str) -> Entry:
            return Entry(
                key,
                "Example",
                ["Engineer", "2023"],
                {key + "a": "Achieved 90% accuracy", key + "b": "Built a parser"},
            )

        master = Master(
            {"Programming Languages": "Python"},
            [entry("e")],
            [entry("p1"), entry("p2")],
            [entry("edu")],
        )
        data = build_content(master, "Example JD", FakeClient())
        self.assertEqual(data["entries"][0]["metadata"], ["Engineer", "2023"])
        bullet = data["entries"][0]["bullets"][0]
        self.assertTrue(bullet["fallback"])
        self.assertEqual(bullet["text"], bullet["source_text"])
        self.assertTrue(data["manual_review_required"])
        self.assertNotIn("score", data)

    def test_invalid_edit_selection_gets_only_one_retry(self) -> None:
        class BadClient(FakeClient):
            corrections = 0

            def ask(self, task: str, payload: dict) -> dict:
                if "entry" in payload:
                    if "previous" in payload:
                        self.corrections += 1
                    return {"edits": []}
                return super().ask(task, payload)

        def entry(key: str) -> Entry:
            return Entry(
                key, "Example", [], {key + "a": "Built API", key + "b": "Tested API"}
            )

        client = BadClient()
        master = Master(
            {"Programming Languages": "Python"},
            [entry("e")],
            [entry("p1"), entry("p2")],
            [],
        )
        with self.assertRaises(ValueError):
            build_content(master, "JD", client)
        self.assertEqual(client.corrections, 1)

    def test_cache_avoids_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = Ollama(Path(temp))

            class Response:
                def __enter__(self) -> object:
                    import io

                    return io.StringIO(
                        json.dumps({"message": {"content": '{"ids": ["a"]}'}})
                    )

                def __exit__(self, *args: object) -> bool:
                    return False

            with patch("urllib.request.urlopen", return_value=Response()) as request:
                self.assertEqual(client.ask("task", {}), client.ask("task", {}))
                self.assertEqual(request.call_count, 1)
                req = request.call_args.args[0]
                self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/chat")
                self.assertNotIn("format", json.loads(req.data))

    def test_missing_renderer_never_ready(self) -> None:
        with patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                render_one_page(Path("template"), Path("output"), {}, {}, "")

    def test_rejected_job_stops_before_reading_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / "record.json"
            record.write_text(json.dumps({"screening": {"decision": "reject"}}))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.job_builder",
                    "--record",
                    str(record),
                    "--cv",
                    "missing",
                    "--jd",
                    "missing",
                    "--template",
                    "missing",
                    "--slots",
                    "missing",
                    "--output",
                    temp,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must explicitly pass", result.stderr)

    def test_docx_preserves_package_and_split_contact(self) -> None:
        from zipfile import ZipFile

        from lxml import etree

        from app.job_builder.docx import W, write_docx

        with tempfile.TemporaryDirectory() as temp:
            root = etree.Element(W + "document", nsmap={"w": W[1:-1]})
            body = etree.SubElement(root, W + "body")
            for index in range(39):
                para = etree.SubElement(body, W + "p")
                run = etree.SubElement(para, W + "r")
                etree.SubElement(run, W + "t").text = str(index)
            contact = body[1]
            contact[0][0].text = "Old City"
            run = etree.SubElement(contact, W + "r")
            etree.SubElement(run, W + "t").text = ", CA | "
            link = etree.SubElement(contact, W + "hyperlink")
            etree.SubElement(
                etree.SubElement(link, W + "r"), W + "t"
            ).text = "email@example.com"
            etree.SubElement(body, W + "sectPr")
            template = Path(temp) / "template.docx"
            output = Path(temp) / "draft.docx"
            slots = json.loads(
                (
                    Path(__file__).parents[2]
                    / "job-builder-config/buildkite-slots.json"
                ).read_text()
            )
            slots["expected"] = {str(i): str(i) for i in (2, 5, 16, 35)}
            with ZipFile(template, "w") as archive:
                archive.writestr("word/document.xml", etree.tostring(root))
                archive.writestr("word/styles.xml", b"unchanged")
            write_docx(
                template,
                output,
                {"skills": ["Python"], "entries": [], "education": []},
                slots,
                11,
                "New City, CA",
            )
            with ZipFile(output) as archive:
                self.assertEqual(archive.read("word/styles.xml"), b"unchanged")
                doc = etree.fromstring(archive.read("word/document.xml"))
                self.assertIn(
                    "New City, CA | email@example.com", "".join(doc.itertext())
                )
                self.assertNotIn("Old City", "".join(doc.itertext()))
                self.assertIsNotNone(doc.find(".//" + W + "sectPr"))

    def test_import_does_not_load_provider_or_scoring(self) -> None:
        for name in (
            "litellm",
            "app.llm",
            "app.services.improver",
            "app.services.scoring",
        ):
            self.assertNotIn(name, sys.modules)


if __name__ == "__main__":
    unittest.main()
