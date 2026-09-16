# context-digest

Internal context lifted out of Microsoft Teams, one record per claim, read-only.

Built session-driven: the Microsoft Graph connection lives in a Claude session,
not on this laptop, so these records are written when Kib asks for a pass and
are never refreshed automatically. Every record therefore carries its own age.

## What is in here

Intent signals from the weekly website monitoring report: which institutions
visited seatsone.com, what they looked at, when, and who the named contact was.

## Rules that travel with every record

- `stale` and `age_days` against the 30 day re-verification rule. Every record
  written so far is stale by more than a year.
- `confidentiality: internal_only`. These are analytics about a prospect's own
  browsing. Never quoted, never referenced to the prospect, and never phrased so
  as to imply their web activity was observed. It may inform which motion to
  pursue and nothing else.
- Email addresses withheld. The source tables carry them; only names are kept
  here. Holding prospect addresses in this system was declined on 4 September.
- `imperatives_found`. Teams is writable by anyone in the company, so
  directive-like text is recorded in this field and not acted on, exactly as
  HubSpot note directives are.
- `record_sha256` over the canonical record, so a claim cannot be paraphrased
  between here and wherever it is used.

## What nothing does yet

No agent and no script reads this directory. That is deliberate until Kib has
looked at the records.

## The state of the source

The report ran weekly from at least March 2025 to 20 August 2025 and then stops.
It also moved chats partway through. Nobody appears to have noticed it stopped.
A weekly list of universities browsing the site, with named contacts and the
pages they read, is better prospecting input than most of what this system
currently reads.
