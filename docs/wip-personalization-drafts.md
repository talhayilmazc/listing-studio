# WIP: personalization on existing drafts, and the "says personalized" warning

Not merged. Parked on 2026-10-26 when the seller's report turned out to mean hand-written listings.

Done (backend only, untested):

- `api/personalization_etsy.py`: turn personalization on for one listing, for selected listings of a
  batch, or apply a profile's to its listings' existing drafts; queues one write per draft or live
  listing on Etsy. Etsy's rules are checked before anything is saved or sent.
- `workers/personalization_etsy.py`: `apply_personalization_on_etsy` writes it
  (updateListingPersonalization), reads it back and checks it; the outcome goes on
  `listing_publication.personalization_sync` (migration `0093_personalization_sync`).
- `pipeline/personalization.py`: `says_personalized`, `profile_question`, `reference_question`.
- Schemas: `PublicationOut.personalization_sync`, `ContentOut.personalization_mismatch`.

Missing:

- `ContentOut.personalization_mismatch` is never filled (contents_out), and nothing shows it on the
  Scheduled page.
- `ProfileOut.existing_drafts` and the profile hint ("your reference listing has personalization;
  this profile has it off").
- Every frontend part: the card's "Turn on personalization", the bulk action, the profile's
  "Also apply to N existing drafts", the write status per shop.
- Tests: the API, the job (written, refused here, not kept, deleted on Etsy), isolation, the
  migration, and the `says_personalized` cases.
- The job type in `test_no_request_counts_for_sellers` / commercial audit lists, if they need it.
