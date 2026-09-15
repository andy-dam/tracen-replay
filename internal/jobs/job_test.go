package jobs

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// A queued job has not started: its JSON must not carry a zero start time,
// which the client would otherwise show as an elapsed time counted from year 1.
func TestQueuedJobOmitsZeroTimes(t *testing.T) {
	job := Job{ID: "j", Status: Queued, CreatedAt: time.Date(2026, 9, 15, 1, 32, 0, 0, time.UTC)}
	body, err := json.Marshal(job)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"started_at", "finished_at", "stage_at"} {
		if strings.Contains(string(body), key) {
			t.Errorf("queued job JSON carries %s: %s", key, body)
		}
	}
	job.StartedAt = job.CreatedAt.Add(time.Minute)
	body, _ = json.Marshal(job)
	if !strings.Contains(string(body), `"started_at":"2026-09-15T01:33:00Z"`) {
		t.Errorf("started job JSON lacks its start time: %s", body)
	}
}
