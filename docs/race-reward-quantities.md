# Race reward quantities

Race result parsing retains high-confidence `xN` or `×N` text beneath a visible Items heading in the supported gameplay layout. A result group exposes `visible_item_reward_snapshots` only after matching quantities and positions appear at two distinct timestamps no more than 500 ms apart. A missing observation, movement, changed quantity, or longer gap breaks the snapshot.

Each snapshot carries frame evidence and unknown item names. Several visible quantities can coexist. Snapshots are observations, not inventory additions: repeated or scrolling views cannot be summed, and neither item identity nor list completeness is certified.

Both supplied recordings were rebuilt from cached OCR. The original has quantity snapshots for 12 race results; the separate recording has them for 11 of 12. These are extraction counts, not manually verified accuracy scores.

The preserved source-selected 150–160 second debut reference in the separate recording previously matched 10 of 11 scored fields. It now matches 11 of 11, including the visible quantity 200. Reference screenshot hashes were checked, and the initial failed evaluation remains separate from the new quantity evaluation. Item identity, pre-race fans, strategy, and race time remain unscored by that check.

All 263 unit tests and the frozen `regression-23` checks pass. The separate recording's stat, performance, and fan intervals remain balanced. Full-recording recall, complete inventory, active concert bonuses, and unresolved non-stat mechanics still require validation; this change does not close the Go gate.
