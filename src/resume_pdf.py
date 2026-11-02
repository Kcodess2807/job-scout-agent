"""The tailored resume as a PDF, laid out like APPLYZER's LaTeX template (app/templates/resume.cls and
dynamic_resume_generator.py): the name in capitals, contact lines centred with diamonds between, ruled capital
headings, an Objective, Education then Skills as a two-column table, Experience with the role and dates on one line
and the employer and location under it, Projects as "Title. (tools)" with their bullets. US Letter, 11pt, 0.4in
margins, Computer Modern. TeX can't run in the agent's sandbox, so Typst (a pip wheel) lays it out; content reaches
the template as JSON through `sys.inputs`, so no text is ever spliced into markup and nothing needs escaping."""

from __future__ import annotations

import json
import re

import typst

TEMPLATE = r"""
#let data = json(bytes(sys.inputs.data))
#let s(x) = if x == none { "" } else { str(x) }

#set page(paper: "us-letter", margin: 0.4in)
#set text(font: ("New Computer Modern", "Libertinus Serif"), size: 11pt, lang: "en")
#set par(justify: false, leading: 0.55em, spacing: 0.6em)

#let section(title, body) = {
  v(0.85em, weak: true)
  block(breakable: false, below: 0.45em, spacing: 0em)[
    #text(weight: "bold")[#upper(title)]
    #v(0.3em)
    #line(length: 100%, stroke: 0.4pt)
  ]
  pad(left: 1.5em, body)
}

#let bullets(items) = if items.len() > 0 {
  list(marker: [$dot.c$], indent: 0em, body-indent: 0.45em, spacing: 0.32em, ..items.map(b => [#s(b)]))
}

#let job(e) = block(breakable: false, below: 0.6em)[
  #text(weight: "bold")[#s(e.at("title", default: ""))] #h(1fr) #s(e.at("dates", default: ""))
  #if s(e.at("organisation", default: "")) != "" or s(e.at("location", default: "")) != "" [
    \ #s(e.at("organisation", default: "")) #h(1fr) #emph(s(e.at("location", default: "")))
  ]
  #bullets(e.at("bullets", default: ()))
]

#let school(e) = block(breakable: false, below: 0.45em)[
  #text(weight: "bold")[#s(e.at("title", default: ""))]#if s(e.at("organisation", default: "")) != "" [, #s(e.at("organisation", default: ""))]
  #h(1fr) #s(e.at("dates", default: ""))
  #bullets(e.at("bullets", default: ()))
]

#let project(e) = block(breakable: false, below: 0.45em)[
  #text(weight: "bold")[#s(e.at("title", default: "")).]
  #if e.at("tech", default: ()).len() > 0 [ #emph[(#e.at("tech").join(", "))]]
  #bullets(e.at("bullets", default: ()))
]

#let skills() = if data.at("skills_rows", default: ()).len() > 0 {
  section("Skills", grid(columns: (auto, 1fr), column-gutter: 2.4em, row-gutter: 0.5em,
    ..data.skills_rows.map(r => (text(weight: "bold")[#s(r.at(0))], [#s(r.at(1))])).flatten()))
}

#align(center)[#text(size: 20pt, weight: "bold")[#upper(s(data.name))]]
#v(0.5em)
#for row in data.at("contact_lines", default: ()) [
  #align(center)[#row.map(x => [#x]).join([ $diamond.small$ ])]
]

#if s(data.at("summary", default: "")) != "" { section("Objective", s(data.summary)) }

#for sec in data.at("sections", default: ()) {
  let heading = s(sec.at("heading", default: "Experience"))
  let kind = lower(heading)
  section(heading, {
    for e in sec.at("entries", default: ()) {
      if kind.contains("project") { project(e) } else if kind.contains("education") { school(e) } else { job(e) }
    }
  })
  if kind.contains("education") { skills() }
}
#if not data.at("sections", default: ()).any(sec => lower(s(sec.at("heading", default: ""))).contains("education")) { skills() }
"""


def _bare(link: str) -> str:
    return re.sub(r"^(https?://)?(www\.)?", "", link).rstrip("/")


def contact_lines(contact_line: str) -> list[list[str]]:
    """APPLYZER's two address lines: phone and place first, then email and links (shown without https://)."""
    items = [x.strip() for x in (contact_line or "").split("|") if x.strip()]
    online = [x for x in items if "@" in x or x.startswith("http") or ("." in x and " " not in x and not x[:1].isdigit())]
    rest = [x for x in items if x not in online]
    return [line for line in (rest, [_bare(x) for x in online]) if line]


def skills_rows(skills_line: str) -> list[list[str]]:
    """'Languages: Go, SQL. Tools: Docker' -> [['Languages', 'Go, SQL'], ['Tools', 'Docker']]."""
    rows = []
    for part in re.split(r"\.\s+(?=[A-Z][\w /&-]*:)", (skills_line or "").strip().rstrip(".")):
        cat, sep, items = part.partition(":")
        if part.strip():
            rows.append([cat.strip(), items.strip()] if sep else ["Skills", part.strip()])
    return rows


def build_pdf(rewrite: dict) -> bytes:
    """The resume (tailor.ResumeDoc as a dict) as a PDF. Fonts come with Typst itself, so the result is the same on
    this machine and in the sandbox."""
    data = {**rewrite, "contact_lines": contact_lines(rewrite.get("contact_line") or ""),
            "skills_rows": skills_rows(rewrite.get("skills_line") or "")}
    return typst.compile(TEMPLATE.encode("utf-8"), sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
                         ignore_system_fonts=True)
