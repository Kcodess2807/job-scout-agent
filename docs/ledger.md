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

## The search joins (2026-11-03, Sid's jobs-scout vendored; Arush chose "his code into my repo")
Ruling: his `src/` is vendored unchanged under `src/scout/` (his `agent.py`, `output.py`'s graph side and `state.py` left out; the three helpers his graph kept are copied into `src/discover.py` with attribution), and his tests under `tests/scout/` with his conftest — unchanged files, so a newer commit of his repo copies over the same way — if wrong, two copies drift; the cure is for him to commit here.
Ruling: one graph. `discover` runs his `engine.scout` with this agent's sources by his names (jobs→search, pages→page, jobs_more→search_more, answers, similar) and writes the qualified jobs (near misses left out) through the seam as `source: discovery`, `picked: False`; `pick` (per row) marks one picked and runs `prepare`; `prepare` only takes picked rows. A row pasted by the person is picked at once. The board shows every job: found, to prepare, or its application — one table still — if wrong, a found job the person never picks sits on the board until declined.
Ruling: the default run routes to `discover` when nothing is pasted and nothing picked waits; else `prepare`. `jobs` input no longer required — a first run with just a resume searches — if wrong, a person who wanted only to paste links gets a search they didn't ask for (they can paste in the same run).
Ruling: his notebook lives under `memory["scout"]` (seen, rejected, prefs, compact profile); notebook 256 KB; `timeout_seconds` 1500 for his 22-minute search budget; `page` source default app changed from Exa to Composio Search, which he verified live for LinkedIn and ATS pages — if wrong, the contact finder's team-page reads go through Composio Search too.
Ruling: no `gravity.ask` in discover (`ask=None`): a missing role or years takes his disclosed defaults instead of pausing the run — the v2 SDK's ask is a plain function and a hosted pause re-runs the node — if wrong, a vague resume gets a default role.
Ruling: his tests need UTF-8 as the default encoding (they read fixtures without one; the platform runs UTF-8): `PYTHONUTF8=1` for pytest on Windows.
Seen: his engine sent no second-source traffic when `search_more`, `answers` and `similar` are not set up (the fake raises as the gateway would), and his `enrich` and evaluator treat a failing model as no model, so the demo's stand-in model runs the search code-only.
Deferred: his `action-url` for a found job on a board copy may be the board's page, not the employer's form; his verification status is shown in the row's note.
## The first real run (2026-11-03, OpenRouter back; Arush's Gmail on dev's gateway)
Seen: Prepare parsed the sample resume and GitHub in 20 model calls (2 min); a dead page was reported as needing the posting's text; the pasted posting was tailored with 0 lines dropped; the letter and the email read well and quoted only the resume; Send held at the gateway with recipient, subject and the resume file, refused -> a draft in Gmail, approved by Arush -> sent from his Gmail; follow-ups and the report ran; the real search found 5 fitting jobs in 197 s with reasons; picking one read the real posting and the company's pages (nobody named, so no address, as designed).
Fix: an action run carries no resume text; Prepare now reuses the profile already in the notebook (it refused with "no resume").
Fix: the model's output limit 1500 cut the cover letter off (a reasoning model spends part of it); 4000.
Fix: a must-have matches the resume under its other name (Postgres/PostgreSQL) and a posting's alternates count as one ("Go (Golang)", "SQL/NoSQL", "AWS/GCP/Azure"); must_have is asked for as named technologies, never duties ("backend services", "distributed systems" read as missing skills).
Fix: a source the person skipped or whose app needs reconnecting (Exa) is asked once per run, not once per query (eight 5-second refusals).
Fix: the letter check's findings ride on the row (`letter_problems`), beside the letter.
Finding (platform): Gmail's send and draft receipts carried no thread (Composio nests it under response_data); gravity-cli PR #51 (0.13.12). Until dev has it, a reply to a sent email can match only by the company's domain. For the live test the thread was set by hand to the sent message's id, which is Gmail's thread id for a thread's first message.
Finding (platform): the gateway dropped the draft from any Gmail send since schema 0.13.7 (it read `{file}` as "a file write"): core PR #661. Until it deploys, a send goes without the attachment; the file does reach the gateway.
Finding (CLI): `gravity run` did not find the held approval it polled for, so no prompt; a helper decided it at the gateway's /approvals. To look at in gravity-cli.

## The admin run on dev (2026-11-03, a teammate: "agent admin side run bhi nahi ho raha, kuch toh sheet ka issue aa raha hai")
Finding: dev shows no run of applyzer at all. The platform's check_install fills every unpicked write with its default app and then checks the setup; the optional sheet got googlesheets and the check refused "googlesheets needs a Spreadsheet", so no run was ever minted. The platform function pins gravity-schema 0.13.6, before even the 0.13.9 CLI fix, and that fix only honoured an explicit `app: null`, which a platform setup that omits the write never sends.
Ruling: fixed where it belongs, gravity-schema 0.13.10 (gravity-cli PR #49): an optional binding whose default app needs a place is not filled in; the consumer opts in. The platform function needs the bump (core follow-up).
Ruling: until the platform has it, the manifest's `tracker` carries no default_app (0.6.1): the platform then fills nothing for it, the setup passes, and a person who wants the sheet picks Google Sheets and a spreadsheet at setup. The warning validate prints for a write without a default is accepted for now; the default comes back once dev runs 0.13.10.

## The page on Gravity (2026-11-03, Arush: "push it to gravity so there on the agent explore page we can look it and work on the output")
Finding: the explore card and the agent's page are rendered by apps/web from the manifest (name as the title, description clamped to three lines, kind as "Chat", app logos from the tools) and from `about` (the control plane's agent-about.ts splits the description into steps at clauses that start with a verb, at most six); a run's result is drawn by its shape (lib/run-document.ts) unless it carries `_layout`. No HTML, image or page of the agent's own is shipped: the design links Arush sent (3D, scroll, shaders) have nowhere to go.
Ruling: the description is rewritten so its clauses split into the six steps that matter (finds, takes, tailors, sends, tracks, drafts); input labels are short noun phrases, since the page lists them as the form and as a "Uses ..." step.
Ruling: every result carries `_layout`: cards per application (title company, subtitle role, stage chip, this run's update in accent, fit, contact, the email, the cover letter, a button to the posting) — if wrong, an older page ignores it and draws the rows as a table, as before.
Seen: the team's own `job-scout` (Devansh, v1, 1.0.18) has a hand-made view in apps/web keyed on that agent name and its own result shape; this agent keeps the name `applyzer` and the generic page.

## The same code on every mail app (2026-11-03, Arush: "it should give the user complete flexibility in adapting the tools he uses")
Finding: the mail reads spoke Gmail (`in:sent to:x newer_than:30d`); on Outlook the hand-sent check, the follow-up pre-flight and the manual reply check would have read nothing or the wrong mail, and the resume would not have gone.
Ruling: `mail` is message.list with `window: 30d` (the farthest any run looks), and every read names a standard mailbox, `filters.folder` (inbox, sent, drafts): a new shape filter in gravity-schema 0.13.8, mapped to Gmail labels and Outlook folders, with `to` on both apps' records — if wrong, an older gateway ignores the filter and warns, and the code still picks what it needs out of the records (only wider).
Ruling: a per-call window is not a v2 read argument (filters, limit, text, id, target only), so the window is the manifest's and anything narrower (two days, an address, a thread) is picked out in code; Sent is read once per run, not per address.
Ruling: Outlook's send, draft and reply carry `{file}` (0.13.8); a reply draft gets the file by a second step (Outlook's create-reply takes none), gated on a reply having been made and a file given.
Deferred: Outlook's send of a new email returns no thread; a follow-up to it goes unthreaded until the agent makes a draft first and sends that (message.send_draft).

## Applying and tracking, refined (2026-11-03, Arush: "work on the refinement of the job application part and tracking the mails and follow up parts")
Ruling: a person named on the company's pages gets their address from the `hunter.find_email` + `hunter.verify_email` built-ins when the page prints none (0.4.0; `built_ins:` in the manifest, `Gravity.find_email`, `research.contact(find=)`); a printed address wins; invalid is dropped, risky is used and labelled — if wrong, a risky address bounces, and the bounce rule closes the row and says so.
Ruling: the Hunter answer is read tolerantly (`_dig`: the first `email` / `status` anywhere in it), because the gateway's exact shape was not readable here (the tool is Composio's HUNTER_EMAIL_FINDER on Gravity's account) — if wrong, `find_email` answers {} and the row says "nobody found", never a wrong address.
Fix (deferred minor closed): Send on a kept draft runs the hand-sent check first (`hand_sent`, one function shared with the daily run, limited to the rows pressed); a draft already sent from Gmail is recorded as the candidate's and skipped: "not sent again: you sent it yourself".
Ruling: `status_update` is a reply kind (rule: "application … under review / reviewed by / moved to / shortlisted" with no question and no rejection words; else the model): recorded, not urgent, not answered — if wrong, a status mail that hides a question waits for the next one.
Ruling: the follow-up prompt gets what was already written to the company (the sent text and earlier follow-ups) and must add one new thing; the second follow-up asks whether the role is still open.
Ruling: the report adds `by_email_kind` and `follow_ups.replies_after_one`, over all time, so the drafts-only decision can be revisited on numbers.
Skipped: Greenhouse's form questions (`?questions=true`) in the answer sheet — until a real run shows which forms the person meets; the answer bank already covers the questions recruiters ask by mail.
Local CLI moved to gravity-cli 0.13.7 (`uv tool install --force gravity-cli==0.13.7`; `uv tool upgrade` had said nothing to upgrade); `built_ins:` validates there.

2026-11-03: Fix: a found job's domain from the search is now the registrable domain (careers.zentrix.ai -> zentrix.ai); the contact finder read team pages on the careers host and found nobody. Caught walking the demo. Demo: a next-step card and progress strip on Live, the orchestrator's button named for the next step, a sample resume the search's fixtures match, a team page for one found company, jobs empty by default so the first run searches.
