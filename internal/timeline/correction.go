package timeline

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Correction is a viewer's review overlay for one turn: the action the report
// did not see, amounts attributed to nothing in particular, edits to the
// report's own entries (amounts, deletion, a reviewed mark) and events the
// viewer adds. It lives beside the report, never inside it, and is checked
// against the turn's observed endpoints on every read, so an edited value is
// only ever shown as verified when the numbers add up to what the screen
// showed next.
type Correction struct {
	ReportID  string               `json:"report_id"`
	TurnID    string               `json:"turn_id"`
	UserID    string               `json:"-"`
	Action    *CorrectionAction    `json:"action,omitempty"`
	Changes   []CorrectionChange   `json:"changes"`
	Entries   map[string]EntryEdit `json:"entries,omitempty"`
	Added     []AddedEvent         `json:"added,omitempty"`
	Note      string               `json:"note,omitempty"`
	CreatedAt time.Time            `json:"created_at"`
	UpdatedAt time.Time            `json:"updated_at"`

	// Resolved is the viewer saying the turn needs no more checking, for a
	// turn whose numbers cannot be proven from the recording. A review that
	// closes everything the report asked about is resolved without it.
	Resolved bool `json:"resolved,omitempty"`
}

// CorrectionAction names the turn's decision when the report has none.
type CorrectionAction struct {
	Kind           string         `json:"kind"` // training, rest, outing, race
	TrainingOption string         `json:"training_option,omitempty"`
	Name           string         `json:"name,omitempty"`
	Gains          map[string]int `json:"gains,omitempty"` // stat field -> amount the action gave
}

// CorrectionChange attributes an amount of one field to something the viewer
// saw that no entry carries. Marked Misread, it says the opposite: the
// amount is not a change the game made but a number the report read
// wrongly at one end of the turn, and the field is set aside rather than
// explained. A worked-out amount the report put on an event is withdrawn
// with it.
type CorrectionChange struct {
	Field   string `json:"field"`
	Channel string `json:"channel,omitempty"` // stats (default) or performance
	Amount  int    `json:"amount"`
	Note    string `json:"note,omitempty"`
	Misread bool   `json:"misread,omitempty"`
}

// EntryEdit is the viewer's edit of one of the report's own entries. Changes
// replaces the entry's amount per field (a field left out keeps the report's
// reading); Deleted removes the entry from the arithmetic; Reviewed marks a
// flagged entry as checked without a change.
type EntryEdit struct {
	Changes  map[string]map[string]int `json:"changes,omitempty"` // channel -> field -> amount
	Deleted  bool                      `json:"deleted,omitempty"`
	Reviewed bool                      `json:"reviewed,omitempty"`
	Note     string                    `json:"note,omitempty"`
}

// AddedEvent is an event the viewer saw that the report has no entry for.
type AddedEvent struct {
	ID      string                    `json:"id"`
	Kind    string                    `json:"kind"` // event, training, race, rest, outing, purchase
	Title   string                    `json:"title,omitempty"`
	TimeMS  *int64                    `json:"time_ms,omitempty"`
	Changes map[string]map[string]int `json:"changes,omitempty"`
	Note    string                    `json:"note,omitempty"`
}

// StatFields and PerformanceFields are the channels a correction can touch.
var (
	StatFields        = []string{"speed", "stamina", "power", "guts", "wit", "skill_points"}
	PerformanceFields = []string{"dance", "passion", "vocal", "visual", "composure"}
	actionKinds       = map[string]bool{"training": true, "rest": true, "outing": true, "race": true}
	addedKinds        = map[string]bool{"event": true, "training": true, "race": true, "rest": true, "outing": true, "purchase": true}
)

const (
	maxCorrectionAmount = 5000
	maxCorrectionNote   = 500
	maxCorrectionItems  = 48
)

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}

func validChanges(changes map[string]map[string]int, what string) error {
	for channel, fields := range changes {
		var known []string
		switch channel {
		case "stats":
			known = StatFields
		case "performance":
			known = PerformanceFields
		default:
			return fmt.Errorf("%s: unknown channel %q", what, channel)
		}
		for field, amount := range fields {
			if !contains(known, field) {
				return fmt.Errorf("%s: unknown %s field %q", what, channel, field)
			}
			if amount < -maxCorrectionAmount || amount > maxCorrectionAmount {
				return fmt.Errorf("%s: %s out of range", what, field)
			}
		}
	}
	return nil
}

// Validate rejects anything that is not a plausible review entry: unknown
// kinds, fields or options, absurd amounts, or nothing at all.
func (c *Correction) Validate() error {
	if c.Action == nil && len(c.Changes) == 0 && len(c.Entries) == 0 && len(c.Added) == 0 && !c.Resolved {
		return errors.New("a correction needs an action, a change, an entry edit, an added event or the resolved mark")
	}
	if len(c.Changes) > maxCorrectionItems || len(c.Entries) > maxCorrectionItems || len(c.Added) > maxCorrectionItems {
		return fmt.Errorf("at most %d items of each kind", maxCorrectionItems)
	}
	if len(c.Note) > maxCorrectionNote {
		return fmt.Errorf("the note is longer than %d characters", maxCorrectionNote)
	}
	if a := c.Action; a != nil {
		a.Kind = strings.ToLower(strings.TrimSpace(a.Kind))
		if !actionKinds[a.Kind] {
			return fmt.Errorf("unknown action kind %q", a.Kind)
		}
		a.TrainingOption = strings.ToLower(strings.TrimSpace(a.TrainingOption))
		if a.Kind == "training" && !contains(StatFields[:5], a.TrainingOption) {
			return fmt.Errorf("a training needs one of speed, stamina, power, guts or wit, not %q", a.TrainingOption)
		}
		if a.Kind != "training" {
			a.TrainingOption = ""
		}
		a.Name = strings.TrimSpace(a.Name)
		if len(a.Name) > 80 {
			return errors.New("the action name is too long")
		}
		for field, amount := range a.Gains {
			if !contains(StatFields, field) {
				return fmt.Errorf("unknown gain field %q", field)
			}
			if amount < -maxCorrectionAmount || amount > maxCorrectionAmount {
				return fmt.Errorf("gain for %s out of range", field)
			}
		}
	}
	for i := range c.Changes {
		ch := &c.Changes[i]
		ch.Field = strings.ToLower(strings.TrimSpace(ch.Field))
		ch.Channel = strings.ToLower(strings.TrimSpace(ch.Channel))
		if ch.Channel == "" {
			ch.Channel = "stats"
		}
		if err := validChanges(map[string]map[string]int{ch.Channel: {ch.Field: ch.Amount}}, "change"); err != nil {
			return err
		}
		if ch.Amount == 0 {
			return fmt.Errorf("the change for %s is zero", ch.Field)
		}
		if len(ch.Note) > maxCorrectionNote {
			return fmt.Errorf("the note for %s is too long", ch.Field)
		}
	}
	for id, edit := range c.Entries {
		if strings.TrimSpace(id) == "" {
			return errors.New("an entry edit needs the entry id")
		}
		if err := validChanges(edit.Changes, "entry "+id); err != nil {
			return err
		}
		if len(edit.Note) > maxCorrectionNote {
			return fmt.Errorf("the note for entry %s is too long", id)
		}
	}
	seen := map[string]bool{}
	for i := range c.Added {
		a := &c.Added[i]
		a.ID = strings.TrimSpace(a.ID)
		if a.ID == "" || seen[a.ID] {
			return errors.New("each added event needs a unique id")
		}
		seen[a.ID] = true
		a.Kind = strings.ToLower(strings.TrimSpace(a.Kind))
		if !addedKinds[a.Kind] {
			return fmt.Errorf("unknown added event kind %q", a.Kind)
		}
		a.Title = strings.TrimSpace(a.Title)
		if len(a.Title) > 80 {
			return errors.New("an added event's title is too long")
		}
		if err := validChanges(a.Changes, "added event "+a.ID); err != nil {
			return err
		}
		if len(a.Note) > maxCorrectionNote {
			return fmt.Errorf("the note for added event %s is too long", a.ID)
		}
	}
	return nil
}

// FieldVerification is one field's arithmetic: what the screen showed before
// and after the turn, what the report already accounts for, what the viewer
// supplied, and whether the sum lands on the observed value.
type FieldVerification struct {
	Channel        string `json:"channel"`
	Field          string `json:"field"`
	Before         *int   `json:"before"`
	After          *int   `json:"after"`
	Recorded       int    `json:"recorded"`        // amounts the report explains (direct and derived) after the viewer's entry edits
	TurnDifference int    `json:"turn_difference"` // the part of the report's amount worked out from the difference between turns
	Residual       *int   `json:"residual"`        // after - before - recorded; nil when an endpoint was not observed
	Supplied       int    `json:"supplied"`        // what the correction adds for this field
	Status         string `json:"status"`          // balanced, off, unverifiable, open, turn_difference, misread
	WindowStart    *int64 `json:"window_start_ms,omitempty"`
	WindowEnd      *int64 `json:"window_end_ms,omitempty"`
}

// Verification is the outcome for one correction against its turn.
type Verification struct {
	Fields   []FieldVerification `json:"fields"`
	Balanced bool                `json:"balanced"` // no touched field is off
	Verified bool                `json:"verified"` // balanced, and at least one field had an observed residual to check
	Summary  string              `json:"summary"`
}

func deref(p *int) int {
	if p == nil {
		return 0
	}
	return *p
}

// counted reports whether an entry's change for a field is part of the
// report's accounting: it carries a basis and the entry is not in conflict.
func counted(entry Entry, channel, field string) (int, bool) {
	change, ok := entry.Changes[channel][field]
	if !ok || change.Amount == nil || change.Basis == "" {
		return 0, false
	}
	if entry.ConflictsPresent != nil && *entry.ConflictsPresent {
		return 0, false
	}
	return *change.Amount, true
}

// Verify checks a correction against the turn's accounting. The viewer's
// entry edits replace the report's amounts, deleted entries count for
// nothing, added events and action gains add, and an extrapolated amount is
// replaced by the viewer's own. A field is balanced when before + recorded +
// supplied equals after; fields without both endpoints cannot be verified.
func Verify(turn Turn, entries []Entry, c Correction) Verification {
	supplied := map[string]map[string]int{"stats": {}, "performance": {}}
	adjust := map[string]map[string]int{"stats": {}, "performance": {}} // edits to what the report counted
	if c.Action != nil {
		for field, amount := range c.Action.Gains {
			supplied["stats"][field] += amount
		}
	}
	misread := map[string]map[string]bool{"stats": {}, "performance": {}}
	for _, ch := range c.Changes {
		channel := ch.Channel
		if channel == "" {
			channel = "stats"
		}
		if ch.Misread {
			misread[channel][ch.Field] = true
			continue
		}
		supplied[channel][ch.Field] += ch.Amount
	}
	for _, added := range c.Added {
		for channel, fields := range added.Changes {
			for field, amount := range fields {
				supplied[channel][field] += amount
			}
		}
	}
	byID := map[string]Entry{}
	for _, e := range entries {
		byID[e.ID] = e
	}
	for id, edit := range c.Entries {
		entry, ok := byID[id]
		if !ok {
			continue
		}
		for _, channel := range []string{"stats", "performance"} {
			for field := range entry.Changes[channel] {
				reported, isCounted := counted(entry, channel, field)
				if edit.Deleted {
					if isCounted {
						adjust[channel][field] -= reported
					}
					continue
				}
				if override, has := edit.Changes[channel][field]; has {
					if isCounted {
						adjust[channel][field] += override - reported
					} else {
						adjust[channel][field] += override
					}
				}
			}
			for field, override := range edit.Changes[channel] {
				if _, known := entry.Changes[channel][field]; !known && !edit.Deleted {
					adjust[channel][field] += override
				}
			}
		}
	}
	var out Verification
	out.Balanced = true
	observed := 0
	var off, unverifiable, open, extrapolated, setAside []string
	for _, channel := range []string{"stats", "performance"} {
		fields := StatFields
		if channel == "performance" {
			fields = PerformanceFields
		}
		for _, field := range fields {
			fa, has := turn.Accounting[channel][field]
			give := supplied[channel][field] + adjust[channel][field]
			touchedByEdit := adjust[channel][field] != 0
			var residual *int
			recorded, extra := 0, 0
			if has {
				recorded = deref(fa.Direct) + deref(fa.Derived)
				extra = deref(fa.TurnDifference)
				if (supplied[channel][field] != 0 || misread[channel][field]) && extra != 0 {
					// The viewer's own amount replaces the worked-out one, and
					// a misread withdraws it.
					recorded -= extra
				}
				if fa.Before != nil && fa.After != nil {
					r := *fa.After - *fa.Before - recorded
					residual = &r
				}
			}
			touched := give != 0 || touchedByEdit || (residual != nil && *residual != 0) || extra != 0 || misread[channel][field]
			if !touched {
				continue
			}
			fv := FieldVerification{Channel: channel, Field: field, Recorded: recorded, TurnDifference: extra, Residual: residual, Supplied: give}
			if has {
				fv.Before, fv.After, fv.WindowStart, fv.WindowEnd = fa.Before, fa.After, fa.WindowStartMS, fa.WindowEndMS
			}
			switch {
			case misread[channel][field]:
				// The difference is a reading, not a change: nothing to
				// explain and nothing the screen can confirm.
				fv.Status = "misread"
				setAside = append(setAside, field)
			case give == 0 && !touchedByEdit && extra != 0 && residual != nil && *residual == 0:
				fv.Status = "turn_difference"
				extrapolated = append(extrapolated, fmt.Sprintf("%s %+d", field, extra))
			case give == 0 && !touchedByEdit:
				fv.Status = "open"
				if residual != nil {
					open = append(open, fmt.Sprintf("%s %+d", field, *residual))
				}
			case residual == nil:
				fv.Status = "unverifiable"
				unverifiable = append(unverifiable, field)
			case *residual == give:
				fv.Status = "balanced"
				observed++
			default:
				fv.Status = "off"
				off = append(off, fmt.Sprintf("%s by %+d", field, *residual-give))
				out.Balanced = false
			}
			out.Fields = append(out.Fields, fv)
		}
	}
	rank := map[string]int{"off": 0, "balanced": 1, "unverifiable": 2, "open": 3, "turn_difference": 4, "misread": 5}
	sort.SliceStable(out.Fields, func(i, j int) bool { return rank[out.Fields[i].Status] < rank[out.Fields[j].Status] })
	out.Verified = out.Balanced && observed > 0
	switch {
	case len(off) > 0:
		out.Summary = "Does not add up: " + strings.Join(off, ", ")
	case observed > 0 && len(unverifiable) == 0:
		out.Summary = "Adds up to the values observed next"
	case observed > 0:
		out.Summary = "Adds up where the next values were observed; not checkable on " + strings.Join(unverifiable, ", ")
	case len(unverifiable) > 0:
		out.Summary = "Cannot verify: the values before or after this turn were not observed"
	default:
		out.Summary = "Nothing to check"
	}
	if len(open) > 0 {
		out.Summary += ". Still unexplained: " + strings.Join(open, ", ")
	}
	if len(extrapolated) > 0 {
		out.Summary += ". Worked out from the difference between turns: " + strings.Join(extrapolated, ", ")
	}
	if len(setAside) > 0 {
		out.Summary += ". Read wrongly by the report: " + strings.Join(setAside, ", ")
	}
	return out
}
