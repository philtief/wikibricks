# Local nightly curation for one project

This run covers one project. `request.project` names it and `request.living_page` is the
path of its living page.

1. Keep one living page per project at `request.living_page`. If that path is in
   `current_pages`, propose `update_page` for it; otherwise propose `create_page` with
   `page_type` `entity`.
2. Write the living page body as Markdown with the sections `## Current state`,
   `## Decisions`, `## Open issues`, and `## Key facts`. Keep it under 6000 characters.
   Integrate the new evidence into the existing text: keep facts that are still true and
   replace facts the evidence supersedes.
3. Propose `update_page` for another current page only when the evidence directly changes
   a fact on it.
4. Propose `add_link` with `related` from the living page to current pages it builds on.
5. Use `risk_class` `low` for `create_page`, `update_page`, and `add_link`. Never propose
   `retarget_links`, `add_alias`, or `supersede_page`.
6. Write dates as absolute dates. Never copy secrets, tokens, passwords, or credentials.
7. Return `{"proposals": []}` when the evidence holds nothing durable.
