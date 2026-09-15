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

func amount(n int, basis string) Change {
	return Change{Amount: &n, Basis: basis}
}

// A race-day turn shows no stat bar, so its opening is never observed; the
// summary carries the previous turn's opening plus what its entries count,
// marked as an estimate, and only when every stat of that opening was read.
func TestSummaryCarriesAnUnobservedOpeningFromThePreviousTurn(t *testing.T) {
	yes := true
	first := Turn{ID: "turn-001", Opening: Opening{Stats: ints(map[string]int{"speed": 100, "stamina": 200})}}
	second := Turn{ID: "turn-002"}
	entries := []Entry{
		{ID: "e1", Kind: "training", TurnID: "turn-001", Changes: map[string]map[string]Change{"stats": {"speed": amount(5, "observed_training_gain")}}},
		{ID: "e2", Kind: "outcome", TurnID: "turn-001", Changes: map[string]map[string]Change{"stats": {"stamina": amount(7, "observed_receipt"), "speed": {Amount: nil}}}},
		{ID: "e3", Kind: "outcome", TurnID: "turn-001", ConflictsPresent: &yes, Changes: map[string]map[string]Change{"stats": {"speed": amount(50, "observed_receipt")}}},
		{ID: "e4", Kind: "outcome", TurnID: "turn-002", Changes: map[string]map[string]Change{"stats": {"speed": amount(9, "observed_receipt")}}},
	}
	d := &Document{Turns: []Turn{first, second}, Entries: entries}
	summaries := d.TurnSummaries()
	if summaries[0].OpeningEstimate != nil {
		t.Fatalf("an observed opening needs no estimate: %+v", summaries[0])
	}
	got := summaries[1].OpeningEstimate
	if got == nil || summaries[1].OpeningEstimateBasis != "carried_from_previous_turn" || *got.Stats["speed"] != 105 || *got.Stats["stamina"] != 207 {
		t.Fatalf("carried opening: %+v (%s)", got, summaries[1].OpeningEstimateBasis)
	}
	if summaries[1].OpeningObserved {
		t.Fatal("an estimate is not an observation")
	}
	// A stat the previous turn did not read leaves no estimate at all.
	first.Opening.Stats["stamina"] = nil
	if s := (&Document{Turns: []Turn{first, second}, Entries: entries}).TurnSummaries(); s[1].OpeningEstimate != nil {
		t.Fatalf("a partial carry must not be offered: %+v", s[1].OpeningEstimate)
	}
	// Two unobserved turns in a row: the second has nothing to carry from.
	first.Opening.Stats["stamina"] = ints(map[string]int{"x": 200})["x"]
	if s := (&Document{Turns: []Turn{first, second, {ID: "turn-003"}}, Entries: entries}).TurnSummaries(); s[2].OpeningEstimate != nil {
		t.Fatal("an estimate is never carried across an unobserved turn")
	}
}
