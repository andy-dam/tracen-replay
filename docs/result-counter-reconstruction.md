# Result values with an obscured cap

A training-result counter can expose its current value while an animation obscures the cap. For example, `576/13` contains a readable current value but does not establish a cap of 13 or 1300.

The parser retains these observations separately from complete result values and stat caps. A partial observation requires confidence of at least 97, a complete one-to-four-digit value followed by a literal slash, and either no cap digits or an incomplete one-to-three-digit cap prefix. Bare numbers, signed gains, malformed text and low-confidence reads are excluded. Conflicting original and padded readings remain separate candidates.

A partial counter can corroborate a training-gain candidate only when a later complete, high-confidence result in the same training result agrees with its value. It must appear after the last displayed gain candidate. The existing requirements for a nearby pre-training state, distinct timestamps and temporal span still apply. A later career-hub checkpoint is not an input to this result-level check.

`partial_result_counter_evidence` records the source frames and the original or padded counter readings used. Multiple crops at one timestamp cannot supply additional temporal observations. The cap remains unknown in the partial observations; it is read only from the complete fraction.

The source regression retains three readings of 576 with an obscured cap, followed by a complete `576/1300` result. Alongside the pre-training value of 544 and the displayed gain candidates, this corroborates the 32-wit award. Removing the complete result causes the regression to abstain.
