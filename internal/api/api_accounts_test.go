package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// memoryAuth is an in-memory auth.Store for the handler tests.
type memoryAuth struct {
	mu       sync.Mutex
	users    map[string]auth.User
	hashes   map[string]string
	sessions map[string]struct {
		user    string
		expires time.Time
	}
}

func newMemoryAuth() *memoryAuth {
	return &memoryAuth{users: map[string]auth.User{}, hashes: map[string]string{}, sessions: map[string]struct {
		user    string
		expires time.Time
	}{}}
}

func (m *memoryAuth) CreateUser(ctx context.Context, user auth.User, hash string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.users[user.ID], m.hashes[user.ID] = user, hash
	return nil
}

func (m *memoryAuth) UserByEmail(ctx context.Context, email string) (auth.User, string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, u := range m.users {
		if u.Email == email {
			return u, m.hashes[u.ID], nil
		}
	}
	return auth.User{}, "", auth.ErrNotFound
}

func (m *memoryAuth) UserByID(ctx context.Context, id string) (auth.User, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	u, ok := m.users[id]
	if !ok {
		return auth.User{}, auth.ErrNotFound
	}
	return u, nil
}

func (m *memoryAuth) CreateSession(ctx context.Context, tokenHash, userID string, createdAt, expiresAt time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sessions[tokenHash] = struct {
		user    string
		expires time.Time
	}{userID, expiresAt}
	return nil
}

func (m *memoryAuth) SessionUser(ctx context.Context, tokenHash string, now time.Time) (auth.User, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[tokenHash]
	if !ok || !now.Before(s.expires) {
		return auth.User{}, auth.ErrNotFound
	}
	return m.users[s.user], nil
}

func (m *memoryAuth) DeleteSession(ctx context.Context, tokenHash string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.sessions, tokenHash)
	return nil
}

// memoryRecordings is an in-memory Recordings store.
type memoryRecordings struct {
	mu   sync.Mutex
	recs map[string]jobs.Recording
}

func (m *memoryRecordings) CreateRecording(ctx context.Context, r jobs.Recording) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.recs[r.ID] = r
	return nil
}

func (m *memoryRecordings) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.recs[id]
	if !ok {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return r, nil
}

func (m *memoryRecordings) ListRecordingsForUser(ctx context.Context, userID string) ([]jobs.Recording, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []jobs.Recording
	for _, r := range m.recs {
		if r.UserID == userID {
			out = append(out, r)
		}
	}
	return out, nil
}

func (m *memoryRecordings) DeleteRecording(ctx context.Context, id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.recs[id]; !ok {
		return &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	delete(m.recs, id)
	return nil
}

func (m *memoryRecordings) RecordingInUse(ctx context.Context, id string) (bool, error) {
	return false, nil
}

func newAccountServer(t *testing.T) (*Server, *memoryRecordings, string) {
	t.Helper()
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	report := fixtureReport(t)
	recs := &memoryRecordings{recs: map[string]jobs.Recording{}}
	dir := t.TempDir()
	srv := New(Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}}, Sources: fakeSources{},
		Auth: auth.New(newMemoryAuth()), Recordings: recs, RecordingsDir: dir, UploadLimit: 1 << 20,
		Ready: func() []Check { return []Check{{Name: "python", OK: true}} }})
	return srv, recs, dir
}

// call performs a request with an optional session cookie and returns the
// recorder.
func call(t *testing.T, srv http.Handler, method, path string, body io.Reader, contentType, cookie string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, "http://127.0.0.1:8765"+path, body)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if cookie != "" {
		req.AddCookie(&http.Cookie{Name: sessionCookie, Value: cookie})
	}
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	return rec
}

func sessionOf(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	for _, c := range rec.Result().Cookies() {
		if c.Name == sessionCookie {
			if !c.HttpOnly || c.SameSite != http.SameSiteStrictMode || c.Path != "/" {
				t.Fatalf("session cookie attributes are wrong: %+v", c)
			}
			return c.Value
		}
	}
	return ""
}

func register(t *testing.T, srv http.Handler, email string) string {
	t.Helper()
	body := `{"email":"` + email + `","password":"longenough","display_name":"Tester"}`
	rec := call(t, srv, "POST", "/api/auth/register", strings.NewReader(body), "application/json", "")
	if rec.Code != http.StatusCreated {
		t.Fatalf("register %s: %d %s", email, rec.Code, rec.Body.String())
	}
	token := sessionOf(t, rec)
	if token == "" {
		t.Fatal("registration must sign the user in")
	}
	return token
}

func TestApiRoutesRequireASession(t *testing.T) {
	srv, _, _ := newAccountServer(t)
	for _, path := range []string{"/api/jobs", "/api/reports", "/api/sources", "/api/recordings", "/api/reports/rep-1/summary"} {
		if rec := call(t, srv, "GET", path, nil, "", ""); rec.Code != http.StatusUnauthorized {
			t.Fatalf("%s without a session: %d", path, rec.Code)
		}
	}
	if rec := call(t, srv, "GET", "/api/auth/me", nil, "", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("me without a session: %d", rec.Code)
	}
	if rec := call(t, srv, "GET", "/readyz", nil, "", ""); rec.Code != http.StatusOK {
		t.Fatalf("readiness needs no session: %d", rec.Code)
	}
}

func TestRegisterLoginAndLogout(t *testing.T) {
	srv, _, _ := newAccountServer(t)
	token := register(t, srv, "andy@example.com")
	rec := call(t, srv, "GET", "/api/auth/me", nil, "", token)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"andy@example.com"`) {
		t.Fatalf("me: %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "POST", "/api/auth/register", strings.NewReader(`{"email":"andy@example.com","password":"longenough","display_name":"Again"}`), "application/json", ""); rec.Code != http.StatusConflict {
		t.Fatalf("duplicate email: %d", rec.Code)
	}
	if rec := call(t, srv, "POST", "/api/auth/login", strings.NewReader(`{"email":"andy@example.com","password":"wrong-one"}`), "application/json", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("wrong password: %d", rec.Code)
	}
	rec = call(t, srv, "POST", "/api/auth/login", strings.NewReader(`{"email":"Andy@Example.com","password":"longenough"}`), "application/json", "")
	if rec.Code != http.StatusOK || sessionOf(t, rec) == "" {
		t.Fatalf("login: %d %s", rec.Code, rec.Body.String())
	}
	if rec := call(t, srv, "GET", "/api/jobs", nil, "", token); rec.Code != http.StatusOK {
		t.Fatalf("jobs with a session: %d", rec.Code)
	}
	rec = call(t, srv, "POST", "/api/auth/logout", nil, "", token)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("logout: %d", rec.Code)
	}
	if rec := call(t, srv, "GET", "/api/jobs", nil, "", token); rec.Code != http.StatusUnauthorized {
		t.Fatalf("a logged-out session must be rejected: %d", rec.Code)
	}
}

func multipartFile(t *testing.T, field, name string, content []byte) (io.Reader, string) {
	t.Helper()
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	part, err := w.CreateFormFile(field, name)
	if err != nil {
		t.Fatal(err)
	}
	part.Write(content)
	w.Close()
	return &buf, w.FormDataContentType()
}

func TestUploadsAreOwnedStreamedAndSeekable(t *testing.T) {
	srv, recs, dir := newAccountServer(t)
	andy := register(t, srv, "andy@example.com")
	someone := register(t, srv, "someone@example.com")
	content := bytes.Repeat([]byte("frame"), 2000)
	body, ct := multipartFile(t, "file", "..\\evil\\my run.mp4", content)
	rec := call(t, srv, "POST", "/api/recordings", body, ct, andy)
	if rec.Code != http.StatusCreated {
		t.Fatalf("upload: %d %s", rec.Code, rec.Body.String())
	}
	var uploaded jobs.Recording
	json.Unmarshal(rec.Body.Bytes(), &uploaded)
	if uploaded.Name != "my run.mp4" || uploaded.Size != int64(len(content)) || uploaded.SHA256 == "" {
		t.Fatalf("recording record: %+v", uploaded)
	}
	stored := recs.recs[uploaded.ID]
	if filepath.Dir(filepath.Dir(stored.Path)) != dir || filepath.Ext(stored.Path) != ".mp4" {
		t.Fatalf("upload must live under the recordings directory with a server-chosen name: %s", stored.Path)
	}
	if data, err := os.ReadFile(stored.Path); err != nil || !bytes.Equal(data, content) {
		t.Fatalf("upload content mismatch: %v", err)
	}
	// Ranges: the browser seeks with partial requests.
	req := httptest.NewRequest("GET", "http://127.0.0.1:8765/api/recordings/"+uploaded.ID+"/video", nil)
	req.Header.Set("Range", "bytes=5-9")
	req.AddCookie(&http.Cookie{Name: sessionCookie, Value: andy})
	out := httptest.NewRecorder()
	srv.ServeHTTP(out, req)
	if out.Code != http.StatusPartialContent || out.Body.String() != "frame" || out.Header().Get("Content-Type") != "video/mp4" {
		t.Fatalf("range request: %d %q %s", out.Code, out.Body.String(), out.Header().Get("Content-Type"))
	}
	// Another user cannot see, play or delete it.
	if rec := call(t, srv, "GET", "/api/recordings/"+uploaded.ID+"/video", nil, "", someone); rec.Code != http.StatusNotFound {
		t.Fatalf("another user's video: %d", rec.Code)
	}
	if rec := call(t, srv, "DELETE", "/api/recordings/"+uploaded.ID, nil, "", someone); rec.Code != http.StatusNotFound {
		t.Fatalf("another user's delete: %d", rec.Code)
	}
	if rec := call(t, srv, "GET", "/api/recordings", nil, "", someone); !strings.Contains(rec.Body.String(), `"recordings":[]`) {
		t.Fatalf("another user's list: %s", rec.Body.String())
	}
	// Unsupported and oversized uploads are refused and leave nothing behind.
	body, ct = multipartFile(t, "file", "notes.txt", []byte("hello"))
	if rec := call(t, srv, "POST", "/api/recordings", body, ct, andy); rec.Code != http.StatusBadRequest {
		t.Fatalf("unsupported extension: %d", rec.Code)
	}
	body, ct = multipartFile(t, "file", "big.mp4", bytes.Repeat([]byte("x"), 2<<20))
	if rec := call(t, srv, "POST", "/api/recordings", body, ct, andy); rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized upload: %d %s", rec.Code, rec.Body.String())
	}
	entries, _ := os.ReadDir(filepath.Dir(stored.Path))
	if len(entries) != 1 {
		t.Fatalf("refused uploads must not leave files: %d entries", len(entries))
	}
	// The owner deletes it.
	if rec := call(t, srv, "DELETE", "/api/recordings/"+uploaded.ID, nil, "", andy); rec.Code != http.StatusNoContent {
		t.Fatalf("delete: %d", rec.Code)
	}
	if _, err := os.Stat(stored.Path); !os.IsNotExist(err) {
		t.Fatal("the file must be removed with the record")
	}
}

func TestOwnedJobsAndReportsAreInvisibleToOthers(t *testing.T) {
	srv, _, _ := newAccountServer(t)
	andy := register(t, srv, "andy@example.com")
	someone := register(t, srv, "someone@example.com")
	rec := call(t, srv, "POST", "/api/jobs", strings.NewReader(`{"source_id":"src-1"}`), "application/json", andy)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("submit: %d %s", rec.Code, rec.Body.String())
	}
	// The fake manager does not stamp the owner; stamp it the way the real one does.
	fj := srv.cfg.Jobs.(*fakeJobs)
	fj.mu.Lock()
	job := fj.jobs["job-1"]
	var me struct {
		User auth.User `json:"user"`
	}
	json.Unmarshal(call(t, srv, "GET", "/api/auth/me", nil, "", andy).Body.Bytes(), &me)
	job.UserID = me.User.ID
	fj.jobs["job-1"] = job
	fj.mu.Unlock()
	if rec := call(t, srv, "GET", "/api/jobs/job-1", nil, "", andy); rec.Code != http.StatusOK {
		t.Fatalf("owner get: %d", rec.Code)
	}
	if rec := call(t, srv, "GET", "/api/jobs/job-1", nil, "", someone); rec.Code != http.StatusNotFound {
		t.Fatalf("other user get: %d", rec.Code)
	}
	if rec := call(t, srv, "POST", "/api/jobs/job-1/cancel", nil, "", someone); rec.Code != http.StatusNotFound {
		t.Fatalf("other user cancel: %d", rec.Code)
	}
	// The imported fixture report has no owner and is visible to everyone.
	if rec := call(t, srv, "GET", "/api/reports/rep-1/summary", nil, "", someone); rec.Code != http.StatusOK {
		t.Fatalf("unowned report: %d %s", rec.Code, rec.Body.String())
	}
}
