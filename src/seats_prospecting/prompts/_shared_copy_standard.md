# The copy standard

One file, read by the agent that writes the copy and the agent that grades it.
It was split into two descriptions of the same thing until 4 September 2026, and
they drifted: the writer optimised for batch-level variety while the grader
asked whether a recipient would notice a template. Four consecutive rewrites
came out of that gap, each one naming a construction the previous rewrite had
introduced. A standard that cannot be satisfied is a broken standard, not a
strict one.

## What a cold first touch is

A first touch to a stranger has a shape, and the shape is not a defect. It names
something true about their institution, says why that might matter to the person
holding this role, and asks one thing or asks nothing. Written well, five of
them to five institutions will resemble each other the way five good cover
letters resemble each other. The recipient sees one message, never the batch.

## Rewrite grounds, and there are six

A batch of first touches gets rewritten when, and only when, one of these is
true. Quote the sentence. Naming the category without the sentence is not a
finding.

1. **A never-say hit.** A named institution beside a compliance shortfall,
   "Student CRM", surveillance framing, or British spelling.
2. **An unsupported claim about the recipient.** A problem asserted as fact
   about their institution with no dated source under it. A hypothesis offered
   as a hypothesis is not this. Cold outreach is allowed to wonder aloud; it is
   not allowed to tell someone what is wrong at their institution and be
   guessing.
3. **No recipient-specific anchor.** Nothing in the message names this
   institution's own program, number, date or decision. Category facts about
   higher education, dressed with an institution's name, are this failure.
4. **Same institution, same problem.** Two roles at one institution built on one
   problem with the titles swapped. Two people who talk to each other get two
   different reasons to reply or they get one message.
5. **Register mismatch.** Practitioner vocabulary to a Provost or a Chief
   Financial Officer, or board language to a coordinator.
6. **An invented motion.** A composed phrase where a named motion from the
   battlecards belongs.

Artifact integrity sits outside the list and above it: customer-facing text
carrying an instruction to an operator fails on that alone.

## The floor: what is not a defect

These are properties of the form. They are never rewrite grounds, and a finding
that reduces to one of them is not a finding.

- **Closing with a question.** Most cold first touches end with one. Varying the
  ask across a batch is worth doing and it is not worth a rewrite.
- **One sentence naming SEAtS and what it does.** The product does one thing.
  Five messages about it will describe it similarly. Grade whether the sentence
  is tied to that recipient's situation, not whether it is phrased freshly.
- **Fact, then implication, then ask.** That is the order cold outreach uses.
- **Comparable length and register inside one role tier.**
- **Shared domain vocabulary.** Attendance, engagement, persistence, retention
  and advising are the words for these things. Synonym-hunting to avoid
  repetition across a batch makes the copy worse.
- **Subject lines with a family resemblance** that are not identical.

## What the machine already owns

`seats-check-batch` counts, before any judgement runs: identical subject lines,
a sentence appearing in two messages, a shared opening on eight words, an
identical closing, a placeholder recipient, an institution threaded once. Those
are settled by string comparison and enforced at the write path. Do not spend a
review pass re-deriving them by eye, and do not escalate "the same move in
different words" into one of them.

## Cadence touches are graded differently: additivity

Touches 2 to 4 go to one person who has already read the earlier ones. Batch
variety is not the question there. The question is additivity: whether each
touch carries something the earlier touches to that person did not. A touch that restates
touch 2 in new words, or that only observes the last one went unanswered, fails
and is rewritten. This is at full strength and the floor above does not soften
it.

## Observations

Everything that is not on the six and not on the cadence rule is an observation.
Report it under its own heading, plainly, and ship the batch anyway. Kib reads
observations. A repeated construction worth mentioning and a defect worth
rewriting are different objects, and collapsing them is what produced four
rewrites and no shipped batch.
