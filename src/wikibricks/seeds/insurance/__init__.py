"""Motor-insurance seed domain. Paths match `eval_queries()` targets in ops.py."""


def pages() -> list[dict]:
    return [
        {
            "path": "sops/motor/total-loss",
            "title": "Total Loss Assessment SOP",
            "page_type": "entity",
            "content": {
                "summary": "Standard operating procedure for declaring and settling a vehicle total loss.",
                "body": (
                    "A motor claim is declared a total loss when repair cost plus salvage value "
                    "exceeds actual cash value (ACV). Assessor photographs the vehicle, pulls "
                    "VIN-matched comparables from the valuation service, and subtracts salvage "
                    "bid from the nearest retail comparable to produce the settlement offer. "
                    "Salvage title is transferred to the insurer. Notify the lienholder before "
                    "payout. Total-loss decisions older than 30 days require senior-adjuster "
                    "sign-off."
                ),
            },
            "created_by": "setup",
            "tags": ["sop", "motor", "total-loss", "claims"],
        },
        {
            "path": "claims/liability/comparative-negligence",
            "title": "Comparative Negligence in Multi-Party Claims",
            "page_type": "concept",
            "content": {
                "summary": "Allocating fault percentages across parties in a multi-vehicle claim.",
                "body": (
                    "Comparative negligence apportions damages according to each party's share "
                    "of fault. Pure comparative: a party recovers its damages minus its own "
                    "fault share, even at 99 % fault. Modified comparative (50 % bar): recovery "
                    "only if the claimant is at most 50 % at fault. Use scene photos, police "
                    "report, and witness statements to anchor the percentages. Document the "
                    "rationale in the claim notes — these percentages are the single largest "
                    "driver of indemnity payout and must survive litigation."
                ),
            },
            "created_by": "setup",
            "tags": ["liability", "negligence", "claims", "multi-party"],
        },
        {
            "path": "claims/fraud/patterns",
            "title": "Collision Fraud Indicators",
            "page_type": "synthesis",
            "content": {
                "summary": "Red-flag patterns in staged or inflated collision claims.",
                "body": (
                    "Common indicators: damage inconsistent with the reported point of impact; "
                    "occupants unrelated to the policyholder with immediate representation; "
                    "prior claims in the same network of adjusters/body-shops; vehicles with "
                    "pre-existing damage that matches the new claim; late-night low-speed "
                    "impacts with high soft-tissue-injury counts. Any two indicators trigger "
                    "SIU referral before settlement."
                ),
            },
            "created_by": "setup",
            "tags": ["fraud", "claims", "collision", "siu"],
        },
        {
            "path": "products/motor/coverage-tiers",
            "title": "Motor Coverage Tiers",
            "page_type": "comparison",
            "content": {
                "summary": "Comparison of Basic, Standard, and Premium auto coverage tiers.",
                "body": (
                    "Basic: liability-only, state minimums, no rental reimbursement. "
                    "Standard: liability + collision + comprehensive, $500 deductible, "
                    "rental $30/day for 15 days. Premium: Standard plus OEM parts guarantee, "
                    "$250 deductible, rental $60/day for 30 days, new-car replacement within "
                    "the first 24 months, diminished-value coverage. Upsell path: Standard → "
                    "Premium at renewal for customers with vehicles < 3 years old."
                ),
            },
            "created_by": "setup",
            "tags": ["products", "motor", "coverage", "tiers"],
        },
        {
            "path": "claims/fraud/repeat-claimants",
            "title": "Repeat Claimant Analysis",
            "page_type": "synthesis",
            "content": {
                "summary": "Risk scoring for claimants with multiple recent claims.",
                "body": (
                    "A claimant with three or more claims in 24 months across any carrier "
                    "warrants enhanced review. Run the claim through ISO ClaimSearch before "
                    "settlement. Focus on same-injury recurrence (whiplash, soft-tissue), "
                    "same provider networks, and rotating attorneys. Auto-flag for SIU when "
                    "two of these dimensions overlap."
                ),
            },
            "created_by": "setup",
            "tags": ["fraud", "claims", "repeat", "siu", "iso"],
        },
        {
            "path": "customers/preferences/language",
            "title": "Customer Language Preferences",
            "page_type": "entity",
            "content": {
                "summary": "Per-customer preferred correspondence language.",
                "body": (
                    "Language preference is stored on the customer record and honoured across "
                    "all written correspondence, IVR routing, and portal UI. Defaults from "
                    "policy-origination state; customer can override in the portal. Supported: "
                    "English, Spanish, French, Portuguese. Fallback to English when a template "
                    "is not yet translated. Claims-letter templates must be approved by Legal "
                    "in each language before rollout."
                ),
            },
            "created_by": "setup",
            "tags": ["customers", "preferences", "language", "correspondence"],
        },
        {
            "path": "claims/weather/hail-surge",
            "title": "Hail Storm Claim Surge Playbook",
            "page_type": "synthesis",
            "content": {
                "summary": "Operational playbook for catastrophic hail events.",
                "body": (
                    "On CAT declaration: open a dedicated event code in the claims system, "
                    "route all matching ZIP-code claims to the CAT team, activate mobile "
                    "appraisal units, and pre-authorise dent-repair shops up to $3500 without "
                    "additional inspection. Publish daily loss estimates to reinsurance. "
                    "Close the CAT event 14 days after last new claim; reconcile any post-CAT "
                    "claims individually. Communications: automated SMS status updates every "
                    "72 hours until settlement."
                ),
            },
            "created_by": "setup",
            "tags": ["cat", "hail", "weather", "claims", "playbook"],
        },
    ]
