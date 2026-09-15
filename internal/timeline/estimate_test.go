package timeline

import "testing"

func ints(values map[string]int) map[string]*int {
	out := make(map[string]*int, len(values))
	for k, v := range values {
		v := v
		out[k] = &v
	}
	return out
}

// A race-day turn shows no stat bar, so its opening is never observed; the
// summary carries the previous turn's opening plus what its entries explain,
// marked as an estimate, and only when every stat can be carried.
func TestSummaryCarriesAnUnobservedOpeningFromThePreviousTurn(t *testing.T) {
	zero := 0
	five := 5
	first := Turn{ID: "turn-001", Opening: Opening{Stats: ints(map[string]int{"speed": 100, "stamina": 200})},
		Accounting: map[string]map[string]FieldAccounting{"stats": {
			"speed":   {Direct: &five, Derived: &zero},
			"stamina": {Direct: &zero, Derived: &five},
		}}}
	second := Turn{ID: "turn-002"}
	d := &Document{Turns: []Turn{first, second}}
	summaries := d.TurnSummaries()
	if summaries[0].OpeningEstimate != nil {
		t.Fatalf("an observed opening needs no estimate: %+v", summaries[0])
	}
	got := summaries[1].OpeningEstimate
	if got == nil || summaries[1].OpeningEstimateBasis != "carried_from_previous_turn" || *got.Stats["speed"] != 105 || *got.Stats["stamina"] != 205 {
		t.Fatalf("carried opening: %+v (%s)", got, summaries[1].OpeningEstimateBasis)
	}
	if summaries[1].OpeningObserved {
		t.Fatal("an estimate is not an observation")
	}
	// A stat the previous turn could not account for leaves no estimate at all.
	first.Accounting["stats"]["stamina"] = FieldAccounting{Direct: &zero}
	if s := (&Document{Turns: []Turn{first, second}}).TurnSummaries(); s[1].OpeningEstimate != nil {
		t.Fatalf("a partial carry must not be offered: %+v", s[1].OpeningEstimate)
	}
	// Two unobserved turns in a row: the second has nothing to carry from.
	if s := (&Document{Turns: []Turn{first, second, {ID: "turn-003"}}}).TurnSummaries(); s[2].OpeningEstimate != nil {
		t.Fatal("an estimate is never carried across an unobserved turn")
	}
}
