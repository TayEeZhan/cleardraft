# ClearDraft

**Shipping document verification for a shared operations inbox.**

ClearDraft reads a shipping company's inbox, works out what each email is
asking for, and — when it is asking for a document check — compares the
Shipping Instruction against the draft Bill of Lading field by field.

## The problem

A shipping desk runs one shared mailbox for a whole team. Customers send the
Shipping Instruction (SI): what they want shipped and to where. Carriers
reply with a draft Bill of Lading (BL): what they intend to issue. Before it
is issued, a clerk opens both attachments and compares seven fields by hand —
shipper, consignee, notify party, port of loading, port of discharge,
container count, gross weight. A Workshop 2 domain expert put a number on
it: up to 10 minutes per pair by hand, for a documentation team of 5–10
people — an estimated 20 staff-hours across the sample inbox's 119 pairs,
against about 1.7 seconds of machine time. A missed discrepancy means a
Bill of Lading is issued with the wrong consignee or the wrong port, costing
money and delaying the cargo. It is also harder than it sounds: the two
documents routinely label the same field differently — "Port of Loading" in
one, "Load Port" in the other — and real documents carry partial matches
such as an asterisk marking a party name that continues elsewhere on the
page.

## What ClearDraft does

It classifies what an email is asking for, extracts the seven fields from
each attached document with a record of where each value was read, compares
the two sets of values deterministically, and escalates to a person instead
of guessing whenever it cannot decide. It then drafts the reply. The split
is deliberate: ClearDraft sorts, reads, compares and drafts; the person
confirms or flags each result, decides every escalated case, and edits and
sends the reply — nothing is sent automatically. When a carrier sends back
an amended draft, ClearDraft re-checks it against the same SI and reports
what was fixed, what is still wrong, and what the amendment broke.

A visitor starts from an empty inbox and adds mail by pasting an email (text
plus the SI/BL files) or dropping `.eml` files — no Gmail connection needed,
by design, since Gmail read access needs Google's restricted-scope review —
or loads the full 520-email sample inbox in one click. Each case shows the
seven-field table with the source line behind every value, who decided it
and with what confidence, and a "Your part" line naming what to check or do.
Results export as a discrepancy CSV, a full-results CSV, or the organiser's
submission JSON.

## How AI is used, and kept in bounds

Rules run first and handle most emails and most fields on their own. Claude
Haiku 4.5 is consulted only for what the rules cannot resolve — an unclear
email intent, or a field the format parsers could not locate. Every value
the model returns must appear word-for-word in the source document, or it is
discarded and the case is escalated. The model never decides a mismatch;
that comparison is exact string equality on normalised values.

## Results

On the 520 emails supplied for the preliminary round, ClearDraft caught 46 of
46 planted defects (an end-to-end score of 1.0000). That is a validation
number on the one dataset we hold labels for, not evidence that the system
generalises. The real evidence is a small held-out set written without
reference to our own rules: category accuracy rose from 67% to 100% and
intent accuracy from 50% to 100% once the model fallback ran, and fields
under labels the system had never seen went from 2 of 41 read to 41 of 41.

## Cloud and links

Deployed as a static site plus a Python serverless API, both on Vercel, with
the Anthropic API for the model tier.

- Repository: https://github.com/TayEeZhan/cleardraft
- Live site: https://cleardraft-one.vercel.app

## Team

Ee Zhan, Sheng Kuan, Zi Qi.
