# Route sessions to wiki topics

Each session in `sessions` ran in a container folder such as the home directory, `code`,
or `emails`. The folder says nothing about the subject. Assign each session to the wiki
page it is about.

For each session return one route with `session_id`, `page_path`, and a short `reason`:

1. Use a `candidates` path when the session's main subject is that page's topic. Prefer a
   candidate that covers the same customer, product, or program even when it is broader
   than the session: a Contoso pricing call belongs on `topics/contoso`.
2. Use a new path `topics/<slug>` when the session is about a durable subject that no
   candidate covers. The slug names the subject (a customer, product, project, or
   concept) in lowercase letters, digits, and hyphens. Never name it after the folder.
   Name the account or product, not the meeting: `topics/contoso`, not
   `topics/contoso-weekly-sync`. Sessions in this request on the same subject share
   one path.
3. Use `null` only when the session holds nothing worth remembering: a one-off factual
   question, a check that a tool or login works, or small talk. A work product (script,
   guide, deck, email, analysis), a solved problem, or a meeting, workshop, bug bash, or
   training with substance is durable: route it with rule 1 or 2.

Return `{"routes": [...]}` with one entry per session.
