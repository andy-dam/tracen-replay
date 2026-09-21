package store

import (
	"context"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/timeline"
)

func TestCorrectionsRoundTrip(t *testing.T) {
	s, _ := open(t)
	ctx := context.Background()
	if _, ok, err := s.GetCorrection(ctx, "rep", "turn-001", "u1"); err != nil || ok {
		t.Fatalf("empty store: %v %v", ok, err)
	}
	c := timeline.Correction{ReportID: "rep", TurnID: "turn-001", UserID: "u1",
		Action:  &timeline.CorrectionAction{Kind: "training", TrainingOption: "guts", Name: "Incline", Gains: map[string]int{"guts": 11, "power": 2}},
		Changes: []timeline.CorrectionChange{{Field: "speed", Amount: 30, Note: "Shine On Forever"}}, Note: "seen on the frame"}
	if err := s.PutCorrection(ctx, c); err != nil {
		t.Fatal(err)
	}
	got, ok, err := s.GetCorrection(ctx, "rep", "turn-001", "u1")
	if err != nil || !ok || got.Action.Name != "Incline" || got.Action.Gains["guts"] != 11 || len(got.Changes) != 1 || got.Changes[0].Amount != 30 || got.Note != "seen on the frame" {
		t.Fatalf("round trip: %v %v %+v", ok, err, got)
	}
	c.Changes[0].Amount = 31
	if err := s.PutCorrection(ctx, c); err != nil {
		t.Fatal(err)
	}
	list, err := s.ListCorrections(ctx, "rep", "u1")
	if err != nil || len(list) != 1 || list[0].Changes[0].Amount != 31 || list[0].Resolved {
		t.Fatalf("replace: %v %+v", err, list)
	}
	// The resolved mark alone is a correction, and it is kept.
	mark := timeline.Correction{ReportID: "rep", TurnID: "turn-002", UserID: "u1", Resolved: true}
	if err := mark.Validate(); err != nil {
		t.Fatalf("a resolved mark alone: %v", err)
	}
	if err := s.PutCorrection(ctx, mark); err != nil {
		t.Fatal(err)
	}
	if got, ok, err := s.GetCorrection(ctx, "rep", "turn-002", "u1"); err != nil || !ok || !got.Resolved {
		t.Fatalf("the resolved mark: %v %v %+v", ok, err, got)
	}
	if err := (&timeline.Correction{ReportID: "rep", TurnID: "turn-003", UserID: "u1"}).Validate(); err == nil {
		t.Fatal("an empty correction is still refused")
	}
	if _, ok, _ := s.GetCorrection(ctx, "rep", "turn-001", "u2"); ok {
		t.Fatal("corrections are per viewer")
	}
	if err := s.DeleteCorrection(ctx, "rep", "turn-001", "u1"); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := s.GetCorrection(ctx, "rep", "turn-001", "u1"); ok {
		t.Fatal("delete")
	}
}
