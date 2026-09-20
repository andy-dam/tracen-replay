package api

import (
	"bytes"
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
)

func limitedServer(t *testing.T, cfg func(*Config)) *Server {
	t.Helper()
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	report := fixtureReport(t)
	config := Config{Jobs: fj, Reports: fakeReports{reports: map[string]jobs.Report{report.ID: report}},
		Auth: auth.New(newMemoryAuth()), Recordings: &memoryRecordings{recs: map[string]jobs.Recording{}}, RecordingsDir: t.TempDir(),
		Ready: func() []Check { return []Check{{Name: "python", OK: true}} }}
	if cfg != nil {
		cfg(&config)
	}
	return New(config)
}

func registerWith(t *testing.T, srv http.Handler, email, invite, from string) *httptest.ResponseRecorder {
	t.Helper()
	body := `{"email":"` + email + `","password":"longenough","display_name":"Tester","invite":"` + invite + `"}`
	req := httptest.NewRequest("POST", "http://127.0.0.1:8765/api/auth/register", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if from != "" {
		req.RemoteAddr = from
	}
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	return rec
}

// A hosted service hands out invites or takes no accounts at all; the
// default stays open for one machine.
func TestRegistrationModes(t *testing.T) {
	closed := limitedServer(t, func(c *Config) { c.Registration = RegistrationClosed })
	if rec := registerWith(t, closed, "a@example.com", "", ""); rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "registration_closed") {
		t.Fatalf("closed registration: %d %s", rec.Code, rec.Body.String())
	}
	invite := limitedServer(t, func(c *Config) { c.Registration = RegistrationInvite; c.InviteCodes = []string{"friends-2026"} })
	if rec := registerWith(t, invite, "a@example.com", "", ""); rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "invite_required") {
		t.Fatalf("invite registration without a code: %d %s", rec.Code, rec.Body.String())
	}
	if rec := registerWith(t, invite, "a@example.com", "friends-2025", ""); rec.Code != http.StatusForbidden {
		t.Fatalf("a wrong code must be refused: %d", rec.Code)
	}
	if rec := registerWith(t, invite, "a@example.com", " friends-2026 ", ""); rec.Code != http.StatusCreated {
		t.Fatalf("the invite code must open registration: %d %s", rec.Code, rec.Body.String())
	}
	open := limitedServer(t, nil)
	if rec := registerWith(t, open, "a@example.com", "", ""); rec.Code != http.StatusCreated {
		t.Fatalf("open registration: %d %s", rec.Code, rec.Body.String())
	}
	if ValidRegistration("sometimes") || !ValidRegistration("") || !ValidRegistration(RegistrationInvite) {
		t.Fatal("registration mode validation")
	}
}

// Five accounts an hour from one address is plenty for a household; a
// script gets a 429 on the sixth.
func TestRegistrationIsRateLimitedPerAddress(t *testing.T) {
	srv := limitedServer(t, nil)
	for i := 0; i < 5; i++ {
		email := "u" + string(rune('a'+i)) + "@example.com"
		if rec := registerWith(t, srv, email, "", "203.0.113.9:4000"); rec.Code != http.StatusCreated {
			t.Fatalf("account %d: %d %s", i+1, rec.Code, rec.Body.String())
		}
	}
	if rec := registerWith(t, srv, "sixth@example.com", "", "203.0.113.9:4001"); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("sixth account from the address must wait: %d %s", rec.Code, rec.Body.String())
	}
	// Another address is unaffected.
	if rec := registerWith(t, srv, "other@example.com", "", "198.51.100.7:4000"); rec.Code != http.StatusCreated {
		t.Fatalf("another address: %d %s", rec.Code, rec.Body.String())
	}
}

// Behind a trusted proxy the client is the last forwarded hop no proxy
// added; anything a client writes into the header itself sits behind the
// proxy's entry. Without trusted proxies the connecting address is the client.
func TestClientAddressBehindTrustedProxy(t *testing.T) {
	_, proxies, _ := net.ParseCIDR("10.0.0.0/8")
	srv := limitedServer(t, func(c *Config) { c.TrustedProxies = []*net.IPNet{proxies} })
	req := httptest.NewRequest("GET", "http://127.0.0.1:8765/healthz", nil)
	req.RemoteAddr = "10.0.0.1:5555"
	req.Header.Set("X-Forwarded-For", "203.0.113.5, 10.0.0.2")
	if got := srv.clientAddress(req); got != "203.0.113.5" {
		t.Fatalf("client behind proxies: %q", got)
	}
	req.Header.Set("X-Forwarded-For", "198.51.100.1, 203.0.113.5, 10.0.0.2")
	if got := srv.clientAddress(req); got != "203.0.113.5" {
		t.Fatalf("a client-sent hop before the real one must not count: %q", got)
	}
	req.Header.Del("X-Forwarded-For")
	if got := srv.clientAddress(req); got != "10.0.0.1" {
		t.Fatalf("no forwarded header: %q", got)
	}
	req.RemoteAddr = "203.0.113.8:5555"
	req.Header.Set("X-Forwarded-For", "1.2.3.4")
	if got := srv.clientAddress(req); got != "203.0.113.8" {
		t.Fatalf("an untrusted connection keeps its own address: %q", got)
	}
	plain := limitedServer(t, nil)
	if got := plain.clientAddress(req); got != "203.0.113.8" {
		t.Fatalf("without trusted proxies the header is ignored: %q", got)
	}
}

// Every response says what it is and refuses framing; the client's page is
// pinned to its own origin, API documents are not pages.
func TestSecurityHeaders(t *testing.T) {
	srv := limitedServer(t, nil)
	page := httptest.NewRecorder()
	srv.ServeHTTP(page, httptest.NewRequest("GET", "http://127.0.0.1:8765/", nil))
	for header, want := range map[string]string{"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"} {
		if got := page.Header().Get(header); got != want {
			t.Fatalf("%s: %q", header, got)
		}
	}
	if csp := page.Header().Get("Content-Security-Policy"); !strings.Contains(csp, "default-src 'self'") || !strings.Contains(csp, "frame-ancestors 'none'") {
		t.Fatalf("page CSP: %q", csp)
	}
	api := httptest.NewRecorder()
	srv.ServeHTTP(api, httptest.NewRequest("GET", "http://127.0.0.1:8765/api/auth/me", nil))
	if api.Header().Get("Content-Security-Policy") != "" || api.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Fatalf("API headers: %v", api.Header())
	}
}

// A server-side failure never tells the client what broke on disk.
func TestInternalErrorsAreGenericToTheClient(t *testing.T) {
	rec := httptest.NewRecorder()
	writeError(rec, http.StatusInternalServerError, "store_error", "open C:\\data\\tracen.db: locked")
	if strings.Contains(rec.Body.String(), "tracen.db") || !strings.Contains(rec.Body.String(), "internal error") {
		t.Fatalf("5xx message leaked: %s", rec.Body.String())
	}
	rec = httptest.NewRecorder()
	writeError(rec, http.StatusBadRequest, "bad_request", "send a file")
	if !strings.Contains(rec.Body.String(), "send a file") {
		t.Fatalf("4xx message must stand: %s", rec.Body.String())
	}
}

// A user keeps a bounded number of uploads and a bounded number of bytes;
// the service as a whole keeps a bounded number of bytes.
func TestUploadQuotas(t *testing.T) {
	srv := limitedServer(t, func(c *Config) {
		c.Quota = Quota{MaxRecordingsPerUser: 2, MaxBytesPerUser: 25_000, MaxBytesTotal: 40_000}
	})
	andy := register(t, srv, "andy@example.com")
	someone := register(t, srv, "someone@example.com")
	upload := func(token, name string, size int) *httptest.ResponseRecorder {
		body, ct := multipartFile(t, "file", name, bytes.Repeat([]byte("f"), size))
		return call(t, srv, "POST", "/api/recordings", body, ct, token)
	}
	if rec := upload(andy, "one.mp4", 10_000); rec.Code != http.StatusCreated {
		t.Fatalf("first upload: %d %s", rec.Code, rec.Body.String())
	}
	// Over the byte quota, refused before the body is read (Content-Length).
	if rec := upload(andy, "big.mp4", 20_000); rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "quota_exceeded") {
		t.Fatalf("byte quota: %d %s", rec.Code, rec.Body.String())
	}
	if rec := upload(andy, "two.mp4", 10_000); rec.Code != http.StatusCreated {
		t.Fatalf("second upload: %d %s", rec.Code, rec.Body.String())
	}
	// Two kept: the third is refused however small.
	if rec := upload(andy, "three.mp4", 10); rec.Code != http.StatusForbidden || !strings.Contains(rec.Body.String(), "at most 2 recordings") {
		t.Fatalf("count quota: %d %s", rec.Code, rec.Body.String())
	}
	// Another user has their own quota, but the disk guard is shared.
	if rec := upload(someone, "mine.mp4", 15_000); rec.Code != http.StatusCreated {
		t.Fatalf("other user's upload: %d %s", rec.Code, rec.Body.String())
	}
	if rec := upload(someone, "more.mp4", 8_000); rec.Code != http.StatusServiceUnavailable || !strings.Contains(rec.Body.String(), "storage_full") {
		t.Fatalf("disk guard: %d %s", rec.Code, rec.Body.String())
	}
}

// An upload is kept only when ffprobe reads it as a video within the
// limits; anything else is removed from disk before it is recorded.
func TestUploadsAreProbedBeforeTheyAreKept(t *testing.T) {
	var media artifacts.Media
	var probeErr error
	srv := limitedServer(t, func(c *Config) {
		c.Quota = Quota{MaxDuration: 3 * time.Hour, MaxPixels: 4096 * 2304, MaxFPS: 120}
		c.Probe = func(ctx context.Context, path string) (artifacts.Media, error) { return media, probeErr }
	})
	andy := register(t, srv, "andy@example.com")
	upload := func() *httptest.ResponseRecorder {
		body, ct := multipartFile(t, "file", "run.mp4", []byte("not really a video"))
		return call(t, srv, "POST", "/api/recordings", body, ct, andy)
	}
	files := func() int {
		matches, _ := filepath.Glob(filepath.Join(srv.cfg.RecordingsDir, "*", "*.mp4"))
		return len(matches)
	}
	probeErr = errors.New("ffprobe: Invalid data found when processing input")
	if rec := upload(); rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "unsupported_recording") || files() != 0 {
		t.Fatalf("unreadable file: %d %s, %d files kept", rec.Code, rec.Body.String(), files())
	}
	probeErr = nil
	for _, bad := range []artifacts.Media{
		{HasVideo: false, Duration: time.Minute},
		{HasVideo: true, Duration: 4 * time.Hour, Width: 1920, Height: 1080, FPS: 60},
		{HasVideo: true, Duration: time.Hour, Width: 7680, Height: 4320, FPS: 60},
		{HasVideo: true, Duration: time.Hour, Width: 1920, Height: 1080, FPS: 240},
	} {
		media = bad
		if rec := upload(); rec.Code != http.StatusBadRequest || files() != 0 {
			t.Fatalf("%+v: %d %s, %d files kept", bad, rec.Code, rec.Body.String(), files())
		}
	}
	media = artifacts.Media{HasVideo: true, Duration: 45 * time.Minute, Width: 1920, Height: 1080, FPS: 60}
	if rec := upload(); rec.Code != http.StatusCreated || files() != 1 {
		t.Fatalf("a career recording: %d %s, %d files", rec.Code, rec.Body.String(), files())
	}
}

// The limiter forgets attempts that fall out of its window.
func TestLimiterWindow(t *testing.T) {
	now := time.Date(2026, 9, 20, 12, 0, 0, 0, time.UTC)
	limiter := auth.NewLimiter(2, time.Hour)
	limiter.SetClock(func() time.Time { return now })
	if !limiter.Allow("k") || !limiter.Allow("k") || limiter.Allow("k") {
		t.Fatal("two attempts allowed, the third refused")
	}
	now = now.Add(61 * time.Minute)
	if !limiter.Allow("k") {
		t.Fatal("an hour later the key is free again")
	}
	limiter.Reset("k")
	if !limiter.Allow("k") || !limiter.Allow("k") {
		t.Fatal("reset clears the key")
	}
}
