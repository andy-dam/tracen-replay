package api

import (
	"encoding/json"
	"net/http"
)

// Settings are the desktop application's choices: whether analyses use
// the graphics card, and what the machine has to offer.
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
}

// SettingsStore keeps the settings and applies them.
type SettingsStore interface {
	Get() Settings
	Set(Settings) error
}

// getSettings answers GET /api/settings.
func (s *Server) getSettings(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Settings == nil {
		writeError(w, http.StatusNotFound, "no_settings", "this service has no settings to change")
		return
	}
	writeJSON(w, http.StatusOK, s.cfg.Settings.Get())
}

// putSettings answers PUT /api/settings with the switch; the read-only
// fields are what the machine has, not what the request says.
func (s *Server) putSettings(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Settings == nil {
		writeError(w, http.StatusNotFound, "no_settings", "this service has no settings to change")
		return
	}
	var body struct {
		GPU bool `json:"gpu"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "send {\"gpu\": true|false}")
		return
	}
	current := s.cfg.Settings.Get()
	if body.GPU && !current.GPUAvailable {
		writeError(w, http.StatusConflict, "gpu_unavailable", "no supported graphics card was found on this machine")
		return
	}
	current.GPU = body.GPU
	if err := s.cfg.Settings.Set(current); err != nil {
		writeError(w, http.StatusInternalServerError, "settings_error", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, s.cfg.Settings.Get())
}
