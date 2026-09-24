# Preliminary round — submission checklist

**Deadline:** Tuesday 22 September 2026, 12:00 noon.

## The five required components

| # | Component | What to submit | Status |
|---|---|---|---|
| 1 | Project description | The short text below. Paste it into the form. | Ready |
| 2 | Demo video link | YouTube, **Unlisted** or Public (not Private), at most 5:00. Script: `docs/VIDEO_SCRIPT.md` (kept locally, not in the repo) | **To record** |
| 3 | GitHub repository link | https://github.com/TayEeZhan/cleardraft (the README has setup and the written responses) | Ready |
| 4 | Live prototype link | https://cleardraft-one.vercel.app | Ready |
| 5 | Slide deck / documentation link | The deck, downloaded as PDF and uploaded to Google Drive (see below) | **To upload** |

## Project description (paste into the form)

> **ClearDraft: shipping paperwork, checked in seconds.**
>
> **The problem.** A shipping documentation team checks every draft Bill of
> Lading against its Shipping Instruction by hand: seven fields, up to 10
> minutes per pair. The work comes through a shared inbox full of other mail.
> A missed field means a wrong Bill of Lading, followed by amendment fees,
> bank discrepancy fees and delayed cargo.
>
> **What ClearDraft does.**
> - Sorts every email.
> - Reads both documents: text, PDF, Excel or Word.
> - Compares the seven fields exactly and shows the source line behind every
>   value.
> - Drafts the reply.
> - Escalates anything it cannot prove to a person.
>
> People confirm each result and send every reply.
>
> **AI and cloud.** AI (Claude Haiku 4.5) is only a fallback, and every value
> it returns must appear word for word in the document. ClearDraft runs on
> Vercel with Upstash Redis.
>
> **Results.**
> - On the organiser's 520 emails it caught all 46 planted discrepancies.
> - On unseen mail, email sorting rose from 67% to 100% with the AI fallback,
>   with no wrong AI decisions.

(About 140 words. A longer version is in [PROJECT_DESCRIPTION.md](PROJECT_DESCRIPTION.md).)

## Before you submit

**The deck**

1. Download it as a PDF and upload it to Google Drive.
2. Set sharing to **Anyone with the link → Viewer**.

**The video**

3. Record with all three cameras on (you open on camera), following `docs/VIDEO_SCRIPT.md` (local file).
4. Keep it **5:00 or less**: every 30 s over costs 1 mark.
5. Upload to YouTube as **Unlisted**.

**Check every link in an incognito window**

6. The video plays, the Drive PDF opens, the repo is public, and the live
   site loads.

**Before recording, on the live site**

7. Run `localStorage.clear()` so "Your mail" starts at (0).
8. Check that https://cleardraft-one.vercel.app/api/health returns
    `"status":"ok"`.

**Finally**

9. Submit all five components on the hackathon site.

## Mark as same and the submission

"Mark as same" (a signed-in clerk teaching ClearDraft one exact SI/BL pair,
see [README.md](../README.md#mark-as-same)) never touches the organiser
submission. `scripts/run_pipeline.py` never passes `known_equal` to
`compare()`, so `out/submission.json` is byte-identical whether or not any
account has saved pairs. The **Submission file (JSON)** export in the UI is
the same deliberate exception (`buildSubmissionJson` in `web/app.js`): it
always exports the originally checked result, never a marked-as-same
re-count, because it exists to score the pipeline itself.

## Owners

| Task | Who |
|---|---|
| Record the video (screen share plus three cameras) | Ee Zhan drives the demo; all three speak |
| Upload the video and the deck, share the links | [Name] |
| Submit the form | [Name] |
