package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/andy-dam/tracen-replay/internal/timeline"
)

// correctionPayload is the stored part of a correction; the keys are the row.
type correctionPayload struct {
	Action  *timeline.CorrectionAction    `json:"action,omitempty"`
	Changes []timeline.CorrectionChange   `json:"changes"`
	Entries map[string]timeline.EntryEdit `json:"entries,omitempty"`
	Added   []timeline.AddedEvent         `json:"added,omitempty"`
	Note    string                        `json:"note,omitempty"`
}

func scanCorrection(row interface{ Scan(...any) error }) (timeline.Correction, error) {
	var c timeline.Correction
	var payload string
	var created, updated sql.NullString
	if err := row.Scan(&c.ReportID, &c.TurnID, &c.UserID, &payload, &created, &updated); err != nil {
		return timeline.Correction{}, err
	}
	var body correctionPayload
	if err := json.Unmarshal([]byte(payload), &body); err != nil {
		return timeline.Correction{}, err
	}
	c.Action, c.Changes, c.Entries, c.Added, c.Note = body.Action, body.Changes, body.Entries, body.Added, body.Note
	if c.Changes == nil {
		c.Changes = []timeline.CorrectionChange{}
	}
	c.CreatedAt, c.UpdatedAt = parseStamp(created), parseStamp(updated)
	return c, nil
}

const correctionColumns = `report_id, turn_id, user_id, payload, created_at, updated_at`

// GetCorrection returns the viewer's correction for one turn, if any.
func (s *Store) GetCorrection(ctx context.Context, reportID, turnID, userID string) (timeline.Correction, bool, error) {
	c, err := scanCorrection(s.queryRow(ctx, `SELECT `+correctionColumns+` FROM corrections WHERE report_id=? AND turn_id=? AND user_id=?`,
		reportID, turnID, userID))
	if errors.Is(err, sql.ErrNoRows) {
		return timeline.Correction{}, false, nil
	}
	if err != nil {
		return timeline.Correction{}, false, err
	}
	return c, true, nil
}

// PutCorrection creates or replaces the viewer's correction for one turn.
func (s *Store) PutCorrection(ctx context.Context, c timeline.Correction) error {
	payload, err := json.Marshal(correctionPayload{Action: c.Action, Changes: c.Changes, Entries: c.Entries, Added: c.Added, Note: c.Note})
	if err != nil {
		return err
	}
	now := time.Now()
	if c.CreatedAt.IsZero() {
		c.CreatedAt = now
	}
	_, err = s.exec(ctx, `INSERT INTO corrections (`+correctionColumns+`) VALUES (?,?,?,?,?,?)
		ON CONFLICT(report_id, turn_id, user_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at`,
		c.ReportID, c.TurnID, c.UserID, string(payload), stamp(c.CreatedAt), stamp(now))
	return err
}

// DeleteCorrection removes the viewer's correction for one turn.
func (s *Store) DeleteCorrection(ctx context.Context, reportID, turnID, userID string) error {
	_, err := s.exec(ctx, `DELETE FROM corrections WHERE report_id=? AND turn_id=? AND user_id=?`, reportID, turnID, userID)
	return err
}

// ListCorrections returns every correction the viewer made on one report.
func (s *Store) ListCorrections(ctx context.Context, reportID, userID string) ([]timeline.Correction, error) {
	rows, err := s.query(ctx, `SELECT `+correctionColumns+` FROM corrections WHERE report_id=? AND user_id=? ORDER BY turn_id`, reportID, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []timeline.Correction
	for rows.Next() {
		c, err := scanCorrection(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}
