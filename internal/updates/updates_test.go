package updates

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestNewer(t *testing.T) {
	for _, c := range []struct {
		current, latest string
		want            bool
	}{
		{"v0.2.0", "v0.2.1", true},
		{"v0.2.0", "v0.10.0", true},
		{"0.2.0", "v1.0.0", true},
		{"v0.2.0", "v0.2.0", false},
		{"v0.3.0", "v0.2.9", false},
		// A build that is not a release is never told to update: the hosted
		// site runs a commit, a developer runs "dev", a tester a test build.
		{"5a8231c4bccdd9d68b4d3186ec6cb75abc399181", "v0.2.0", false},
		{"dev", "v0.2.0", false},
		{"test-feb47be2", "v0.2.0", false},
		{"v0.2.0", "ffmpeg-slim-9.0.1", false},
		{"v0.2.0", "", false},
	} {
		if got := Newer(c.current, c.latest); got != c.want {
			t.Errorf("Newer(%q, %q) = %v", c.current, c.latest, got)
		}
	}
}

func TestCheckerAsksOnceAndOffersOnlyANewerRelease(t *testing.T) {
	asked := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked++
		if agent := r.Header.Get("User-Agent"); agent != "tracen-replay/v0.2.0" && agent != "tracen-replay/v0.3.0" {
			t.Errorf("the request names %q", agent)
		}
		w.Write([]byte(`{"tag_name":"v0.3.0","html_url":"https://github.com/andy-dam/tracen-replay/releases/tag/v0.3.0"}`))
	}))
	defer server.Close()

	installed := &Checker{Current: "v0.2.0", URL: server.URL}
	if v, _ := installed.Available(); v != "" {
		t.Fatal("nothing is known before the first look")
	}
	installed.Look(context.Background())
	if v, page := installed.Available(); v != "v0.3.0" || page == "" || asked != 1 {
		t.Fatalf("offered %q %q after %d requests", v, page, asked)
	}

	current := &Checker{Current: "v0.3.0", URL: server.URL}
	current.Look(context.Background())
	if v, _ := current.Available(); v != "" {
		t.Fatalf("the newest build was offered %q", v)
	}

	// A commit build never even asks.
	hosted := &Checker{Current: "5a8231c4bccdd9d68b4d3186ec6cb75abc399181", URL: server.URL}
	before := asked
	hosted.Run(context.Background())
	if v, _ := hosted.Available(); v != "" || asked != before {
		t.Fatalf("a commit build asked %d times and was offered %q", asked-before, v)
	}
	var none *Checker
	if v, _ := none.Available(); v != "" {
		t.Fatal("no checker offers nothing")
	}
}
