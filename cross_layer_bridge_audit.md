# Cross-layer bridging axioms — coverage audit (CSFO network + 41 XKGs)

**Question.** Beyond frames, roles and moderators, does the pipeline extract the
*axioms that bridge CSFO's layers* (situation → frame → cascade steps L4–L7 →
outcome, with moderator × step interactions and feedback)? Verified at two levels.

---

## 1. Ontology-network level — the bridging vocabulary IS defined (strong coverage)

CSFO encodes the L1–L8 stack as **step classes** (`csf:AutonomicStep`,
`NeuroendocrineStep`, `ImmuneStep`, `EpigeneticStep`, `NeuralPlasticityStep`,
`BehavioralStep`, `SocialGenomicsStep`) and defines a comprehensive set of
cross-layer object properties:

| Bridge type | Properties defined in CSFO |
|---|---|
| situation ↔ frame | `evokes`, `evokedBy`, `triggeredByFrame` |
| frame → cascade | `triggersCascade`, `typicalCascade` |
| cascade ↔ step | `hasStep`, `hasCascadeStep`, `hasStepOccurrence`, `executesStep`, `isExecutedIn`, `isStepIn` |
| step → step (causal, cross-layer) | `precedes`, `directlyPrecedes`, `feedsForwardTo`, `feedsBackTo`, `typicallyLeadsTo` |
| pathway → outcome (→L8) | `leadsToOutcome`, `hasOutcome` |
| moderator × cascade/step | `amplifies`, `buffers`, `amplifierIn`, `bufferIn`, `isAmplifiedBy`, `isBufferedBy`, `hasInteractingModerator` |
| phase / temporal | `hasSituationPhase`, `hasPhaseSequence`, `activatesCascadePhase`, `hasTemporalPhase` |

So the **vocabulary coverage is complete**. Two caveats at the ontology level:
(i) the ontology *axiomatises* very few concrete cross-layer chains itself
(`leadsToOutcome` appears once); (ii) `csf-core` explicitly notes that the full
cross-layer semantics is *"not fully capturable in OWL2 DL axioms alone. SHACL shapes
or SPIN rules are…"* — i.e. the bridges are **defined but not constrained**: nothing
*requires* an extraction to instantiate them.

---

## 2. XKG level — the bridges are mostly NOT instantiated (the gap)

Audit over all **41 XKGs**. Two categories emerge.

**Containment / attribute bridges — PRESENT (the layers are represented and hung off
the situation hub):**

| Bridge | Uses | Posts (of 41) | Meaning |
|---|---:|---:|---|
| `evokes` | 104 | 40 | situation → frame |
| `hasCascadeStep` | 217 | 34 | situation → step (layer nodes exist) |
| `hasSituationPhase` | 56 | 40 | GAS phase |
| `hasOutcome` | 30 | 18 | situation → outcome (partial) |
| `licensingCue` | 449 | 39 | step's warrant — **as a text string, not a typed edge** |
| `precedes` | 211 | 34 | **narrative-event** timeline (early-adversity→addiction→recovery…), *not* the cascade |

**Causal cross-layer bridges — ABSENT (the layers are not wired to each other):**

| Target bridge | Posts (of 41) |
|---|---:|
| inter-**layer** step→step edge (L4→L5→L6→L7) | **4** |
| `directlyPrecedes` / `feedsForwardTo` / `feedsBackTo` (typed causal / feedback) | **0** |
| `triggersCascade` / `triggeredByFrame` (frame→cascade) | **0** |
| `leadsToOutcome` / step→outcome edge | **1** |
| moderator → step (`amplifies`/`buffers`/`hasInteractingModerator`) | **0** |
| `activatesCascadePhase` (phase→cascade) | **0** |

**What a step node actually looks like** (e.g. `cascade_l6_inflammation_1`): rich
*attributes* — type, layer label, `evidenceStatus=Abduced`, `plausibility`,
`licensingCue` (string), `channel=Internalized`, `verdict` — but **no outgoing causal
edge** to the next-layer step and **no `leadsToOutcome`**. It is reached only *from*
the situation via `hasCascadeStep`. The 7 moderator instances in D1 connect to **no**
cascade step.

---

## 3. Verdict

The extraction produces a **hub-and-spoke** graph: the CSF situation is the hub, with
spokes to frames, layer-tagged cascade-step nodes, outcomes, phase and (floating)
moderators. Every layer is **co-represented and contained**, so multilayer coverage in
the *compositional* sense holds. But the **relational/causal integration** that CSFO's
vocabulary is built for — the typed edges that chain L4→L5→L6→L7, carry a pathway to
its outcome, wire an amplifier to the specific step it intensifies, and close feedback
loops — is **almost entirely missing** (0–10 % of posts). The frame→step link is
frequently only a `licensingCue` *string*, and the `precedes` chain is the biographical
timeline, not the neuroendocrine cascade.

This is a genuine limit on the paper's central "**layer integration**" claim: today it
is integration-by-co-representation, not integration-by-bridging-axiom. It is also
*why* the instance-level cross-layer queries CSFO advertises (e.g. "which amplifier
raised the endocrine step that led to this outcome?") cannot yet be answered from the
XKGs — the edges to traverse do not exist.

**Root cause:** the ontology *defines* the bridges but does not *require* them (no SHACL
shape mandates step→step / step→outcome / moderator→step), and the extraction task never
asks for them — so, predictably, they are not produced.

---

## 4. Recommendations

1. **Constrain, then extract.** Add SHACL shapes that *require*, per cascade: each
   non-terminal step `directlyPrecedes`/`feedsForwardTo` the next-layer step; each
   terminal step `leadsToOutcome`; each extracted moderator is `amplifierIn`/`bufferIn`
   a named step via `hasInteractingModerator`. Validation pressure is what produced the
   correct single-frame binding; the same lever will produce the bridges.
2. **Extend the extraction schema** (full and lightweight) to emit the causal edges,
   not just layer-tagged nodes: `step_i directlyPrecedes step_{i+1}`,
   `terminal_step leadsToOutcome outcome`, `amplifier amplifies step`. Convert
   `licensingCue` from a string into an actual `triggeredByFrame`/`executesStep` edge.
3. **Materialise what OWL2 allows** via property chains (e.g. situation `evokes` frame ∘
   frame `triggersCascade` cascade ∘ cascade `hasStep` step ⇒ situation
   `hasCascadeStep` step), and defer the rest (feedback, quantitative moderation) to
   SHACL/SPIN as the core comment anticipates.
4. **Paper:** add this as an explicit limitation and a concrete next step — it converts
   a vague "integration" claim into a measurable target (bridge-instantiation rate,
   currently ≈0–10 %) and a clear path to raising it.
