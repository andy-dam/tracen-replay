// Package timeline reads the compact timeline document the analyzer writes
// beside its report (schema tracen-replay/timeline-v1). It is the only
// analyzer output the browser-facing endpoints are built on: the turns with
// their opening states and per-field accounting, and every ledger entry with
// its timestamps, changes and accepted basis. Frame images are never
// referenced; a fact is located by its source timestamp in milliseconds.
package timeline

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
)

// SchemaVersion is the document schema this package understands.
const SchemaVersion = "tracen-replay/timeline-v1"

// Document is one analyzed recording.
type Document struct {
	SchemaVersion       string      `json:"schema_version"`
	ReportSchemaVersion string      `json:"report_schema_version"`
	Source              Source      `json:"source"`
	Recognition         Recognition `json:"recognition"`
	Turns               []Turn      `json:"turns"`
	Entries             []Entry     `json:"entries"`
	Summary             Summary     `json:"summary"`
	Note                string      `json:"note,omitempty"`
}

// Source identifies the recording.
type Source struct {
	Name       string `json:"name"`
	SHA256     string `json:"sha256"`
	DurationMS int64  `json:"duration_ms"`
	Width      int    `json:"width"`
	Height     int    `json:"height"`
}

// Recognition names the OCR stack that produced the readings.
type Recognition struct {
	Model  string `json:"model"`
	Device string `json:"device"`
}

// Turn is one ledger turn. Opening values are unknown (nil) when the
// analyzer never observed the state; that is a fact to display, not a zero.
type Turn struct {
	ID            string          `json:"id"`
	Label         string          `json:"label"`
	Phase         string          `json:"phase"`
	CalendarValue json.RawMessage `json:"calendar_value,omitempty"`
	StartMS       *int64          `json:"start_ms"`
	EndMS         *int64          `json:"end_ms"`
	WindowKind    string          `json:"window_kind"`
	ActionStatus  string          `json:"action_status"`
	ActionCount   int             `json:"action_count"`
	// ExpectsOneAction is false for windows the ledger does not hold to one
	// decision: the career start, pre-debut countdown segments, unresolved phases.
	ExpectsOneAction bool `json:"expects_one_action"`
	// ScheduledRace names the finale race that ends a phase_race_turn; the
	// game schedules it, so it is not counted as the turn's decision.
	ScheduledRace string                                `json:"scheduled_race,omitempty"`
	Opening       Opening                               `json:"opening"`
	Accounting    map[string]map[string]FieldAccounting `json:"accounting"`
}

// Opening holds the observed values at the start of a turn per channel;
// a nil map means no opening observation exists for that channel.
type Opening struct {
	Stats       map[string]*int `json:"stats"`
	Performance map[string]*int `json:"performance"`
}

// FieldAccounting is the causal accounting of one field across a turn.
type FieldAccounting struct {
	Before     *int   `json:"before"`
	After      *int   `json:"after"`
	Status     string `json:"status"`
	Direct     *int   `json:"direct"`
	Derived    *int   `json:"derived"`
	Unresolved *int   `json:"unresolved"`
	// TurnDifference is the part of Derived that was worked out from the
	// difference between turns onto its only possible owner; shown as such
	// and replaceable by a viewer.
	TurnDifference *int `json:"turn_difference,omitempty"`
	// TurnDifferenceOwner names what the difference was worked onto:
	// "training", "event" (a receipt that lost its number), "lesson" or
	// "race" (the one race paying a turn's skill points).
	TurnDifferenceOwner string `json:"turn_difference_owner,omitempty"`
	// WindowStartMS and WindowEndMS bound the two observations compared.
	WindowStartMS *int64 `json:"window_start_ms,omitempty"`
	WindowEndMS   *int64 `json:"window_end_ms,omitempty"`
}

// Change is one recorded amount with the basis that established it, for
// example observed_receipt, observed_training_gain, state_derived or
// committed_skill_debit. Anything but a directly observed basis must be shown
// as such by a consumer.
type Change struct {
	Amount *int   `json:"amount"`
	Basis  string `json:"basis,omitempty"`
	// ReadAmount is the digits the panel showed when a clipped badge was
	// completed from the turn difference; Amount is then the completed gain.
	ReadAmount *int `json:"read_amount,omitempty"`
}

// Entry is one ledger entry. TurnID is empty for entries outside every
// observed turn window; Detail is the compact record of the underlying event
// and is passed to the browser unchanged.
type Entry struct {
	ID               string                       `json:"id"`
	Kind             string                       `json:"kind"`
	TurnID           string                       `json:"turn_id"`
	FirstSeenMS      *int64                       `json:"first_seen_ms"`
	LastSeenMS       *int64                       `json:"last_seen_ms"`
	AssignmentBasis  string                       `json:"assignment_basis,omitempty"`
	ContextTitle     string                       `json:"context_title,omitempty"`
	TrainingOption   string                       `json:"training_option,omitempty"`
	ActionKind       string                       `json:"action_kind,omitempty"`
	RawText          string                       `json:"raw_text,omitempty"`
	AccountingRole   string                       `json:"accounting_role,omitempty"`
	TransactionID    string                       `json:"transaction_id,omitempty"`
	RewardLinkStatus string                       `json:"reward_link_status,omitempty"`
	Effect           json.RawMessage              `json:"effect,omitempty"`
	AcceptedAward    *bool                        `json:"accepted_award,omitempty"`
	ConflictsPresent *bool                        `json:"conflicts_present,omitempty"`
	Changes          map[string]map[string]Change `json:"changes,omitempty"`
	Detail           json.RawMessage              `json:"detail,omitempty"`
}

// StageFailure is an enrichment stage the analyzer skipped fail-soft.
type StageFailure struct {
	Stage     string `json:"stage"`
	Error     string `json:"error"`
	Traceback string `json:"traceback,omitempty"`
}

// Summary carries the run-level counts.
type Summary struct {
	FieldStatusCounts   map[string]int `json:"field_status_counts"`
	ActionStatuses      map[string]int `json:"action_statuses"`
	ObservedTurnWindows int            `json:"observed_turn_windows"`
	EntryCounts         map[string]int `json:"entry_counts"`
	StageFailures       []StageFailure `json:"stage_failures"`
}

// Load reads and checks a timeline document.
func Load(path string) (*Document, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var d Document
	if err := json.NewDecoder(f).Decode(&d); err != nil {
		return nil, fmt.Errorf("timeline %s: %w", path, err)
	}
	if err := d.check(); err != nil {
		return nil, fmt.Errorf("timeline %s: %w", path, err)
	}
	return &d, nil
}

// Parse decodes a document held in memory.
func Parse(data []byte) (*Document, error) {
	var d Document
	if err := json.Unmarshal(data, &d); err != nil {
		return nil, err
	}
	if err := d.check(); err != nil {
		return nil, err
	}
	return &d, nil
}

func (d *Document) check() error {
	if d.SchemaVersion != SchemaVersion {
		return fmt.Errorf("schema %q, expected %q", d.SchemaVersion, SchemaVersion)
	}
	seen := make(map[string]bool, len(d.Turns))
	for i, turn := range d.Turns {
		if turn.ID == "" {
			return fmt.Errorf("turn %d has no id", i)
		}
		if seen[turn.ID] {
			return fmt.Errorf("turn id %q is repeated", turn.ID)
		}
		seen[turn.ID] = true
	}
	ids := make(map[string]bool, len(d.Entries))
	for i, entry := range d.Entries {
		if entry.ID == "" || entry.Kind == "" {
			return fmt.Errorf("entry %d lacks an id or kind", i)
		}
		if ids[entry.ID] {
			return fmt.Errorf("entry id %q is repeated", entry.ID)
		}
		ids[entry.ID] = true
		if entry.TurnID != "" && !seen[entry.TurnID] {
			return fmt.Errorf("entry %s refers to unknown turn %q", entry.ID, entry.TurnID)
		}
	}
	return nil
}

// Turn returns the turn with the given id.
func (d *Document) Turn(id string) (Turn, bool) {
	for _, turn := range d.Turns {
		if turn.ID == id {
			return turn, true
		}
	}
	return Turn{}, false
}

// TurnEntries returns the entries assigned to a turn, in document order.
func (d *Document) TurnEntries(turnID string) []Entry {
	var out []Entry
	for _, entry := range d.Entries {
		if entry.TurnID == turnID && turnID != "" {
			out = append(out, entry)
		}
	}
	return out
}

// Unassigned returns the entries outside every observed turn window. They
// stay accessible on their own; they are never folded into a neighbouring
// turn to make the display look complete.
func (d *Document) Unassigned() []Entry {
	var out []Entry
	for _, entry := range d.Entries {
		if entry.TurnID == "" {
			out = append(out, entry)
		}
	}
	return out
}

// TurnSummary is what a turn navigator needs without the entries.
type TurnSummary struct {
	ID               string          `json:"id"`
	Label            string          `json:"label"`
	Phase            string          `json:"phase"`
	CalendarValue    json.RawMessage `json:"calendar_value,omitempty"`
	StartMS          *int64          `json:"start_ms"`
	EndMS            *int64          `json:"end_ms"`
	WindowKind       string          `json:"window_kind"`
	ActionStatus     string          `json:"action_status"`
	ActionCount      int             `json:"action_count"`
	ExpectsOneAction bool            `json:"expects_one_action"`
	ScheduledRace    string          `json:"scheduled_race,omitempty"`
	EntryCount       int             `json:"entry_count"`
	// Differences lists the stat changes between this turn's observation and
	// the next that no captured event covers, with the window to look in.
	Differences      []Difference   `json:"differences,omitempty"`
	OpeningObserved  bool           `json:"opening_observed"`
	AccountingStatus map[string]int `json:"accounting_status_counts"`
	// ActionKind and TrainingOption describe the turn's first committed
	// action (training, race, rest, outing, infirmary) for the season strip.
	ActionKind     string `json:"action_kind,omitempty"`
	TrainingOption string `json:"training_option,omitempty"`
	// Opening carries the observed opening values so a stat chart across
	// turns needs no per-turn requests.
	Opening Opening `json:"opening"`
}

// TurnSummaries lists every turn with its entry count and the count of each
// accounting status across its fields.
func (d *Document) TurnSummaries() []TurnSummary {
	counts := make(map[string]int, len(d.Turns))
	actions := make(map[string]Entry, len(d.Turns))
	for _, entry := range d.Entries {
		if entry.TurnID == "" {
			continue
		}
		counts[entry.TurnID]++
		if entry.Kind != "committed_action" {
			continue
		}
		// The turn's decision is its first non-race action; a scheduled race
		// only stands in when nothing else was committed.
		if current, seen := actions[entry.TurnID]; !seen || (current.ActionKind == "race" && entry.ActionKind != "race") {
			actions[entry.TurnID] = entry
		}
	}
	out := make([]TurnSummary, 0, len(d.Turns))
	for _, turn := range d.Turns {
		statuses := map[string]int{}
		for _, fields := range turn.Accounting {
			for _, field := range fields {
				if field.Status != "" {
					statuses[field.Status]++
				}
			}
		}
		action := actions[turn.ID]
		out = append(out, TurnSummary{
			ID: turn.ID, Label: turn.Label, Phase: turn.Phase, CalendarValue: turn.CalendarValue,
			StartMS: turn.StartMS, EndMS: turn.EndMS, WindowKind: turn.WindowKind,
			ActionStatus: turn.ActionStatus, ActionCount: turn.ActionCount, ExpectsOneAction: expectsOneAction(turn), ScheduledRace: turn.ScheduledRace, EntryCount: counts[turn.ID],
			Differences:      differences(turn),
			OpeningObserved:  turn.Opening.Stats != nil || turn.Opening.Performance != nil,
			AccountingStatus: statuses, ActionKind: action.ActionKind, TrainingOption: action.TrainingOption,
			Opening: turn.Opening,
		})
	}
	return out
}

// expectsOneAction is the ledger's own rule (a dated calendar turn or a
// finale race turn holds one decision); timelines written before the field
// existed fall back to it.
func expectsOneAction(turn Turn) bool {
	return turn.ExpectsOneAction || turn.WindowKind == "calendar_turn" || turn.WindowKind == "phase_race_turn"
}

// Difference is one stat change between two observations that no captured
// event covers, or that was worked out from the difference onto its only
// possible owner: the sole training, the one receipt that lost its number,
// the one lesson without an observed cost, or the one race for skill points.
type Difference struct {
	Channel       string `json:"channel"`
	Field         string `json:"field"`
	Amount        int    `json:"amount"`
	WorkedOut     bool   `json:"worked_out"`      // assigned to its only possible owner from the difference
	Owner         string `json:"owner,omitempty"` // training, event, lesson or race when worked out
	WindowStartMS *int64 `json:"window_start_ms,omitempty"`
	WindowEndMS   *int64 `json:"window_end_ms,omitempty"`
}

func differences(turn Turn) []Difference {
	var out []Difference
	for _, channel := range []string{"stats", "performance"} {
		for field, fa := range turn.Accounting[channel] {
			if fa.Unresolved != nil && *fa.Unresolved != 0 {
				out = append(out, Difference{Channel: channel, Field: field, Amount: *fa.Unresolved, WindowStartMS: fa.WindowStartMS, WindowEndMS: fa.WindowEndMS})
			} else if fa.TurnDifference != nil && *fa.TurnDifference != 0 {
				out = append(out, Difference{Channel: channel, Field: field, Amount: *fa.TurnDifference, WorkedOut: true, Owner: fa.TurnDifferenceOwner, WindowStartMS: fa.WindowStartMS, WindowEndMS: fa.WindowEndMS})
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Channel != out[j].Channel {
			return out[i].Channel < out[j].Channel
		}
		return out[i].Field < out[j].Field
	})
	return out
}
