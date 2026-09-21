package api

import (
	"testing"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// fakeSettings applies a change the way the desktop application does, less
// the machine: numbers are kept as sent.
type fakeSettings struct{ now Settings }

func (f *fakeSettings) Get() Settings { return f.now }
func (f *fakeSettings) Change(c SettingsChange) (Settings, error) {
	if c.GPU != nil {
		f.now.GPU = *c.GPU
	}
	if c.Parallel != nil {
		f.now.Parallel = *c.Parallel
	}
	if c.MemoryLimitGB != nil {
		f.now.MemoryLimitGB = *c.MemoryLimitGB
	}
	if c.OnClose != nil {
		f.now.OnClose = *c.OnClose
	}
	if c.UpdateCheck != nil {
		f.now.UpdateCheck = *c.UpdateCheck
	}
	return f.now, nil
}

type fakeDesktop struct {
	action   string
	remember bool
	opened   string
}

func (f *fakeDesktop) Open(address string) error {
	f.opened = address
	return nil
}

func (f *fakeDesktop) Close(action string, remember bool) error {
	f.action, f.remember = action, remember
	return nil
}

func TestSettingsChangesAndTheCloseAnswer(t *testing.T) {
	store := &fakeSettings{now: Settings{GPUAvailable: false, Parallel: 1, MemoryLimitGB: 8, OnClose: "ask"}}
	desk := &fakeDesktop{}
	fj := &fakeJobs{jobs: map[string]jobs.Job{}, hub: jobs.NewHub()}
	srv := New(Config{Jobs: fj, Settings: store, Desktop: desk})

	if code, body := do(t, srv, "PUT", "/api/settings", `{"parallel": 2, "memory_limit_gb": 16, "on_close": "background"}`); code != 200 ||
		body["parallel"] != float64(2) || body["memory_limit_gb"] != float64(16) || body["on_close"] != "background" {
		t.Fatalf("a change of three settings: %d %v", code, body)
	}
	// One field alone leaves the rest as they are.
	if code, body := do(t, srv, "PUT", "/api/settings", `{"parallel": 1}`); code != 200 || body["memory_limit_gb"] != float64(16) || body["on_close"] != "background" {
		t.Fatalf("a change of one setting: %d %v", code, body)
	}
	for _, bad := range []string{`{"parallel": 0}`, `{"memory_limit_gb": 0}`, `{"on_close": "sleep"}`, `not json`} {
		if code, _ := do(t, srv, "PUT", "/api/settings", bad); code != 400 {
			t.Fatalf("%s was answered %d", bad, code)
		}
	}
	if code, _ := do(t, srv, "PUT", "/api/settings", `{"gpu": true}`); code != 409 {
		t.Fatalf("the switch without a graphics card was answered %d", code)
	}

	if code, _ := do(t, srv, "POST", "/api/desktop/close", `{"action": "background", "remember": true}`); code != 204 || desk.action != "background" || !desk.remember {
		t.Fatalf("the close answer: %d %+v", code, desk)
	}
	if code, _ := do(t, srv, "POST", "/api/desktop/open", `{"url": "https://github.com/andy-dam/tracen-replay/releases/tag/v0.3.0"}`); code != 204 || desk.opened == "" {
		t.Fatalf("opening a release page: %d %+v", code, desk)
	}
	if code, body := do(t, srv, "PUT", "/api/settings", `{"update_check": true}`); code != 200 || body["update_check"] != true {
		t.Fatalf("the update check switch: %d %v", code, body)
	}
	if code, _ := do(t, srv, "POST", "/api/desktop/close", `{"action": "sleep"}`); code != 400 {
		t.Fatalf("an unknown close action was answered %d", code)
	}
	// A service that is not the desktop application has no such endpoint.
	plain := New(Config{Jobs: fj})
	if code, _ := do(t, plain, "POST", "/api/desktop/close", `{"action": "exit"}`); code != 404 {
		t.Fatalf("the close answer without a desktop was answered %d", code)
	}
}
