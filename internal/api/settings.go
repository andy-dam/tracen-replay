package api

import (
	"encoding/json"
	"net/http"
)

// Settings are the desktop application's choices, and what the machine has
// to offer. The read-only fields are filled by the application; a request
// that carries them changes nothing.
type Settings struct {
	// GPU is the switch: analyses run on the graphics card when it is on
	// and the machine has one the analyzer's runtime supports.
	GPU bool `json:"gpu"`
	// GPUAvailable says whether the switch can be on at all: a supported
	// accelerator was found (DirectML on Windows, CUDA on Linux, CoreML on
	// Apple silicon). Read-only.
	GPUAvailable bool `json:"gpu_available"`
	// Device names the accelerator found, or says CPU only. Read-only.
	Device string `json:"device"`

	// Parallel is how many analyses run at the same time.
	Parallel int `json:"parallel"`
	// MemoryLimitGB is the memory the running analyses are planned within.
	MemoryLimitGB int `json:"memory_limit_gb"`
	// OnClose is what closing the window does: "ask", "background" (keep
	// running without a window) or "exit".
	OnClose string `json:"on_close"`
	// UpdateCheck is whether the application asks GitHub once a day for
	// the newest release. It is the only thing the application ever sends.
	UpdateCheck bool `json:"update_check"`
	// PausedLifetimeDays is how many days a paused analysis keeps its
	// progress before it is cancelled and its files deleted; zero keeps it
	// for good.
	PausedLifetimeDays int `json:"paused_lifetime_days"`

	// ParallelMax is the most analyses the memory limit and the processor
	// allow. Read-only.
	ParallelMax int `json:"parallel_max"`
	// Workers is the number of reader processes each analysis starts under
	// these settings. Read-only.
	Workers int `json:"workers"`
	// MemoryTotalGB and Cores are what the machine has; MemoryMinGB is the
	// smallest limit accepted. Read-only.
	MemoryTotalGB int `json:"memory_total_gb"`
	MemoryMinGB   int `json:"memory_min_gb"`
	Cores         int `json:"cores"`
	// Recommended is the setting suggested for this machine. Read-only.
	Recommended struct {
		Parallel      int `json:"parallel"`
		MemoryLimitGB int `json:"memory_limit_gb"`
	} `json:"recommended"`
	// Background names where the application stays when its window is
	// closed and it keeps running: "tray" or "dock". Read-only.
	Background string `json:"background"`
}

// SettingsChange is a request to change settings; a nil field is left as
// it is.
type SettingsChange struct {
	GPU                *bool   `json:"gpu"`
	Parallel           *int    `json:"parallel"`
	MemoryLimitGB      *int    `json:"memory_limit_gb"`
	OnClose            *string `json:"on_close"`
	UpdateCheck        *bool   `json:"update_check"`
	PausedLifetimeDays *int    `json:"paused_lifetime_days"`
}

// MaxPausedLifetimeDays is the longest lifetime a paused analysis can be given.
const MaxPausedLifetimeDays = 365

// SettingsStore keeps the settings and applies them. Change clamps numbers
// to what the machine allows and returns the settings as they then are.
type SettingsStore interface {
	Get() Settings
	Change(SettingsChange) (Settings, error)
}

// Desktop is what the desktop application lets its page ask of the window.
type Desktop interface {
	// Close answers the question the window asks when it is closed:
	// "exit" or "background", and whether to remember it; "cancel" keeps
	// the window as it is.
	Close(action string, remember bool) error
	// Open shows an address in the system's browser. The application's own
	// window cannot open a new tab, so a link that leaves the application
	// (a release's page) or wants a page of its own (a worker log) goes
	// through here. The desktop refuses any address that is neither.
	Open(address string) error
}

// getSettings answers GET /api/settings.
func (s *Server) getSettings(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Settings == nil {
		writeError(w, http.StatusNotFound, "no_settings", "this service has no settings to change")
		return
	}
	writeJSON(w, http.StatusOK, s.cfg.Settings.Get())
}

// putSettings answers PUT /api/settings with any of the changeable fields.
func (s *Server) putSettings(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Settings == nil {
		writeError(w, http.StatusNotFound, "no_settings", "this service has no settings to change")
		return
	}
	var change SettingsChange
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&change); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "send any of gpu, parallel, memory_limit_gb, on_close, update_check, paused_lifetime_days")
		return
	}
	current := s.cfg.Settings.Get()
	switch {
	case change.GPU != nil && *change.GPU && !current.GPUAvailable:
		writeError(w, http.StatusConflict, "gpu_unavailable", "no supported graphics card was found on this machine")
		return
	case change.Parallel != nil && *change.Parallel < 1:
		writeError(w, http.StatusBadRequest, "bad_request", "parallel must be at least 1")
		return
	case change.MemoryLimitGB != nil && *change.MemoryLimitGB < 1:
		writeError(w, http.StatusBadRequest, "bad_request", "memory_limit_gb must be at least 1")
		return
	case change.OnClose != nil && *change.OnClose != "ask" && *change.OnClose != "background" && *change.OnClose != "exit":
		writeError(w, http.StatusBadRequest, "bad_request", "on_close is ask, background or exit")
		return
	case change.PausedLifetimeDays != nil && (*change.PausedLifetimeDays < 0 || *change.PausedLifetimeDays > MaxPausedLifetimeDays):
		writeError(w, http.StatusBadRequest, "bad_request", "paused_lifetime_days is 0 (kept for good) to 365")
		return
	}
	updated, err := s.cfg.Settings.Change(change)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "settings_error", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, updated)
}

// desktopOpen answers POST /api/desktop/open: an address the desktop
// application shows in the system's browser.
func (s *Server) desktopOpen(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Desktop == nil {
		writeError(w, http.StatusNotFound, "not_desktop", "this service is not the desktop application")
		return
	}
	var body struct {
		URL string `json:"url"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil || body.URL == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "send {\"url\": \"...\"}")
		return
	}
	if err := s.cfg.Desktop.Open(body.URL); err != nil {
		writeError(w, http.StatusBadRequest, "not_opened", err.Error())
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// desktopClose answers POST /api/desktop/close: the page's answer to the
// question the window asked when it was closed.
func (s *Server) desktopClose(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Desktop == nil {
		writeError(w, http.StatusNotFound, "not_desktop", "this service is not the desktop application")
		return
	}
	var body struct {
		Action   string `json:"action"`
		Remember bool   `json:"remember"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil ||
		(body.Action != "exit" && body.Action != "background" && body.Action != "cancel") {
		writeError(w, http.StatusBadRequest, "bad_request", "send {\"action\": \"exit\"|\"background\"|\"cancel\", \"remember\": true|false}")
		return
	}
	if err := s.cfg.Desktop.Close(body.Action, body.Remember); err != nil {
		writeError(w, http.StatusInternalServerError, "desktop_error", err.Error())
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
