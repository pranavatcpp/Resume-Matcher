# Local job-agent builder

This fork adds a standalone Step 4 adapter to Resume Matcher. The existing web
application is unchanged. This entry point does not invoke its scoring, ranking,
keyword insertion, or iterative ATS optimization pipeline.

## Flow

1. Receive a saved JD, canonical Markdown CV, and Step 3 record. Require
   `screening.decision` equal to `pass`; reject `status: rejected_code`.
   Collection, eligibility rules and H-1B verification belong to the parent agent.
2. Use local `qwen3.5:4b` to select two projects and exact canonical skills.
3. Give the model one experience/project at a time to select and shorten bullets.
   Each bullet must reference an ID from that same entry. Names, titles, dates,
   education and project technology lines come directly from the canonical CV.
4. Reuse upstream numeric-preservation and whole-term matching checks. Reject
   new numbers, added known skills, and lost simulation/gate qualifiers by keeping
   the original bullet. Unknown IDs or malformed responses fail the build.
5. Preserve the supplied DOCX package, replace document body text using configured
   template paragraph slots, and render through LibreOffice. Try body fonts from
   11.5 to 9 points; require exactly one PDF page. Never silently drop bullets.
6. Write a DOCX draft, verification PDF, evidence ledger, manual refine prompt,
   and manifest. `draft_ready` means a rendered draft exists, **not** final factual
   or visual approval. `needs_review` means the build failed; do not promote it.

All model calls go to `http://127.0.0.1:11434/api/chat`. There is no paid/cloud
fallback. Calls have a 120-second timeout and a 1,000-token output limit. Parsed
responses are cached by model, adapter version and full input. Invalid cached
responses still fail validation; remove the relevant cache entry before retrying.
No model server is installed or started by this adapter.

## Run

Use Python 3.13 or later, `lxml`, LibreOffice (`soffice`) and Poppler (`pdfinfo`).
The standalone adapter needs only `lxml` beyond the Python standard library;
the full upstream API dependencies are unnecessary.

```bash
python -m pip install -r apps/backend/job-builder-requirements.txt
PYTHONPATH=apps/backend python -m app.job_builder \
  --cv /absolute/path/cv.md \
  --jd /absolute/path/job-description.md \
  --record /absolute/path/step3-record.json \
  --template /absolute/path/template.docx \
  --slots apps/backend/job-builder-config/buildkite-slots.json \
  --output /absolute/path/new-output-directory
```

The included slot profile targets the user's supplied Buildkite resume layout;
other templates require their own slot map. Section labels are verified before
writing. Template name and email must match the canonical CV. Contact hyperlinks
are retained from the supplied template; review them manually. Residence is read
from the canonical CV, not inferred from the employer's location.

Canonical Markdown uses `## Technical Skills`, `## Work Experience`, `## Projects`,
and `## Education`, with `###` entry headings and `- ` evidence bullets. This
profile requires Python in Programming Languages and at least two projects.
Source IDs refer to lines after HTML comments are removed; the ledger includes
exact source text so reviewers need not depend on line numbers.

Use a new output directory for each run. Exit code 0 produces `draft_ready`;
exit code 1 produces `needs_review`; invalid CLI/preflight input exits 2.
The parent agent should only consume artifacts after checking the manifest and
should retain separate evidence-ready, draft-ready, and final-review states.
The adapter does not update the parent agent's state or launch its scheduler.

## Limits and checks

A 4B model can still introduce semantic overclaims that simple checks miss.
Every paraphrase therefore requires human/manual final review against the ledger.
The one-page check verifies pagination; it does not prove good visual layout.
Model quality and speed must be measured locally before enabling unattended builds.
Malformed bullet selections receive one correction attempt. Invalid JSON, project/skill selections, or a second invalid bullet selection fail the build.

Run focused tests without a model server:

```bash
PYTHONPATH=apps/backend python -m unittest discover -s apps/backend/tests/job_builder -v
```

Keep CVs, JDs, templates and generated artifacts outside this public repository.
