package timeline

import (
	"strings"
	"testing"
)

func ip(v int) *int { return &v }

func accountingTurn() Turn {
	return Turn{ID: "turn-028", Accounting: map[string]map[string]FieldAccounting{
		"stats": {
			"speed": {Before: ip(348), After: ip(378), Direct: ip(0), Derived: ip(0), Status: "unexplained_change", Unresolved: ip(30)},
			"wit":   {Before: ip(455), After: ip(465), Direct: ip(10), Derived: ip(0), Status: "balanced_observations"},
			"guts":  {Before: ip(252), After: nil, Status: "missing_endpoint"},
		},
	}}
}

func TestVerifyBalancesWhenTheSuppliedAmountLandsOnTheObservedValue(t *testing.T) {
	c := Correction{Changes: []CorrectionChange{{Field: "speed", Amount: 30, Note: "Shine On Forever"}}}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	v := Verify(accountingTurn(), nil, c)
	if !v.Balanced || !v.Verified || len(v.Fields) != 1 || v.Fields[0].Status != "balanced" || *v.Fields[0].Residual != 30 {
		t.Fatalf("expected a balanced speed check, got %+v", v)
	}
	if !strings.HasPrefix(v.Summary, "Adds up") {
		t.Fatalf("summary: %q", v.Summary)
	}
}

func TestVerifyReportsTheDifferenceWhenOff(t *testing.T) {
	v := Verify(accountingTurn(), nil, Correction{Changes: []CorrectionChange{{Field: "speed", Amount: 25}}})
	if v.Balanced || v.Verified || v.Fields[0].Status != "off" || !strings.Contains(v.Summary, "speed by +5") {
		t.Fatalf("expected off by five, got %+v", v)
	}
}

func TestVerifyCannotCheckAFieldWithoutBothEndpoints(t *testing.T) {
	v := Verify(accountingTurn(), nil, Correction{Action: &CorrectionAction{Kind: "training", TrainingOption: "guts", Gains: map[string]int{"guts": 11}}})
	// speed still has an unexplained residual of 30 that the correction did not touch: reported as open, not as a failure.
	var guts, speed FieldVerification
	for _, f := range v.Fields {
		switch f.Field {
		case "guts":
			guts = f
		case "speed":
			speed = f
		}
	}
	if guts.Status != "unverifiable" || speed.Status != "open" || v.Verified || !v.Balanced || !strings.Contains(v.Summary, "still unexplained: speed +30") {
		t.Fatalf("unexpected verification %+v", v)
	}
}

func TestValidateRejectsNonsense(t *testing.T) {
	bad := []Correction{
		{},
		{Changes: []CorrectionChange{{Field: "luck", Amount: 3}}},
		{Changes: []CorrectionChange{{Field: "speed", Amount: 0}}},
		{Action: &CorrectionAction{Kind: "nap"}},
		{Action: &CorrectionAction{Kind: "training", TrainingOption: "skill_points"}},
		{Action: &CorrectionAction{Kind: "training", TrainingOption: "speed", Gains: map[string]int{"speed": 9999}}},
	}
	for i, c := range bad {
		if err := c.Validate(); err == nil {
			t.Fatalf("case %d should be rejected", i)
		}
	}
	good := Correction{Action: &CorrectionAction{Kind: "Rest"}, Changes: []CorrectionChange{{Field: "Dance", Channel: "performance", Amount: 5}}}
	if err := good.Validate(); err != nil || good.Action.Kind != "rest" || good.Changes[0].Field != "dance" {
		t.Fatalf("normalization failed: %v %+v", err, good)
	}
}

func TestVerifyAppliesEntryEditsAndAddedEvents(t *testing.T) {
	turn := Turn{ID: "turn-028", Accounting: map[string]map[string]FieldAccounting{"stats": {
		"speed": {Before: ip(348), After: ip(378), Direct: ip(30), Derived: ip(0), Status: "balanced_observations"},
		"guts":  {Before: ip(252), After: ip(263), Direct: ip(0), Derived: ip(11), TurnDifference: ip(11), Status: "balanced_with_derived_changes"},
	}}}
	yes := true
	entries := []Entry{{ID: "entry-0116", Changes: map[string]map[string]Change{"stats": {"speed": {Amount: ip(30), Basis: "observed_receipt"}}}}}
	// The viewer says the receipt read 20, not 30, and adds an event worth +10: still adds up.
	c := Correction{Entries: map[string]EntryEdit{"entry-0116": {Changes: map[string]map[string]int{"stats": {"speed": 20}}}},
		Added: []AddedEvent{{ID: "user-1", Kind: "event", Title: "Fan Letter", Changes: map[string]map[string]int{"stats": {"speed": 10}}}}}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	v := Verify(turn, entries, c)
	var speed, guts FieldVerification
	for _, f := range v.Fields {
		if f.Field == "speed" {
			speed = f
		}
		if f.Field == "guts" {
			guts = f
		}
	}
	if !v.Balanced || speed.Status != "balanced" || speed.Supplied != 0 || guts.Status != "turn_difference" || guts.TurnDifference != 11 {
		t.Fatalf("unexpected %+v", v)
	}
	// Deleting the receipt without a replacement leaves speed short by 30.
	del := Correction{Entries: map[string]EntryEdit{"entry-0116": {Deleted: true}}}
	if v := Verify(turn, entries, del); v.Balanced || !strings.Contains(v.Summary, "speed by +30") {
		t.Fatalf("delete: %+v", v)
	}
	// The viewer's own guts amount replaces the extrapolation.
	own := Correction{Changes: []CorrectionChange{{Field: "guts", Amount: 11}}}
	if v := Verify(turn, entries, own); !v.Verified {
		t.Fatalf("replace extrapolation: %+v", v)
	}
	_ = yes
}
