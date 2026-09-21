package api

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

func (m *memoryAuth) DeleteUser(ctx context.Context, id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for email, user := range m.users {
		if user.ID == id {
			delete(m.users, email)
			delete(m.hashes, email)
		}
	}
	for token, session := range m.sessions {
		if session.user == id {
			delete(m.sessions, token)
		}
	}
	return nil
}

// Deleting an account asks for the password, refuses while an analysis is
// active, deletes the recordings' files and ends the session.
func TestDeleteAccount(t *testing.T) {
	srv, recs, dir := newAccountServer(t)
	andy := register(t, srv, "andy@example.com")
	me := call(t, srv, "GET", "/api/auth/me", nil, "", andy)
	if me.Code != http.StatusOK {
		t.Fatalf("me: %d", me.Code)
	}
	id := me.Body.String()[strings.Index(me.Body.String(), `"id":"`)+6:]
	id = id[:strings.Index(id, `"`)]
	file := filepath.Join(dir, "clip.mp4")
	if err := os.WriteFile(file, []byte("clip"), 0o644); err != nil {
		t.Fatal(err)
	}
	recs.recs["rec-1"] = jobs.Recording{ID: "rec-1", UserID: id, Name: "clip.mp4", Path: file, Size: 4}

	if rec := call(t, srv, "POST", "/api/auth/delete", strings.NewReader(`{"password":"wrong-password"}`), "application/json", andy); rec.Code != http.StatusForbidden {
		t.Fatalf("a wrong password: %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "POST", "/api/auth/delete", strings.NewReader(`{}`), "application/json", andy); rec.Code != http.StatusBadRequest {
		t.Fatalf("no password: %d", rec.Code)
	}
	if rec := call(t, srv, "POST", "/api/auth/delete", strings.NewReader(`{"password":"longenough"}`), "application/json", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("no session: %d", rec.Code)
	}

	fj := srv.cfg.Jobs.(*fakeJobs)
	fj.jobs["job-7"] = jobs.Job{ID: "job-7", UserID: id, Status: jobs.Running}
	if rec := call(t, srv, "POST", "/api/auth/delete", strings.NewReader(`{"password":"longenough"}`), "application/json", andy); rec.Code != http.StatusConflict || !strings.Contains(rec.Body.String(), "analysis_active") {
		t.Fatalf("with a running analysis: %d %s", rec.Code, rec.Body.String())
	}
	fj.jobs["job-7"] = jobs.Job{ID: "job-7", UserID: id, Status: jobs.Paused}

	rec := call(t, srv, "POST", "/api/auth/delete", strings.NewReader(`{"password":"longenough"}`), "application/json", andy)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("delete: %d %s", rec.Code, rec.Body.String())
	}
	if fj.jobs["job-7"].Status != jobs.Cancelled {
		t.Fatalf("the paused analysis is %s", fj.jobs["job-7"].Status)
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Fatalf("the recording's file is still there: %v", err)
	}
	if again := call(t, srv, "GET", "/api/auth/me", nil, "", andy); again.Code != http.StatusUnauthorized {
		t.Fatalf("the session still works: %d", again.Code)
	}
	body := `{"email":"andy@example.com","password":"longenough"}`
	if login := call(t, srv, "POST", "/api/auth/login", strings.NewReader(body), "application/json", ""); login.Code == http.StatusOK {
		t.Fatal("the deleted account can still sign in")
	}
}
