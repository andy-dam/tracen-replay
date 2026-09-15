package timeline

import "testing"

// A viewer who says what was played sees that action in the season strip,
// and a turn the report saw nothing in stops counting as missing its action.
func TestApplyCorrectionsOverlaysTheViewersAction(t *testing.T) {
	summaries := []TurnSummary{
		{ID: "turn-005", ActionStatus: "missing_action"},
		{ID: "turn-006", ActionStatus: "one_action", ActionKind: "training", TrainingOption: "guts", ActionCount: 1},
		{ID: "turn-007", ActionStatus: "one_action", ActionKind: "race", ActionCount: 1},
	}
	ApplyCorrections(summaries, []Correction{
		{TurnID: "turn-005", Action: &CorrectionAction{Kind: "rest"}},
		{TurnID: "turn-006", Action: &CorrectionAction{Kind: "training", TrainingOption: "speed"}},
		{TurnID: "turn-007", Changes: []CorrectionChange{{Field: "speed", Amount: 3}}},
	})
	if s := summaries[0]; s.ActionKind != "rest" || !s.ActionFilledIn || s.ActionStatus != "one_action" || s.ActionCount != 1 {
		t.Fatalf("filled-in rest: %+v", s)
	}
	if s := summaries[1]; s.ActionKind != "training" || s.TrainingOption != "speed" || !s.ActionFilledIn || s.ActionStatus != "one_action" {
		t.Fatalf("corrected training option: %+v", s)
	}
	if s := summaries[2]; s.ActionKind != "race" || s.ActionFilledIn {
		t.Fatalf("a correction without an action leaves the report's reading: %+v", s)
	}
}
