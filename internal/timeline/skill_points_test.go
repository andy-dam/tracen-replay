package timeline

import "testing"

// Gains add up as earned and purchases as spent; an entry that only refers
// to an award counted elsewhere, and a change with no amount, count for
// nothing.
func TestSkillPointTotals(t *testing.T) {
	amount := func(n int) map[string]map[string]Change {
		return map[string]map[string]Change{"stats": {"skill_points": {Amount: &n}}}
	}
	doc := &Document{Entries: []Entry{
		{ID: "a", Changes: amount(45)},
		{ID: "b", Changes: amount(120)},
		{ID: "c", Changes: amount(-160)},
		{ID: "d", Changes: amount(30), AccountingRole: "reference_only_not_an_additional_award"},
		{ID: "e", Changes: map[string]map[string]Change{"stats": {"skill_points": {}}}},
		{ID: "f", Changes: map[string]map[string]Change{"stats": {"speed": {}}}},
	}}
	if got := doc.SkillPointTotals(); got.Earned != 165 || got.Spent != 160 {
		t.Fatalf("totals %+v, want 165 earned and 160 spent", got)
	}
}
