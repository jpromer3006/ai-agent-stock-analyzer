# Reclaim Medical Technologies NDA: Analysis for AdminAI

**Document:** Reclaim_NDA__Juan_Romero.pdf
**Counterparty:** Reclaim Medical Technologies Incorporated, a Delaware C-Corporation
**Counterparty signer:** Eddie Perez, President and CEO
**Recipient:** Juan Romero (signing personally, not as AdminAI LLC)
**Effective date:** April 26 (year not specified in body)
**Purpose:** "the launch of Reclaim's proprietary application"
**Reviewed:** May 7, 2026

---

## Triage classification: YELLOW

Sign with edits. Three to five negotiated changes will move it to GREEN. The document is a standard Orrick one way NDA template (the Orrick attorney references in the footnotes confirm this), which means it is on the more Discloser favorable end of the spectrum but is routinely marked up before signing.

---

## The single most important finding

JP is signing this **personally**, not as AdminAI LLC. AdminAI is not a party to this contract. That reshapes the entire risk analysis.

Two consequences flow from this:

First, AdminAI's IP, code, business operations, and revenue are not directly bound by this NDA. AdminAI is not on the hook for breach.

Second, and this is the catch, anything JP learns from Reclaim cannot flow into AdminAI's products, training data, codebase, sales conversations, or strategic thinking. The wall between "JP the advisor to Reclaim" and "JP the founder of AdminAI" must remain perfectly intact. With this NDA's broad definitions and perpetual term, that wall has to hold forever. In practice, when one person is on both sides, that is hard to maintain.

**Decision point:** if the Reclaim work is substantive enough that AdminAI may benefit (revenue, products, knowledge), consider asking Eddie to swap the Recipient party from JP personally to AdminAI LLC, or to add AdminAI as a co Recipient. This shifts obligations and protections to the entity level.

---

## What is good about this NDA

The standard exceptions are present (public domain, prior knowledge, third party source). The IT backup carveout in Section 4 is sensible. The compelled disclosure notice in Section 9 lets JP tell Reclaim before responding to a subpoena. There is no non solicit or non compete clause, which is a common landmine. The use restriction in Section 2 is tied to evaluating the Relationship, which is appropriately narrow. Governing law is Delaware, normal for a Delaware C-corp.

---

## The four clauses that matter most for AdminAI

### 1. Section 7: No reverse engineering and "create other works from"

The clause prohibits modifying, reverse engineering, decompiling, **creating other works from**, or disassembling any software programs, hardware, prototypes, samples, or other technology in Reclaim's confidential information.

For an AI builder, "create other works from" is broad enough to argue that training, fine tuning, or evaluating an ML model on Reclaim materials is prohibited. If JP's advisory work touches AI development on Reclaim's behalf, this clause would arguably block him from doing the work he is being engaged for.

**Ask:** Add a permitted use carveout that says Recipient may use Confidential Information to perform the services contemplated by the Relationship, including training, fine tuning, and evaluation of AI models for the benefit of Discloser.

### 2. Section 11: Perpetual term

The NDA has no expiration. Most modern NDAs cap confidentiality at two to five years for general confidential information, with trade secrets surviving as long as they remain trade secrets. This NDA has no time limit at all.

**Ask:** Cap non trade secret confidentiality at five years from the date of disclosure, with trade secrets continuing to survive under their own terms.

### 3. Section 1: No residuals carveout

The definition of Confidential Information is sweeping (technical data, algorithms, processes, business plans, customer lists, employee info, plus anything a reasonable person would consider confidential). What is missing is a residuals clause that protects general skills and unaided memory.

**Ask:** Add a residuals clause. Standard language: "nothing in this Agreement restricts Recipient from using general skills, knowledge, ideas, concepts, know how, and experience that are retained in the unaided memory of Recipient or Recipient's personnel as a result of exposure to Confidential Information."

### 4. Section 14(d): Asymmetric assignment

Reclaim can freely assign this NDA. JP cannot assign without Reclaim's written consent. If Reclaim gets acquired, the new owner (potentially a direct AdminAI competitor) inherits enforcement rights against JP.

**Ask (optional, lower priority):** Condition assignment to a competitor of Recipient on Recipient's written consent. Reclaim may refuse this; not a deal breaker if so.

---

## Smaller items worth knowing

The 10 day return or destruction window in Section 4 is tight but workable. The injunctive relief language in Section 13 is standard for NDAs and rarely negotiated. The export controls in Section 8 are boilerplate. The "result of negotiations between... reviewed by each... and their respective counsel" language in 14(g) is benign but technically represents that JP had counsel review the document. If JP did not, that is a soft fiction that courts treat permissively, but worth flagging.

---

## Recommended path forward

Reply to Eddie with three asks:

1. Five year cap on non trade secret confidentiality (Section 11).
2. Standard residuals carveout (added to Section 1 or as a new subsection).
3. Permitted use carveout for the technical work contemplated by the Relationship, including AI development if relevant (Section 7).

Separately, decide whether the Recipient should be JP personally or AdminAI LLC. If the Reclaim engagement is a service AdminAI is delivering, ask Eddie to swap the Recipient block.

---

## Disclaimer

This analysis is not legal advice. JP is not represented by counsel here, and the analyzer is not his attorney. For any material business relationship with Reclaim that flows from this NDA, a 30 minute call with an actual attorney is worth far more than this review. The NDA does not look predatory; it looks like a standard Orrick template that a careful counterparty would mark up before signing. How Reclaim responds to the three asks above is a useful signal about how they will negotiate the substantive agreement that follows.
