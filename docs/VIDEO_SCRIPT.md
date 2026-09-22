# ClearDraft — demo video script

**Hard limit 5:00** (1 mark off for every 30 s over). This script runs about
**4:45**. Speaking pace: about 150 words a minute. If a rehearsal runs long,
cut the parts marked **(cut if over)**.

**Organiser rules this script meets:** team and project name, the problem
and why it matters, the tech stack, a live demo, and impact with metrics and
feedback. **All three of you must appear on camera.** The minimum is a slide
with all three photos, but cameras on is better.

Slide numbers refer to the deck. Presenters: **EZ** = Ee Zhan,
**SK** = Sheng Kuan, **ZQ** = Zi Qi. Replace `[Team name]` everywhere.

---

## Before you record (10 minutes)

1. **Cameras.** Record in one Zoom, Meet or Teams call with all three
   cameras on while one person shares the screen. Record in gallery or
   speaker view so faces stay visible.
2. **Browser.** Use Chrome, full screen (F11), zoom 110%, no bookmarks bar.
   Open these tabs in this order:
   1. the deck in Present mode;
   2. https://cleardraft-one.vercel.app;
   3. Gmail, signed in;
   4. your Downloads folder, so you can open the CSV.
3. **Clean start.** On the live site, press F12 → Console → type
   `localStorage.clear()` → Enter → reload. "Your mail" should show **(0)**.
4. **Rehearse once with a timer.** Each section ends at the time shown.
5. **Upload.** Use YouTube, **Unlisted** (not Private). Then open the link
   in an incognito window to check that it plays.

---

## 0:00–0:25 · Intro (all three, on camera) — slides 1 → 2

**EZ:** Hi, we're **[Team name]**. I'm Ee Zhan. I built the architecture,
the API and the website.

**SK:** I'm Sheng Kuan. I built how ClearDraft reads documents, in four
different file formats.

**ZQ:** And I'm Zi Qi. I built the email sorting and the evaluation. Our
project is **ClearDraft: shipping paperwork, checked in seconds.**

---

## 0:25–1:05 · The problem and why it matters (ZQ) — slides 3 → 4

**ZQ:** A shipping documentation desk runs on one shared inbox. When a
customer asks for a document check, someone opens two files: the Shipping
Instruction, which is what the customer asked for, and the draft Bill of
Lading, which is what the carrier typed. Then they compare seven fields by
hand.

The domain expert at Workshop 2 told us this takes **up to ten minutes per
pair**. In our sample inbox of 520 emails, only 220 are about these
documents at all, so everything has to be sorted first.

If one field slips through, a wrong Bill of Lading is issued. That means
amendment fees, bank discrepancy fees and delayed cargo. It hits the
documentation team that does the checks, and every shipper and consignee
downstream.

---

## 1:05–1:50 · Solution and tech stack (EZ) — slides 5 → 6 → 7 (→ 8)

**EZ:** ClearDraft does the checking and leaves the decisions to people.
It sorts every email, reads both documents, compares all seven fields, and
drafts the reply.

Two design choices matter.

- **The comparison uses no AI.** It is exact, so it is predictable and
  fast.
- **AI is only a fallback.** Claude Haiku 4.5 handles emails or labels the
  rules don't recognise. Every value it returns must appear **word for
  word** in the document, or we throw it away and a person decides.

It runs on **Vercel**: a static website plus one Python FastAPI function,
with **Upstash Redis** for accounts. Anything uncertain goes to a human, and
ClearDraft **never sends mail by itself**.

---

## 1:50–3:50 · Live demo — switch to the live-site tab

**EZ** (driving): Here's the live site. A new user's inbox starts empty.
*[Inbox → **Try 6 sample emails**]*

Each of these six real emails is going through the live pipeline right
now: sorted, read and compared. Two have discrepancies, two need a person,
one is clean, and one isn't a document check at all.

*[Open the first "Discrepancy found" case: 5ALT-01226]*

**SK:** This draft has two wrong fields: the consignee and the notify
party. Every value shows where it came from.
*[Click **where this came from**]*

Here's the exact line in the Shipping Instruction and in the draft Bill of
Lading. You can verify it in seconds instead of reopening files.

It reads text, PDF, Excel and Word, and it matches labels by meaning:
"Load Port" and "Port of Loading" are the same field. It also notes that
this Bill of Lading is made out *"to the order of"*. That is legally
different, so a person should confirm it.

**EZ:** *[Scroll up briefly]* Up here you can see it was decided by rule,
with its confidence. "Your part" tells the person exactly what to do.

*[Scroll to the reply]* The reply is already drafted from the checked
values. I can edit it, and spellcheck is on.
*[Type a word]*

**Open in Gmail** puts it into a new email, ready to send.
*[Click → show the Gmail compose tab for 2 s → back]*

Then **Looks right — next case** takes me straight to the next
discrepancy. *[Click]*

**(cut if over)** No `.eml` file? Just paste the email and attach the two
documents. *[Show the **Paste an email** tab for 3 s]*

For reporting, **Export** gives a discrepancy report: every mismatch, the
SI value, the BL value and why. It opens straight in Excel.
*[**Sample company inbox** → **Export** → **Discrepancy report (CSV)** →
open the file]*

**ZQ:** And this is the full sample: all **520 emails**, sorted and checked
in under two seconds.
*[Show the Sample inbox tabs: 46 / 22 / 152 / 300]*

---

## 3:50–4:35 · Impact, validation and feedback (ZQ) — slides 14 → 15 → 16

**ZQ:** How do we know it works?

- On the organiser's 520 emails, it caught **all 46 planted
  discrepancies**.
- To test mail it has never seen, we wrote a held-out set without looking
  at our rules. Email sorting went from **67% with rules alone to 100% with
  the AI fallback**, and the AI made **zero wrong decisions**.
- It is backed by **268 automated tests**.

On impact: a comparison takes up to **ten minutes by hand**, and ClearDraft
does the **whole inbox in about 1.7 seconds**. People still review and send
every reply.

Everything on this slide came from the domain expert's feedback: the
export, the human review step, and handling real-world formatting.

---

## 4:35–4:48 · Close — slides 18 → 19

**SK:** Next, ClearDraft will remember each carrier's document layout, so
repeat templates never need AI. It will also connect straight to a
mailbox.

**EZ:** ClearDraft is live at **cleardraft-one.vercel.app**, and the code
is on GitHub. Thanks for watching. We're **[Team name]**. *(All wave.)*

---

## If something breaks while recording

- **"Try 6 sample emails" shows an error:** switch to **Sample company
  inbox** and open case **5ALT-01226**. It is the same case, from saved
  results.
- **Gmail isn't signed in:** use **Copy text** instead and say "copy it
  into any mail app".
- **You're over 5:00:** cut the Paste segment, then show slide 8 (tech
  stack) while you speak over slide 6 instead of showing it separately.
