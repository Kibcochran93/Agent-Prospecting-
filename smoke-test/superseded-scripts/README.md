# Superseded scripts

These wrote to Apollo from a session, each with its own parser. Two of them
shipped defects into live sequences on 3 and 4 September: a message body that
swallowed the section after it, and five people in one cadence.

Replaced by `seats-drafts` (`src/seats_prospecting/drafts.py`), which uses the
same parser as the mechanical checker and is covered by `tests/test_drafts.py`.

Kept for the record. Do not run them.
