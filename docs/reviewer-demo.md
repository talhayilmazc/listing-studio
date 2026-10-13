# Reviewer demo: shot list

A 2–3 minute screen recording of the single-shop flow for Etsy's Commercial Access review, and the still screenshots to send with it.

## Before recording

- Record on **production** (https://listyro.com) with the **test shop**. Local development sends nothing to Etsy (`ETSY_CALLS_BLOCKED`), so the connect and draft steps cannot be recorded there.
- Use a fresh invite for a new e-mail address, so the signup and consent step is real. Approve the request in Admin → Invites and use "Copy invite link".
- Have a folder of 3 to 5 of your **own** designs ready (two or three mockups each). No licensed characters, logos or brand names anywhere in them.
- The test shop needs one listing set up the way you like (the reference listing), with its production partner stated on Etsy.
- Browser at 1366 × 768 or wider, zoom 100%, no other tabs, no bookmarks bar, notifications off.
- Do not show anything from another seller's account, the Admin panel, or any token, key or `.env` file.

## The recording (about 2:40)

| # | Time | Screen | What to do | What it shows the reviewer |
|---|---|---|---|---|
| 1 | 0:00–0:15 | `listyro.com` | Scroll the home page once to the footer; pause on the footer. | The product name is Listyro; the trademark notice is in the footer; nothing claims a partnership with Etsy. |
| 2 | 0:15–0:35 | `/register?invite=…` | Open the invite link. Fill the password. Show that **Create account is disabled** until the box "I agree to the Terms of Service and Privacy Policy" is ticked. Open the Privacy Policy link briefly, come back, tick, create the account. | Click-through acceptance, both documents linked, the box unticked to begin with. |
| 3 | 0:35–1:00 | `/connect` (Shops) | Show the notice beside the button and open "What Listyro asks Etsy for, and why". Press **Connect your shop**; on Etsy's own page show the permissions and press Allow. Back on Shops, the shop is listed. | OAuth on Etsy's page; the five scopes and their reasons; the trademark notice beside "Connect a shop". |
| 4 | 1:00–1:20 | `/upload` | Choose the folder of designs. Show the grouping choice and the count ("4 listings"). Upload. | The seller's own designs; nothing is taken from anyone else. |
| 5 | 1:20–1:40 | Profiles, then the batch | Pick the reference listing as a profile (one click). On the batch, choose that profile and press **Generate content for all**. | The draft's settings come from the seller's own existing listing; AI only suggests text. |
| 6 | 1:40–2:05 | Review page | Open one card. Edit the title, remove and add a tag, show the 13-tag counter and any flag. Tick **Approve for draft** on that listing. | The seller reviews and edits every listing; approval is per listing and explicit. |
| 7 | 2:05–2:20 | Review page | Press **Create draft**. When it finishes, the card shows "Draft" and **Edit draft ↗**. Point at the line "it is never published automatically". | A draft is created, nothing is published. |
| 8 | 2:20–2:30 | Etsy Shop Manager | Follow **Edit draft ↗**. Show the draft in Shop Manager → Listings → Drafts: title, images, variations, production partner. | It is a real draft in the seller's own shop, made through the API. |
| 9 | 2:30–2:40 | `/connect` | Press **Disconnect**, read the confirmation, confirm. Show the message saying the tokens and Etsy data are deleted and what was cancelled. | The seller can revoke at any time; data is deleted at once. |

Optional, if there is time: Settings → **Delete account** (open the form, do not confirm), to show the seller can delete everything themselves.

Say little or nothing; if you narrate, describe only what is on screen. Do not claim anything about ranking or sales.

## Screenshots to take

Take each at 1366 px wide, and the first three also at 375 px (phone).

| # | Screenshot | Where | Must be visible |
|---|---|---|---|
| 1 | Trademark notice in the footer | `listyro.com`, bottom of the page | The full sentence: "The term 'Etsy' is a trademark of Etsy, Inc. This application uses Etsy's API, but is not endorsed or certified by Etsy." |
| 2 | Trademark notice beside "Connect a shop" | `/connect` | The button and the notice in the same view, with the scope reasons open. |
| 3 | Consent checkbox | `/register` | The box **unticked**, the sentence "I agree to the Terms of Service and Privacy Policy" with both links, the disabled button, and the provider line. |
| 4 | The one-time acceptance screen | `/accept-terms` (an account from before consent was recorded, or after a version change) | The two links, the box, "Agree and continue". |
| 5 | Approval step | A review card | The edited title, the 13 tags, the "Approve for draft" tick, and the sentence that a draft is never published automatically. |
| 6 | Publish is explicit | A review card with a draft | "Publish now" and "Schedule" as the seller's own actions. |
| 7 | Privacy Policy | `/privacy#etsy` | The table of Etsy data with "Why" and "Kept for", and the heading "What is never stored". |
| 8 | Terms: who provides the service | `/terms#provider` | The provider line in capitals and the trademark notice in section 3. |
| 9 | Disconnect | `/connect`, after disconnecting | The message that the tokens and Etsy data were deleted and what was cancelled. |
| 10 | Delete account | `/settings` | The Delete account section with what is deleted at once and within 30 days. |
| 11 | Pricing | `/pricing` | The heading "Request an invite to try Listyro", the line "No payment is taken today; the plans below apply after launch", and the notice "Planned prices after launch" above the planned plans. |

## After recording

- Watch it once for anything that should not be there: another seller's data, an e-mail address other than the demo one, a token.
- Disconnect the test shop if you connected it only for the recording, and delete the demo account in Settings.
- Keep the recording and screenshots outside the repository.
