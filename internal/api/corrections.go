package api

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/andy-dam/tracen-replay/internal/timeline"
)

// Corrections stores what viewers filled in per report turn.
type Corrections interface {
	GetCorrection(ctx context.Context, reportID, turnID, userID string) (timeline.Correction, bool, error)
	PutCorrection(ctx context.Context, c timeline.Correction) error
	DeleteCorrection(ctx context.Context, reportID, turnID, userID string) error
	ListCorrections(ctx context.Context, reportID, userID string) ([]timeline.Correction, error)
}

// correctionsEnabled answers 404 when the service runs without a store.
func (s *Server) correctionsEnabled(w http.ResponseWriter) bool {
	if s.cfg.Corrections == nil {
		writeError(w, http.StatusNotFound, "corrections_unavailable", "this service does not store corrections")
		return false
	}
	return true
}

// getCorrection returns the viewer's fill-in for one turn with its check.
func (s *Server) getCorrection(w http.ResponseWriter, r *http.Request) {
	if !s.correctionsEnabled(w) {
		return
	}
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	turn, found := doc.Turn(r.PathValue("turn"))
	if !found {
		writeError(w, http.StatusNotFound, "unknown_turn", "no such turn in this report")
		return
	}
	c, exists, err := s.cfg.Corrections.GetCorrection(r.Context(), report.ID, turn.ID, userFrom(r).ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_failed", err.Error())
		return
	}
	if !exists {
		writeJSON(w, http.StatusOK, map[string]any{"correction": nil, "verification": nil})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"correction": c, "verification": timeline.Verify(turn, doc.TurnEntries(turn.ID), c)})
}

// putCorrection stores the viewer's fill-in for one turn and checks it
// against the turn's observed endpoints.
func (s *Server) putCorrection(w http.ResponseWriter, r *http.Request) {
	if !s.correctionsEnabled(w) {
		return
	}
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	turn, found := doc.Turn(r.PathValue("turn"))
	if !found {
		writeError(w, http.StatusNotFound, "unknown_turn", "no such turn in this report")
		return
	}
	var body timeline.Correction
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384)).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_correction", "the body is not a correction")
		return
	}
	body.ReportID, body.TurnID, body.UserID = report.ID, turn.ID, userFrom(r).ID
	if body.Changes == nil {
		body.Changes = []timeline.CorrectionChange{}
	}
	if err := body.Validate(); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_correction", err.Error())
		return
	}
	if previous, exists, err := s.cfg.Corrections.GetCorrection(r.Context(), report.ID, turn.ID, body.UserID); err == nil && exists {
		body.CreatedAt = previous.CreatedAt
	}
	if body.CreatedAt.IsZero() {
		body.CreatedAt = time.Now()
	}
	body.UpdatedAt = time.Now()
	if err := s.cfg.Corrections.PutCorrection(r.Context(), body); err != nil {
		writeError(w, http.StatusInternalServerError, "store_failed", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"correction": body, "verification": timeline.Verify(turn, doc.TurnEntries(turn.ID), body)})
}

// deleteCorrection removes the viewer's fill-in for one turn.
func (s *Server) deleteCorrection(w http.ResponseWriter, r *http.Request) {
	if !s.correctionsEnabled(w) {
		return
	}
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	turn, found := doc.Turn(r.PathValue("turn"))
	if !found {
		writeError(w, http.StatusNotFound, "unknown_turn", "no such turn in this report")
		return
	}
	if err := s.cfg.Corrections.DeleteCorrection(r.Context(), report.ID, turn.ID, userFrom(r).ID); err != nil {
		writeError(w, http.StatusInternalServerError, "store_failed", err.Error())
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// listCorrections returns every fill-in the viewer made on the report, each
// with its check, so the check tab and the timeline can mark the turns.
func (s *Server) listCorrections(w http.ResponseWriter, r *http.Request) {
	if !s.correctionsEnabled(w) {
		return
	}
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	list, err := s.cfg.Corrections.ListCorrections(r.Context(), report.ID, userFrom(r).ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_failed", err.Error())
		return
	}
	type item struct {
		Correction   timeline.Correction   `json:"correction"`
		Verification timeline.Verification `json:"verification"`
	}
	out := []item{}
	for _, c := range list {
		turn, found := doc.Turn(c.TurnID)
		if !found {
			continue
		}
		out = append(out, item{Correction: c, Verification: timeline.Verify(turn, doc.TurnEntries(turn.ID), c)})
	}
	writeJSON(w, http.StatusOK, map[string]any{"corrections": out})
}
