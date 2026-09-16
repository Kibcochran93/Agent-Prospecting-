# Document precedence and open review items

> INTERNAL ONLY. This file is a pointer and a precedence map, not a copy. It
> records which documents outrank which, where the outranking documents live,
> and which conflicts are unresolved. It exists so that no agent and no person
> has to guess. Nothing here is sent to a prospect.
>
> Read this before treating any other file in this folder as final.

## Why this file exists

SEAtS keeps a canonical GTM layer on SharePoint. The four US documents in this
folder are NOT copies of it. They are a parallel, US-specific body that has no
governed home. The canonical layer outranks them and the agents have never
read it.

The canonical governance file states the rule directly:

> If an older sales asset conflicts with the Commercial Brain, the Commercial
> Brain wins. If field evidence suggests the brain is wrong, create a review
> item; do not silently fork the truth.

That is why the conflicts below are recorded rather than resolved.

## Precedence, highest first

1. **Canonical inheritance and asset governance** — the ordering rule itself.
2. **Central Growth, Sales, Marketing, ABM & GTM Strategy, v4** — motions,
   ABM object, agent reasoning sequence, evidence standard.
3. **Master GTM & Sales Motions** — platform narrative and entry-point motions.
4. **Market & Vertical Sales Motions** — per-market wedges, including US.
5. **ABM, Intent & Outreach canonical** — solution-interest taxonomy, scoring.
6. **US ABM Playbook v3.1** (this folder) — US operational detail. Authoritative
   for US execution *within* the bounds above, not over them.
7. **US Market Intelligence v2, US Competitive Battlecards, US Health Sciences
   GTM Playbook** (this folder) — research and copy inputs.

## Where the canonical layer lives

Site: `https://seatssw.sharepoint.com/sites/SEAtS`
Library path: `Shared Documents/Growth/`
Drive id: `b!LFG_TmJv906_bD_36y9fZXYnepLXZh5GtiX2QWmbVeGKspde-r7wT48dZGQVWsDn`

| Document | Folder | Item id | Modified |
|---|---|---|---|
| Canonical Inheritance & Asset Governance | Revenue GTM Operating System / 00 Strategy and Governance | 01PINOJSGNSGMEEQB5XNC2AOTID54K4JMM | 2026-08-23 |
| Master GTM & Sales Motions | Revenue GTM Operating System / 00 Strategy and Governance | 01PINOJSCPYUHEGFBY5FCZ6XRSJZ7P6JDZ | 2026-08-23 |
| Central Growth GTM Strategy v4, ABM Sales Motions | GTM Strategy | 01PINOJSGGH6BHVGBZKFALVU3OAO5R4W5G | 2026-08-20 |
| Always-On ABM Engine Operating Instruction File | GTM Strategy | 01PINOJSCO44SFOQSTKRAZ7TJNAUX4H3NN | 2026-08-20 |
| Market & Vertical Sales Motions | Revenue GTM Operating System / 03 Regional and Vertical Motions | 01PINOJSDWMP2KSVDT5NDITA46TAFEVHLW | 2026-08-23 |
| ABM, Intent & Outreach canonical | Revenue GTM Operating System / 07 ABM Intent Outreach and Campaigns | 01PINOJSFCMYSJU2OYZRGYCXCHXSYQMAAI | 2026-08-23 |
| ABM Sales Motions Outreach Playbook (.xlsx) | Revenue GTM Operating System / 07 ABM Intent Outreach and Campaigns | 01PINOJSHDD33F5AXIZFDZ7JFLMCMALFDQ | 2026-08-23 |
| Regional Target Account Plans, 24 Aug 2026 | Revenue GTM Operating System / 06 Account Plans | 01PINOJSGFNR7SXESSMFFZFTUVMGUHLQ5C | 2026-08-24 |

The agents cannot read any of these. `SHAREPOINT_AUTHORIZATION` is unset and no
SharePoint tool is wired into the agent runtime.

## Confirmed: the motion vocabulary is correct

The canonical v4 strategy defines Motion 1 through Motion 7 with the same names
and scope as M1 through M7 in `LEDGER_MOTIONS`. M8 is absent because the
canonical file's eighth section is the NHS Trust lookalike motion, which is UK
only and correctly excluded from a US playbook. M9, US Health Sciences, is a US
extension that appears nowhere in the canonical central file.

No change required. This was previously an assumption; it is now verified.

## Open review item 1: M9 clinical placement and rotation

**Status: unresolved. Do not resolve by reading either document alone.**

The US ABM Playbook Guardrails sheet says: do not pitch clinical placement or
rotation management, because Exxat and CORE ELMS already do hours tracking and
accreditor reporting and we lose those deals.

The canonical Market & Vertical Sales Motions file says US Health Sciences is
"clinical readiness, placement/rotation visibility, attendance, student support
and accreditation evidence." The canonical Master GTM file's accreditation
motion says "continuous visibility across class, lab, simulation, placement,
rotations, support and evidence."

Two documents that outrank the US playbook both name the thing the US playbook
forbids. By the conflict rule the canonical layer wins. But the guardrail's
reason is competitive field evidence, which is exactly the case the same rule
says to escalate rather than overwrite.

**The guardrail stays in force until Kib decides.** The agents continue to
refuse placement and rotation copy. This is a positioning decision, not an
agent behavior question.

Note the canonical file also says "Use the existing Health Sciences playbook,"
which points at the document that leads with placement. The canonical layer is
internally consistent on this and the US playbook is the outlier.

## Open review item 2: the US has no canonical account plan

`13_Regional_Target_Account_Plans_2026-08-24.md` covers UK and Ireland,
Australia and New Zealand, Singapore, Canada and Mexico. The `06 Account Plans`
folder has subfolders for those same five regions. There is no United States
section and no United States folder.

The four accounts briefed so far exist in no canonical account plan. The Notion
ledger is currently the only place US account intelligence is recorded.

## Open review item 3: these four documents have no governed home

A content search of SharePoint for the battlecards returned nothing. A search
for the market intelligence returned only personal OneDrive drafts. None of the
four documents in this folder is in the Growth library.

The canonical instruction file is explicit about where they should be: store the
context and skills library centrally, preferably in a private version-controlled
repository, and do not maintain separate master copies per AI runtime. This
project is not a repository and this folder is a manual copy on one laptop.

## Canonical requirements this system does not yet meet

Recorded so they are visible, not silently absent.

- **Every AI output must carry source references, confidence level,
  verification status, recommended human reviewer and timestamp.** The ledger
  record carries sources, dates and a verified-through field. It has no
  confidence field and no named reviewer field.
- **Fact, signal, inference and recommendation must be separated, and an
  inference must never be presented as a fact.** The prompts enforce evidence
  discipline and treat intent as signal, but do not require those four labels
  as an output structure.
- **Missing information must be marked Research Required.** Not implemented as
  a term.
- **The Apollo activation standard requires ten items complete before outbound
  starts:** account fit, solution motion, buyer group, target personas, why
  now, buying mechanism, compliance pack, approved message angle, suppression
  check, AE owner. The current control is a human approval on sequence
  creation, which is a weaker and different thing.
- **Every workflow run must store runtime, model, skill id and version, context
  version, trigger, inputs, sources, approval status, human override and
  confidence.** The audit log stores a subset.
- **Nine named regression scenarios** are prescribed, including "opportunity
  already under active AE control" and "personal email discovered for otherwise
  relevant contact." Both are implemented in behavior; neither is tested under
  the canonical scenario name.

## What the canonical layer confirms about the design

- "No agent gets more permission than the invoking human/role."
- "Destructive CRM operations, pricing exceptions, roadmap commitments,
  legal/security commitments and final forecast commits require authorised
  human action."
- "Account intent is not person intent. Anonymous institutional traffic stays
  account-level."
- "Never claim that a named person visited a page unless first-party identity
  evidence supports that attribution."
- "Never infer an email address and mark it verified without independent
  evidence."
- "Accept only institutional/business email addresses; no personal consumer
  email addresses."
- "Evidence before automation. Every activation must be explainable from stored
  evidence."

Read-only HubSpot, the hardcoded personal-email refusal, the owner-review rule
and the approval gate on sequence creation all sit inside these rules rather
than against them.
