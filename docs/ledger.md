# SDD ledger — plan: docs/plan.md

No git in this folder (plan header): tasks are recorded here instead of as commits.
Pre-flight: Task 6 consumes Tasks 2-5 (row shape, Profile, ResumeDoc, find_contact) and Task 7-9 consume Task 6's
application row; all names are defined once in the plan's Produces blocks. No conflicts found.

Ruling: scaffolded with `gravity init applyzer --here` (CLI 0.12.3) — user asked for the Gravity CLI only — none.
Ruling: an email to an address that came from the job (the row's hr_email or one on the posting) is an application by
email, as APPLYZER sends it: its structure (intro, three bullets, closing, under 150 words) with the tailored resume
PDF and the cover letter PDF attached. A person found on the team page gets the short cold note (under 90 words, no
attachment), as the spec says — the user's latest instruction is "use the practices and logic from the applyzer repo",
which overrides the spec's "outreach attaches nothing" for the application-by-email case — if wrong, application
emails go out bare and look like cold notes.
Ruling: APPLYZER's 7-minute gap between sends is dropped: a run times out at 15 minutes and every send already waits
on its own approval; the daily cap and one-per-company rule carry the spacing — if wrong, a batch of approvals sends
in under a minute.
Ruling: follow-up cadence per kind: an application email follows APPLYZER (day 7 and day 14, at most 2), a cold note
follows the pipeline (day 4, day 10); both close at day 21; a form-only application gets one day-7 note. APPLYZER's
follow-up tone rules are kept, its "3-4 paragraphs in HTML" is cut to two or three plain sentences (the pipeline's
"two-line nudge") — if wrong, follow-ups read short.
Task 1: complete (tests: pytest tests/test_graph.py tests/test_copied.py → 8 passed; gravity validate → valid)
Task 3: Ruling: the plan's src/profile.py is src/candidate.py — profile is a standard-library module and the import resolved to it — none.
Task 3: Ruling: the graph test 'the resume is parsed once per text' moves to Task 6, which owns prepare, the only node that loads the profile — none.
Task 3: complete (tests: pytest tests/test_logic.py → 8 passed)
Task 4: Ruling: the facts check is stricter than the plan's 'capitalised runs of two words': every capitalised word after the first must be in the source (else 'built a service in Rust' passes), with a short list of generic acronyms exempt; skills_line, name and contact line are built by code from the profile, not by the model — a posting keyword the candidate never used cannot be claimed — if too strict, more bullets are dropped and the row says how many.
Task 4: complete (tests: pytest tests/test_logic.py → 18 passed)
Task 5: complete (tests: pytest tests/test_logic.py → 27 passed)
Task 6: Ruling: every result shows one table, applications, in the order they were first prepared, and per-row actions read row numbers against the notebook's last_rows — Gravity gives only the numbers of the rows clicked, so the numbering must not change between results — if wrong, a click acts on the wrong job.
Task 6: Ruling: a page web.fetch cannot read becomes needs_text with the reason; closed needs the page (or the model reading it) to say so — the fetch result does not tell a 404 from a block — if wrong, a dead posting waits for pasted text instead of closing.
Task 6: Ruling: two jobs at one company prepared in the same run share one email naming both roles (the spec's rule), composed in prepare; the send gate for later roles is Task 7's — none.
Task 6: Ruling: store's local stand-in persists collections to GRAVITY_MEMORY_DIR (<name>.json, {key: {value}}), the folder gravity run already names — without it a local prepare's applications vanish before send_email, so the flow could not be tested from a terminal — none hosted.
Task 6: complete (tests: pytest → 43 passed)
Task 7: Ruling: gmail.send takes one file (Composio's `attachment`; the gateway uploads a {name, data: data URL}), so an application email attaches the tailored resume only; the cover letter stays a separate PDF download for the form's own upload — APPLYZER attached both — if wrong, recruiters reading the email see no letter.
Task 7: complete (tests: pytest → 51 passed)
Task 8: complete (tests: pytest → 60 passed)
Task 9: complete (tests: pytest → 69 passed; gravity validate → valid)
Task 10: complete (docs/testing.md written; real runs are Arush's)
Final review (fresh reviewer, fable): 2 Critical, 7 Important, 9 Minor. All Critical and Important enter the fix pass.
Final: Ruling: I5's fix — every result keeps one table (applications, with an `update` column per row) and the other lists become plain lines (`needs_you`) or a dict of counts (`by_stage`) — per-row numbers are the clicked table's, so a second table makes a click act on the wrong job — if wrong, the per-email detail is less visible.
Final: fixed C1 long LinkedIn wall tailored — test_c1_a_long_linkedin_wall_is_a_wall_and_a_long_posting_mentioning_sign_in_once_is_not, test_c1_a_page_the_model_finds_no_requirements_in_waits_for_the_text RED→GREEN, suite 81/81
Final: fixed C2 facts check (dates written another way, acronyms, lowercase skills, magnitude words) — test_c2a/b/c RED→GREEN, suite 81/81
Final: fixed I1 a reply or bounce in a shared thread updates every role in it — test_i1_a_bounce_in_a_shared_thread_closes_both_roles RED→GREEN, suite 81/81
Final: fixed I2 dropping one of two grouped roles clears the other's email and Prepare rewrites it — test_i2_dropping_one_of_two_grouped_roles_rewrites_the_other_roles_email RED→GREEN, suite 81/81
Final: fixed I3 a hand-sent draft is matched only to mail sent after the draft was kept, newest first — test_i3_an_old_email_to_the_same_address_is_not_taken_for_the_kept_draft RED→GREEN, suite 81/81
Final: fixed I4 an application sent by email counts as applied for ATS and fresh-thread matching — test_i4_ats_mail_matches_a_job_applied_to_by_email RED→GREEN, suite 81/81
Final: fixed I5 one table per result — test_i5_every_result_has_one_table RED→GREEN, suite 81/81
Final: fixed I6 the day-7 note goes only to an address that came with the job, and says it is a first email — test_i6_the_day_7_note_goes_only_to_an_address_that_came_with_the_job RED→GREEN, suite 81/81
Final: fixed I7 any reply in the thread from someone other than the candidate stops a follow-up — test_i7_a_reply_from_anyone_else_in_the_thread_stops_the_follow_up RED→GREEN, suite 81/81
Final: minor (deferred): two bare-URL jobs whose company the model can't read share an email with an empty company name.
Final: minor (deferred): a kept draft pressed Send before the daily follow-up run sees the hand-send can go out twice.
Final: minor (deferred): a pasted posting over the jobs item size is dropped silently and the row returns to needs_text.
Final: minor (deferred): a two-role email attaches only the clicked row's resume.
Final: minor (deferred): a "not hiring" reply suppresses the company for 730 days and later rows say "asked not to be contacted".
Final: minor (deferred): a short 404 page is reported as a sign-in page.
Final: minor (deferred): "closed to new applicants after 30 Nov" on a live posting reads as closed.
Final: minor (deferred): three jobs are prepared at once, so up to three model calls run together; the guide suggests two.
Final: minor (deferred): an address read off a job-board page is trusted as the employer's.

## Contract v2 (2026-11-02, after the user's "the agent will be v2 only")
v2: Ruling: the agent is contract v2 (0.2.0). Sources `mail` (message.search, same account as send) and `page` (web_page.get, default Exa); writes `send` (message.send, approved) and `draft` (message.create, same as send). The v1 version is kept in `.v1/`.
v2: Ruling: Hunter is dropped (no v2 app, no built-in); a found person needs an address printed on the company's own pages, else the row says "no address" — the user agreed — if wrong, fewer rows have someone to write to.
v2: Ruling: Exa's people search is dropped with Hunter (v1's exa.search); a contact comes only from the company's own pages — none.
v2: Ruling: v2's message records name no recipients, so a kept draft sent by hand is found by searching Sent for its address (one search per kept draft), and the follow-up pre-flight reads the inbox once and picks out each thread — v2 maps no per-thread message read for Gmail — if wrong, a reply that never reached the inbox (filtered away) is missed.
v2: Ruling: searches use Gmail's syntax (in:inbox newer_than:2d); an Outlook pick would need its own — Gmail is the default app — if wrong, Outlook users get empty searches.
v2: Ruling: a file goes as {name, data: base64}, the engine's form, not v1's data URL — none.
v2: gravity-schema (gravity-cli branch feat/gmail-v2-attachment-thread-draft, not committed): Gmail's message.send, message.create and message.reply map the act's `file` to `attachment`; message.create maps `draft.thread` to `thread_id`; message.send returns `thread`. 493 schema tests pass; checked through core-v2's gravity-connect engine with a fake Gmail. Until it is released and on dev, a v2 send drops the resume and returns no thread.

## The resume template and the sheet (2026-11-03, after "continue" on both)
Ruling: the resume PDF follows APPLYZER's resume.cls: US Letter, 11pt, 0.4in margins, New Computer Modern (Typst's own copy of the LaTeX face), the name in capitals, contact lines centred with ⋄, ruled capital headings, Objective, Skills as a bold two-column table right after Education, Experience as role and dates then employer and location, Projects as "Title. (tools)". Entries gain `location` and `tech`, both checked against the resume like every other field — APPLYZER's Extra-curricular and Leadership sections are not separate (the model puts them under their own heading as entries) — if wrong, a resume with those sections reads slightly differently.
Ruling: an optional `tracker` write (row.upsert, Google Sheets by default, required: false): after every run, the applications that changed are upserted, one row each, keyed on the posting's link (the first column), with APPLYZER's columns plus company, role and the reply. A sheet not set up is skipped silently (gravity.capabilities() has no writes.tracker); one that fails is named in could_not — if wrong, a person who never set up a sheet sees nothing about it, by design.
2026-11-03: Decision (Arush): follow-ups stay drafts in Gmail for the first weeks; auto-send is revisited once the numbers are in. To get the numbers, each follow-up draft is remembered on its application (follow_up_drafts: n, at, text); the daily run matches later Sent mail in the same thread, records when it went and how much it was changed (edit_ratio), and the weekly report says "N of M follow-up drafts sent by you, K unchanged" (unchanged: under 5% edited).
2026-11-03: gravity-cli PR #46 rebuilt on main (0.13.6 had landed with threads and a send's thread from PR #45): it is now the attachment alone, as release 0.13.7.
2026-11-03: Fix: the v2 page read passed the URL as a bare keyword; the engine maps web_page.get from {args.url}, so it is now gravity.read("page", args={"url": url}). Found while reading Sid's jobs-scout, which calls it the right way.
