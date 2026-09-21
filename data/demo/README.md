# Demo data - authored by the team

**Not part of the organiser dataset.** The organiser's inbox contains first
drafts only, so there is nothing to re-check against. This folder holds one
hand-written amended Bill of Lading so the re-check flow can be demonstrated.

`email_004_BL_v2.txt` is the carrier's reply to our discrepancy note on
`email_004`. It was written to exercise all three re-check outcomes:

| Field | Draft v1 | Amended v2 | Outcome |
|---|---|---|---|
| Consignee | UAB NOVAKOPA | EAST BRIGHT FZ-LLC | fixed |
| Notify Party | UAB NOVAKOPA | UAB NOVAKOPA | still wrong |
| Container Count | 6 x 40'HC | 5 x 40'HC | newly broken by the amendment |

It is never scored. `eval/score.py` reads only `data/inbox`.
