package timeline

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadMiniFixture(t *testing.T) {
	d, err := Load(filepath.Join("..", "..", "testdata", "timeline", "mini.json"))
	if err != nil {
		t.Fatal(err)
	}
	if d.Source.DurationMS != 120000 || len(d.Turns) != 2 || len(d.Entries) != 4 {
		t.Fatalf("unexpected document %+v", d.Source)
	}
	first, _ := d.Turn("turn-001")
	if first.Opening.Stats != nil || first.Accounting["stats"]["speed"].Before != nil || *first.Accounting["stats"]["speed"].After != 137 {
		t.Fatalf("unknown opening must stay unknown: %+v", first)
	}
	second, _ := d.Turn("turn-002")
	if *second.Opening.Stats["speed"] != 137 || *second.Opening.Performance["composure"] != 12 || *second.Accounting["stats"]["power"].Unresolved != 7 {
		t.Fatalf("turn-002 values: %+v", second)
	}
	entries := d.TurnEntries("turn-002")
	if len(entries) != 3 || entries[0].Kind != "training" || *entries[0].Changes["stats"]["speed"].Amount != 13 || entries[0].Changes["stats"]["speed"].Basis != "observed_training_gain" {
		t.Fatalf("turn-002 entries: %+v", entries)
	}
	if debit := entries[2].Changes["stats"]["skill_points"]; *debit.Amount != -100 || debit.Basis != "committed_skill_debit" {
		t.Fatalf("skill debit: %+v", debit)
	}
	if un := d.Unassigned(); len(un) != 1 || un[0].ID != "entry-0001" || un[0].AssignmentBasis != "outside_observed_turns" {
		t.Fatalf("unassigned: %+v", un)
	}
	if len(d.TurnEntries("")) != 0 {
		t.Fatal("an empty turn id must not select the unassigned entries")
	}
	summaries := d.TurnSummaries()
	if summaries[0].OpeningObserved || summaries[0].EntryCount != 0 || summaries[0].AccountingStatus["missing_endpoint"] != 1 {
		t.Fatalf("turn-001 summary: %+v", summaries[0])
	}
	if !summaries[1].OpeningObserved || summaries[1].EntryCount != 3 || summaries[1].AccountingStatus["unexplained_change"] != 1 || summaries[1].AccountingStatus["balanced_observations"] != 1 {
		t.Fatalf("turn-002 summary: %+v", summaries[1])
	}
	if string(summaries[1].CalendarValue) != `"Junior Year Early Jul"` {
		t.Fatalf("calendar value passthrough: %s", summaries[1].CalendarValue)
	}
}

func TestParseRejectsForeignSchemaAndBrokenReferences(t *testing.T) {
	cases := map[string]string{
		"schema":         `{"schema_version":"tracen-replay/timeline-v2","turns":[],"entries":[]}`,
		"repeated turn":  `{"schema_version":"tracen-replay/timeline-v1","turns":[{"id":"t"},{"id":"t"}],"entries":[]}`,
		"unknown turn":   `{"schema_version":"tracen-replay/timeline-v1","turns":[{"id":"t"}],"entries":[{"id":"e","kind":"outcome","turn_id":"x"}]}`,
		"repeated entry": `{"schema_version":"tracen-replay/timeline-v1","turns":[],"entries":[{"id":"e","kind":"outcome"},{"id":"e","kind":"outcome"}]}`,
		"entry kind":     `{"schema_version":"tracen-replay/timeline-v1","turns":[],"entries":[{"id":"e"}]}`,
	}
	for name, input := range cases {
		if _, err := Parse([]byte(input)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

// TestLoadRealDevelopmentTimeline reads one of the accepted development
// timelines when it is present on this machine; CI skips it.
func TestLoadRealDevelopmentTimeline(t *testing.T) {
	path := filepath.Join("..", "..", ".local", "final-reliability-v1", "worker-runs", "post-recognition-g8-v11-prepared", "v1", "timeline.json")
	if _, err := os.Stat(path); err != nil {
		t.Skip("development timeline not available")
	}
	d, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(d.Turns) == 0 || len(d.Entries) == 0 || d.Summary.ObservedTurnWindows != len(d.Turns) {
		t.Fatalf("turns %d entries %d observed_turn_windows %d", len(d.Turns), len(d.Entries), d.Summary.ObservedTurnWindows)
	}
	counts := map[string]int{}
	for _, entry := range d.Entries {
		counts[entry.Kind]++
	}
	for kind, n := range d.Summary.EntryCounts {
		if counts[kind] != n {
			t.Fatalf("entry_counts[%s]=%d but %d entries of that kind", kind, n, counts[kind])
		}
	}
	assigned := 0
	for _, summary := range d.TurnSummaries() {
		assigned += summary.EntryCount
	}
	if assigned+len(d.Unassigned()) != len(d.Entries) {
		t.Fatalf("assigned %d + unassigned %d != %d", assigned, len(d.Unassigned()), len(d.Entries))
	}
}
