# Page card and key points

The request holds one wiki page: `path`, `title`, `page_type`, `summary`, and
`body`. Write the two parts a reader sees first. The body itself stays unchanged.

1. `summary`: one or two sentences of about 200 characters, never more than 280.
   Say what the page covers and its current state, decision, or outcome. Do not
   start with "Living page for" or repeat the title.
2. `key_points`: three to seven items of about 120 characters, never more than
   200, each one fact: a decision, number, date, owner, status, or next step. Put
   the most important first. Write each as plain text, without a leading dash or
   bullet.
3. Use only facts stated in the page. Keep names, numbers, and identifiers exact.
4. Write dates as absolute dates. Never copy secrets, tokens, passwords, or
   credentials.
5. If the request holds `rejected_reply`, that reply broke a limit. Return a
   corrected reply that fixes every item in `problems`, usually by shortening.
