package api

import (
	"bufio"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// A pause ends the event stream after the paused record: a paused job says
// nothing more, and an open stream would keep a service that scales to
// nothing awake. A stream opened on a paused job sends the record and ends.
func TestJobEventsEndAtAPause(t *testing.T) {
	srv, fj := newServer(t)
	fj.jobs["job-9"] = jobs.Job{ID: "job-9", Status: jobs.Running, OutputDir: "o"}
	server := httptest.NewServer(srv)
	defer server.Close()
	open := func() *http.Response {
		req, _ := http.NewRequest("GET", server.URL+"/api/jobs/job-9/events", nil)
		req.Host = "localhost"
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}
	resp := open()
	defer resp.Body.Close()
	reader := bufio.NewReader(resp.Body)
	first, err := reader.ReadString('\n')
	if err != nil || !strings.HasPrefix(first, "event: job") {
		t.Fatalf("first line %q %v", first, err)
	}
	time.Sleep(50 * time.Millisecond)
	fj.mu.Lock()
	paused := fj.jobs["job-9"]
	paused.Status, paused.PausedAt = jobs.Paused, time.Now()
	fj.jobs["job-9"] = paused
	fj.mu.Unlock()
	fj.hub.Publish(jobs.Event{JobID: "job-9", Status: jobs.Paused, At: time.Now()})
	done := make(chan string, 1)
	go func() {
		rest, _ := io.ReadAll(reader)
		done <- string(rest)
	}()
	select {
	case rest := <-done:
		if !strings.Contains(rest, "event: progress") || !strings.Contains(rest, `"paused_at"`) {
			t.Fatalf("stream after the pause: %s", rest)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the stream stayed open after the pause")
	}

	again := open()
	defer again.Body.Close()
	all := make(chan string, 1)
	go func() {
		body, _ := io.ReadAll(again.Body)
		all <- string(body)
	}()
	select {
	case body := <-all:
		if !strings.Contains(body, `"status":"paused"`) {
			t.Fatalf("stream of a paused job: %s", body)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a stream opened on a paused job stayed open")
	}
}
