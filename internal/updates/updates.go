// Package updates tells an installed application that a newer release
// exists. It is for the builds people install and have to replace by hand:
// the desktop applications, and a service someone runs from a release. The
// hosted site is never told: it is redeployed from main by itself, and its
// visitors have nothing to download.
//
// Once a day it asks GitHub for the newest release: one request that carries
// nothing but the build's version in its User-Agent, and reads the tag and
// the release's page.
package updates

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"sync"
	"time"
)

// ReleasesURL is where the newest release is asked for. Pre-releases (the
// project's ffmpeg builds) are not "the latest release" to GitHub, so they
// never show up here.
const ReleasesURL = "https://api.github.com/repos/andy-dam/tracen-replay/releases/latest"

// ReleasePage is where every release page of this project begins; nothing
// else is ever offered as a download.
const ReleasePage = "https://github.com/andy-dam/tracen-replay/releases/"

// Checker knows the newest release, when asked to look.
type Checker struct {
	// Current is this build's version (the release tag it was built from).
	Current string
	Log     *slog.Logger
	// Enabled, when set, is asked before every look, so a person can turn
	// the check off and on while the application runs; off, nothing is
	// asked and nothing is offered.
	Enabled func() bool
	// URL and Client replace the real ones in tests.
	URL    string
	Client *http.Client

	mu     sync.Mutex
	latest string
	page   string
}

var release = regexp.MustCompile(`^v?(\d+)\.(\d+)\.(\d+)$`)

// parse reads a release version; anything else (a commit, "dev", a test
// build, a pre-release suffix) is not one.
func parse(version string) ([3]int, bool) {
	m := release.FindStringSubmatch(version)
	if m == nil {
		return [3]int{}, false
	}
	var out [3]int
	for i := range out {
		out[i], _ = strconv.Atoi(m[i+1])
	}
	return out, true
}

// Newer reports whether latest is a later release than current. A build
// that is not a release is never told to update: it has no place in the
// order of releases.
func Newer(current, latest string) bool {
	have, ok := parse(current)
	if !ok {
		return false
	}
	want, ok := parse(latest)
	if !ok {
		return false
	}
	for i := range have {
		if want[i] != have[i] {
			return want[i] > have[i]
		}
	}
	return false
}

// Run looks soon after the start and then once a day, until ctx ends. A
// build that is not a release never looks: the answer could not matter.
func (c *Checker) Run(ctx context.Context) {
	if _, ok := parse(c.Current); !ok {
		return
	}
	delay := 20 * time.Second
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
		if c.Enabled != nil && !c.Enabled() {
			// Turned off: look again soon, in case it is turned on.
			delay = time.Minute
			continue
		}
		c.Look(ctx)
		delay = 24 * time.Hour
	}
}

// Look asks once.
func (c *Checker) Look(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	url, client := c.URL, c.Client
	if url == "" {
		url = ReleasesURL
	}
	if client == nil {
		client = http.DefaultClient
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("User-Agent", "tracen-replay/"+c.Current)
	response, err := client.Do(request)
	if err != nil {
		if c.Log != nil {
			c.Log.Debug("update check failed", "error", err)
		}
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return
	}
	var found struct {
		Tag string `json:"tag_name"`
		URL string `json:"html_url"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(nil, response.Body, 1<<20)).Decode(&found); err != nil || found.Tag == "" {
		return
	}
	c.mu.Lock()
	c.latest, c.page = found.Tag, found.URL
	c.mu.Unlock()
}

// Available is the newer release and its page, or empty strings when this
// build is the newest, is not a release, or nothing has been learned yet.
func (c *Checker) Available() (version, page string) {
	if c == nil {
		return "", ""
	}
	if c.Enabled != nil && !c.Enabled() {
		return "", ""
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if !Newer(c.Current, c.latest) {
		return "", ""
	}
	return c.latest, c.page
}
