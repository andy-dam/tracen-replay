// Package api is the HTTP surface of the application. Handlers only
// translate requests into calls on the job manager, the store, the auth
// service and the timeline documents; no recognition logic and no path from
// a client ever reaches the file system. Every /api route except the auth
// routes needs a signed-in user, and a user sees only their own uploads,
// jobs and reports plus the reports registered from the command line.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/timeline"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Jobs is what the handlers need from the job manager.
type Jobs interface {
	Submit(ctx context.Context, userID, sourceID string) (jobs.Job, error)
	Cancel(ctx context.Context, id string) (jobs.Job, error)
	Get(ctx context.Context, id string) (jobs.Job, error)
	List(ctx context.Context, userID string) ([]jobs.Job, error)
	Hub() *jobs.Hub
}

// Reports is what the handlers need from the store.
type Reports interface {
	ListReportsForUser(ctx context.Context, userID string) ([]jobs.Report, error)
	GetReport(ctx context.Context, id string) (jobs.Report, error)
	DeleteReport(ctx context.Context, id string) error
}

// Recordings is the store of uploaded videos.
type Recordings interface {
	CreateRecording(ctx context.Context, r jobs.Recording) error
	GetRecording(ctx context.Context, id string) (jobs.Recording, error)
	ListRecordingsForUser(ctx context.Context, userID string) ([]jobs.Recording, error)
	DeleteRecording(ctx context.Context, id string) error
	RecordingInUse(ctx context.Context, id string) (bool, error)
}

// Auth is what the handlers need from the account service.
type Auth interface {
	Register(ctx context.Context, email, password, displayName string) (auth.User, error)
	Login(ctx context.Context, email, password, address string) (auth.User, string, error)
	Authenticate(ctx context.Context, token string) (auth.User, error)
	Logout(ctx context.Context, token string) error
	TTL() time.Duration
}

// Check is one readiness probe.
type Check struct {
	Name string `json:"name"`
	OK   bool   `json:"ok"`
	Note string `json:"note,omitempty"`
}

// Config wires the handlers.
type Config struct {
	Jobs       Jobs
	Reports    Reports
	Recordings Recordings
	// Corrections stores what viewers fill in per turn; nil disables the feature.
	Corrections Corrections
	Frames      artifacts.Frames
	// Auth gates every /api route; nil (tests) serves an anonymous user.
	Auth Auth
	// RecordingsDir receives uploads, one subdirectory per user.
	RecordingsDir string
	// ArtifactsDir holds the analyses' run directories. A deleted report's
	// evidence is removed from disk only when it lies under it; an imported
	// report's files stay where they were found.
	ArtifactsDir string
	// UploadLimit bounds one upload in bytes; zero means 16 GiB.
	UploadLimit int64
	// Ready runs the readiness probes; nil means always ready.
	Ready func() []Check
	// Analyzer reports the installed analyzer's identity, so a report can say
	// whether the analyzer that made it is still the one on disk. nil leaves
	// the comparison out rather than guessing at it.
	Analyzer func(context.Context) (worker.WorkerVersion, error)
	// AllowedHosts lists the Host header values this service answers for;
	// empty allows loopback names only.
	AllowedHosts []string
	// AllowedOrigins lists client origins served from elsewhere (scheme,
	// host and port, such as https://app.example.com or
	// http://localhost:5173) that may call the API with credentials. Empty
	// means the client is served by this service and no cross-origin call
	// is accepted.
	AllowedOrigins []string
	// CookieSameSite is the session cookie's SameSite attribute; zero means
	// Strict. Lax suits a client on another port or subdomain of the same
	// site; None (which needs HTTPS) a client on another site.
	CookieSameSite http.SameSite
	// Static serves the browser client at "/"; nil serves a placeholder.
	Static http.Handler
	Logger *slog.Logger
}

// Server is the HTTP handler.
type Server struct {
	cfg  Config
	mux  *http.ServeMux
	log  *slog.Logger
	docs *docCache
}

const sessionCookie = "tracen_session"

type contextKey int

const userKey contextKey = 1

// New builds the handler.
func New(cfg Config) *Server {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	if cfg.UploadLimit <= 0 {
		cfg.UploadLimit = 16 << 30
	}
	s := &Server{cfg: cfg, mux: http.NewServeMux(), log: cfg.Logger, docs: newDocCache(8)}
	s.routes()
	return s
}

func (s *Server) routes() {
	m := s.mux
	m.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	m.HandleFunc("GET /readyz", s.ready)
	m.HandleFunc("POST /api/auth/register", s.register)
	m.HandleFunc("POST /api/auth/login", s.login)
	m.HandleFunc("POST /api/auth/logout", s.logout)
	m.HandleFunc("GET /api/auth/me", s.me)
	m.HandleFunc("GET /api/recordings", s.listRecordings)
	m.HandleFunc("POST /api/recordings", s.uploadRecording)
	m.HandleFunc("DELETE /api/recordings/{id}", s.deleteRecording)
	m.HandleFunc("GET /api/recordings/{id}/video", s.recordingVideo)
	m.HandleFunc("GET /api/recordings/{id}/frame", s.recordingFrame)
	m.HandleFunc("GET /api/jobs", s.listJobs)
	m.HandleFunc("POST /api/jobs", s.submitJob)
	m.HandleFunc("GET /api/jobs/{id}", s.getJob)
	m.HandleFunc("POST /api/jobs/{id}/cancel", s.cancelJob)
	m.HandleFunc("GET /api/jobs/{id}/events", s.jobEvents)
	m.HandleFunc("GET /api/jobs/{id}/log", s.jobLog)
	m.HandleFunc("GET /api/reports", s.listReports)
	m.HandleFunc("DELETE /api/reports/{id}", s.deleteReport)
	m.HandleFunc("GET /api/reports/{id}/summary", s.reportSummary)
	m.HandleFunc("GET /api/reports/{id}/turns", s.reportTurns)
	m.HandleFunc("GET /api/reports/{id}/turns/{turn}", s.reportTurn)
	m.HandleFunc("GET /api/reports/{id}/turns/{turn}/correction", s.getCorrection)
	m.HandleFunc("PUT /api/reports/{id}/turns/{turn}/correction", s.putCorrection)
	m.HandleFunc("DELETE /api/reports/{id}/turns/{turn}/correction", s.deleteCorrection)
	m.HandleFunc("GET /api/reports/{id}/corrections", s.listCorrections)
	m.HandleFunc("GET /api/reports/{id}/unassigned", s.reportUnassigned)
	m.HandleFunc("GET /api/reports/{id}/entries", s.reportEntries)
	m.HandleFunc("GET /api/reports/{id}/download", s.reportDownload)
	m.HandleFunc("GET /api/reports/{id}/frame", s.reportFrame)
	m.HandleFunc("GET /api/reports/{id}/video", s.reportVideo)
	if s.cfg.Static != nil {
		m.Handle("/", s.cfg.Static)
	} else {
		m.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/" {
				writeError(w, http.StatusNotFound, "not_found", "no such page")
				return
			}
			writeJSON(w, http.StatusOK, map[string]any{"service": "tracen-replay", "api": "/api", "health": "/healthz", "ready": "/readyz"})
		})
	}
}

// ServeHTTP applies the host, origin and session checks, then routes. A
// client served from one of the allowed origins gets CORS headers and its
// preflight answered; any other cross-origin request that could change
// state is refused.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !s.hostAllowed(r.Host) {
		writeError(w, http.StatusMisdirectedRequest, "bad_host", "this service only answers for its configured host")
		return
	}
	origin := r.Header.Get("Origin")
	if origin != "" && s.crossOriginAllowed(origin) {
		h := w.Header()
		h.Set("Access-Control-Allow-Origin", origin)
		h.Set("Access-Control-Allow-Credentials", "true")
		h.Add("Vary", "Origin")
		if r.Method == http.MethodOptions {
			h.Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
			h.Set("Access-Control-Allow-Headers", "Content-Type")
			h.Set("Access-Control-Max-Age", "600")
			w.WriteHeader(http.StatusNoContent)
			return
		}
	} else if r.Method != http.MethodGet && r.Method != http.MethodHead {
		if origin != "" && !s.originAllowed(origin) {
			writeError(w, http.StatusForbidden, "bad_origin", "cross-origin requests are not accepted")
			return
		}
	}
	if strings.HasPrefix(r.URL.Path, "/api/") && !strings.HasPrefix(r.URL.Path, "/api/auth/") {
		user, err := s.currentUser(r)
		if err != nil {
			writeError(w, http.StatusUnauthorized, "unauthenticated", "sign in to continue")
			return
		}
		r = r.WithContext(context.WithValue(r.Context(), userKey, user))
	}
	s.mux.ServeHTTP(w, r)
}

// currentUser resolves the session cookie; without an auth service every
// request is the anonymous user.
func (s *Server) currentUser(r *http.Request) (auth.User, error) {
	if s.cfg.Auth == nil {
		return auth.User{}, nil
	}
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		return auth.User{}, auth.ErrUnauthenticated
	}
	return s.cfg.Auth.Authenticate(r.Context(), cookie.Value)
}

func userFrom(r *http.Request) auth.User {
	user, _ := r.Context().Value(userKey).(auth.User)
	return user
}

// owns reports whether a user may see a record: their own, or one without
// an owner (registered from the command line).
func owns(user auth.User, ownerID string) bool {
	return ownerID == "" || ownerID == user.ID
}

func (s *Server) hostAllowed(host string) bool {
	name := host
	if h, _, err := net.SplitHostPort(host); err == nil {
		name = h
	}
	name = strings.Trim(strings.ToLower(name), "[]")
	if len(s.cfg.AllowedHosts) == 0 {
		return name == "localhost" || name == "127.0.0.1" || name == "::1"
	}
	for _, allowed := range s.cfg.AllowedHosts {
		if strings.EqualFold(allowed, name) {
			return true
		}
	}
	return false
}

func (s *Server) originAllowed(origin string) bool {
	trimmed := strings.TrimPrefix(strings.TrimPrefix(strings.ToLower(origin), "http://"), "https://")
	return s.hostAllowed(trimmed)
}

// crossOriginAllowed reports whether a client served from another origin
// may call this API with credentials.
func (s *Server) crossOriginAllowed(origin string) bool {
	for _, allowed := range s.cfg.AllowedOrigins {
		if strings.EqualFold(strings.TrimRight(allowed, "/"), strings.TrimRight(origin, "/")) {
			return true
		}
	}
	return false
}

// sameSite is the session cookie's SameSite attribute; None only makes
// sense over HTTPS, where the cookie is also Secure.
func (s *Server) sameSite() http.SameSite {
	if s.cfg.CookieSameSite == 0 {
		return http.SameSiteStrictMode
	}
	return s.cfg.CookieSameSite
}

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	var checks []Check
	if s.cfg.Ready != nil {
		checks = s.cfg.Ready()
	}
	status := http.StatusOK
	for _, c := range checks {
		if !c.OK {
			status = http.StatusServiceUnavailable
		}
	}
	writeJSON(w, status, map[string]any{"ready": status == http.StatusOK, "checks": checks})
}

// ---- accounts ----

func (s *Server) setSession(w http.ResponseWriter, r *http.Request, token string) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: token, Path: "/", HttpOnly: true, Secure: r.TLS != nil || s.sameSite() == http.SameSiteNoneMode,
		SameSite: s.sameSite(), MaxAge: int(s.cfg.Auth.TTL() / time.Second)})
}

func (s *Server) clearSession(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: "", Path: "/", HttpOnly: true, Secure: r.TLS != nil || s.sameSite() == http.SameSiteNoneMode,
		SameSite: s.sameSite(), MaxAge: -1})
}

func authError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, auth.ErrInvalidEmail), errors.Is(err, auth.ErrWeakPassword), errors.Is(err, auth.ErrInvalidName):
		writeError(w, http.StatusBadRequest, "invalid_input", err.Error())
	case errors.Is(err, auth.ErrEmailTaken):
		writeError(w, http.StatusConflict, "email_taken", err.Error())
	case errors.Is(err, auth.ErrBadCredentials):
		writeError(w, http.StatusUnauthorized, "bad_credentials", err.Error())
	case errors.Is(err, auth.ErrTooManyAttempts):
		writeError(w, http.StatusTooManyRequests, "too_many_attempts", err.Error())
	case errors.Is(err, auth.ErrUnauthenticated):
		writeError(w, http.StatusUnauthorized, "unauthenticated", err.Error())
	default:
		writeError(w, http.StatusInternalServerError, "auth_error", err.Error())
	}
}

func (s *Server) noAuth(w http.ResponseWriter) bool {
	if s.cfg.Auth == nil {
		writeError(w, http.StatusNotFound, "accounts_disabled", "this service runs without accounts")
		return true
	}
	return false
}

func (s *Server) register(w http.ResponseWriter, r *http.Request) {
	if s.noAuth(w) {
		return
	}
	var body struct {
		Email       string `json:"email"`
		Password    string `json:"password"`
		DisplayName string `json:"display_name"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "a JSON body with email, password and display_name is required")
		return
	}
	user, err := s.cfg.Auth.Register(r.Context(), body.Email, body.Password, body.DisplayName)
	if err != nil {
		authError(w, err)
		return
	}
	_, token, err := s.cfg.Auth.Login(r.Context(), user.Email, body.Password, clientAddress(r))
	if err != nil {
		authError(w, err)
		return
	}
	s.setSession(w, r, token)
	writeJSON(w, http.StatusCreated, map[string]any{"user": user})
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if s.noAuth(w) {
		return
	}
	var body struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "a JSON body with email and password is required")
		return
	}
	user, token, err := s.cfg.Auth.Login(r.Context(), body.Email, body.Password, clientAddress(r))
	if err != nil {
		authError(w, err)
		return
	}
	s.setSession(w, r, token)
	writeJSON(w, http.StatusOK, map[string]any{"user": user})
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	if s.noAuth(w) {
		return
	}
	if cookie, err := r.Cookie(sessionCookie); err == nil {
		s.cfg.Auth.Logout(r.Context(), cookie.Value)
	}
	s.clearSession(w, r)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	if s.noAuth(w) {
		return
	}
	user, err := s.currentUser(r)
	if err != nil {
		authError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"user": user})
}

func clientAddress(r *http.Request) string {
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return r.RemoteAddr
}

// ---- uploads ----

func (s *Server) noRecordings(w http.ResponseWriter) bool {
	if s.cfg.Recordings == nil || s.cfg.RecordingsDir == "" {
		writeError(w, http.StatusNotFound, "uploads_disabled", "this service does not accept uploads")
		return true
	}
	return false
}

func (s *Server) listRecordings(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	list, err := s.cfg.Recordings.ListRecordingsForUser(r.Context(), userFrom(r).ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	if list == nil {
		list = []jobs.Recording{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"recordings": list})
}

func (s *Server) ownRecording(w http.ResponseWriter, r *http.Request) (jobs.Recording, bool) {
	recording, err := s.cfg.Recordings.GetRecording(r.Context(), r.PathValue("id"))
	if err != nil || recording.UserID != userFrom(r).ID {
		writeError(w, http.StatusNotFound, "not_found", "no such recording")
		return jobs.Recording{}, false
	}
	return recording, true
}

func (s *Server) deleteRecording(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	recording, ok := s.ownRecording(w, r)
	if !ok {
		return
	}
	inUse, err := s.cfg.Recordings.RecordingInUse(r.Context(), recording.ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	if inUse {
		writeError(w, http.StatusConflict, "recording_in_use", "an analysis of this recording is queued or running; cancel it first")
		return
	}
	if err := s.cfg.Recordings.DeleteRecording(r.Context(), recording.ID); err != nil {
		writeNotFoundOr(w, err)
		return
	}
	if path, err := artifacts.Confined(s.cfg.RecordingsDir, recording.Path); err == nil {
		os.Remove(path)
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) recordingVideo(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	recording, ok := s.ownRecording(w, r)
	if !ok {
		return
	}
	path, err := artifacts.Confined(s.cfg.RecordingsDir, recording.Path)
	if err != nil {
		writeError(w, http.StatusNotFound, "recording_unavailable", "the upload is no longer on disk")
		return
	}
	serveVideo(w, r, path)
}

// serveVideo streams a recording with range support so the browser can
// seek; the analyzer's timestamps are the video's own.
// recordingFrame extracts one frame of an upload at a source timestamp, for
// the thumbnail of a recording that has no report yet.
func (s *Server) recordingFrame(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	recording, ok := s.ownRecording(w, r)
	if !ok {
		return
	}
	ms, err := strconv.ParseInt(r.URL.Query().Get("ms"), 10, 64)
	if err != nil || ms < 0 {
		writeError(w, http.StatusBadRequest, "bad_timestamp", "ms must be a non-negative integer millisecond timestamp")
		return
	}
	path, err := artifacts.Confined(s.cfg.RecordingsDir, recording.Path)
	if err != nil {
		writeError(w, http.StatusNotFound, "recording_unavailable", "the upload is no longer on disk")
		return
	}
	frame, err := s.cfg.Frames.At(r.Context(), "recording-"+recording.ID, path, ms)
	if err != nil {
		writeError(w, http.StatusNotFound, "frame_unavailable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "private, max-age=86400")
	http.ServeFile(w, r, frame)
}

func serveVideo(w http.ResponseWriter, r *http.Request, path string) {
	if _, err := os.Stat(path); err != nil {
		writeError(w, http.StatusNotFound, "recording_unavailable", "the recording is no longer at its recorded path")
		return
	}
	if ct := videoType(path); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	w.Header().Set("Cache-Control", "private, max-age=3600")
	http.ServeFile(w, r, path)
}

func videoType(path string) string {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".mp4", ".m4v":
		return "video/mp4"
	case ".webm":
		return "video/webm"
	case ".mov":
		return "video/quicktime"
	case ".mkv":
		return "video/x-matroska"
	}
	return ""
}

// ---- jobs ----

func (s *Server) listJobs(w http.ResponseWriter, r *http.Request) {
	list, err := s.cfg.Jobs.List(r.Context(), userFrom(r).ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	if list == nil {
		list = []jobs.Job{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"jobs": list})
}

func (s *Server) submitJob(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SourceID string `json:"source_id"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil || body.SourceID == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "a JSON body with source_id is required")
		return
	}
	job, err := s.cfg.Jobs.Submit(r.Context(), userFrom(r).ID, body.SourceID)
	if err != nil {
		var full *jobs.QueueFullError
		var nf *jobs.NotFoundError
		switch {
		case errors.As(err, &full):
			writeError(w, http.StatusTooManyRequests, "queue_full", err.Error())
		case errors.As(err, &nf):
			writeError(w, http.StatusNotFound, "unknown_source", err.Error())
		default:
			writeError(w, http.StatusInternalServerError, "submit_failed", err.Error())
		}
		return
	}
	writeJSON(w, http.StatusAccepted, job)
}

// ownJob loads a job the user may see.
func (s *Server) ownJob(w http.ResponseWriter, r *http.Request) (jobs.Job, bool) {
	job, err := s.cfg.Jobs.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeNotFoundOr(w, err)
		return jobs.Job{}, false
	}
	if !owns(userFrom(r), job.UserID) {
		writeError(w, http.StatusNotFound, "not_found", "job "+job.ID+" not found")
		return jobs.Job{}, false
	}
	return job, true
}

func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	job, ok := s.ownJob(w, r)
	if !ok {
		return
	}
	writeJSON(w, http.StatusOK, job)
}

func (s *Server) cancelJob(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.ownJob(w, r); !ok {
		return
	}
	job, err := s.cfg.Jobs.Cancel(r.Context(), r.PathValue("id"))
	if err != nil {
		var te *jobs.TransitionError
		if errors.As(err, &te) {
			writeJSON(w, http.StatusConflict, map[string]any{"error": map[string]string{"code": "not_cancellable", "message": err.Error()}, "job": job})
			return
		}
		writeNotFoundOr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, job)
}

// jobEvents streams the job's state as server-sent events: the current
// record first, then every change until the job is terminal.
func (s *Server) jobEvents(w http.ResponseWriter, r *http.Request) {
	job, ok := s.ownJob(w, r)
	if !ok {
		return
	}
	id := job.ID
	events, unsubscribe := s.cfg.Jobs.Hub().Subscribe(id)
	defer unsubscribe()
	job, err := s.cfg.Jobs.Get(r.Context(), id)
	if err != nil {
		writeNotFoundOr(w, err)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "no_streaming", "streaming is not supported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	send := func(name string, v any) {
		data, _ := json.Marshal(v)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", name, data)
		flusher.Flush()
	}
	send("job", job)
	if job.Status.Terminal() {
		return
	}
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-keepalive.C:
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
		case e, ok := <-events:
			if !ok {
				return
			}
			send("progress", e)
			if e.Terminal {
				if final, err := s.cfg.Jobs.Get(r.Context(), id); err == nil {
					send("job", final)
				}
				return
			}
		}
	}
}

func (s *Server) jobLog(w http.ResponseWriter, r *http.Request) {
	job, ok := s.ownJob(w, r)
	if !ok {
		return
	}
	if job.LogPath == "" {
		writeError(w, http.StatusNotFound, "no_log", "this job has no worker log")
		return
	}
	text, err := artifacts.LogTail(job.LogPath, 64*1024)
	if err != nil {
		writeError(w, http.StatusNotFound, "no_log", "the worker log is not readable")
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprint(w, text)
}

// ---- reports ----

func (s *Server) listReports(w http.ResponseWriter, r *http.Request) {
	list, err := s.cfg.Reports.ListReportsForUser(r.Context(), userFrom(r).ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	if list == nil {
		list = []jobs.Report{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"reports": list})
}

// ownReport loads a report the user may see.
// deleteReport removes a report the caller owns, with its corrections, its
// cached frames and, for a report the service produced, its run directory.
func (s *Server) deleteReport(w http.ResponseWriter, r *http.Request) {
	report, ok := s.ownReport(w, r)
	if !ok {
		return
	}
	if report.UserID == "" {
		writeError(w, http.StatusForbidden, "report_unowned", "this report has no owner and cannot be deleted here")
		return
	}
	if err := s.cfg.Reports.DeleteReport(r.Context(), report.ID); err != nil {
		writeNotFoundOr(w, err)
		return
	}
	s.docs.forget(report.ID)
	if s.cfg.ArtifactsDir != "" && report.Origin == "job" {
		if dir, err := artifacts.ConfinedDir(s.cfg.ArtifactsDir, report.EvidenceRoot); err == nil {
			os.RemoveAll(dir)
		}
	}
	s.cfg.Frames.Forget(report.ID)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) ownReport(w http.ResponseWriter, r *http.Request) (jobs.Report, bool) {
	report, err := s.cfg.Reports.GetReport(r.Context(), r.PathValue("id"))
	if err != nil {
		writeNotFoundOr(w, err)
		return jobs.Report{}, false
	}
	if !owns(userFrom(r), report.UserID) {
		writeError(w, http.StatusNotFound, "not_found", "report "+report.ID+" not found")
		return jobs.Report{}, false
	}
	return report, true
}

func (s *Server) report(w http.ResponseWriter, r *http.Request) (jobs.Report, *timeline.Document, bool) {
	report, ok := s.ownReport(w, r)
	if !ok {
		return jobs.Report{}, nil, false
	}
	doc, err := s.docs.get(report)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "timeline_unreadable", err.Error())
		return jobs.Report{}, nil, false
	}
	return report, doc, true
}

func (s *Server) reportSummary(w http.ResponseWriter, r *http.Request) {
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	hasVideo := report.SourcePath != ""
	if hasVideo {
		_, err := os.Stat(report.SourcePath)
		hasVideo = err == nil
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"report": report, "source": doc.Source, "recognition": doc.Recognition, "summary": doc.Summary,
		"turns": len(doc.Turns), "entries": len(doc.Entries), "unassigned": len(doc.Unassigned()),
		"schema_version": doc.SchemaVersion, "note": doc.Note, "video_available": hasVideo,
		"analyzer": s.analyzerLedger(r, report),
	})
}

// analyzerLedger says which analyzer made this report and whether that is
// still the analyzer on disk. Both halves are optional and each is reported
// only when it is known: a report registered from a bundle never had a job to
// record an identity, and an analyzer that cannot be reached must not make a
// report look current or stale. "stale" is stated only when both are in hand.
func (s *Server) analyzerLedger(r *http.Request, report jobs.Report) map[string]any {
	ledger := map[string]any{}
	var made *worker.WorkerVersion
	if report.JobID != "" {
		if job, err := s.cfg.Jobs.Get(r.Context(), report.JobID); err == nil && job.Result != nil {
			made = job.Result.WorkerVersion
		}
	}
	if made != nil {
		ledger["report"] = made
	}
	if s.cfg.Analyzer != nil {
		if installed, err := s.cfg.Analyzer(r.Context()); err == nil {
			ledger["installed"] = installed
			if made != nil {
				ledger["stale"] = made.CodeDigest != installed.CodeDigest
			}
		}
	}
	if len(ledger) == 0 {
		return nil
	}
	return ledger
}

func (s *Server) reportTurns(w http.ResponseWriter, r *http.Request) {
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	summaries := doc.TurnSummaries()
	// The viewer's own answers show in the strip and the counts, per viewer.
	if s.cfg.Corrections != nil {
		if list, err := s.cfg.Corrections.ListCorrections(r.Context(), report.ID, userFrom(r).ID); err == nil {
			timeline.ApplyCorrections(summaries, list)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"turns": summaries})
}

func (s *Server) reportTurn(w http.ResponseWriter, r *http.Request) {
	report, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	turn, found := doc.Turn(r.PathValue("turn"))
	if !found {
		writeError(w, http.StatusNotFound, "unknown_turn", "no such turn in this report")
		return
	}
	entries := doc.TurnEntries(turn.ID)
	if entries == nil {
		entries = []timeline.Entry{}
	}
	response := map[string]any{"turn": turn, "entries": entries}
	if s.cfg.Corrections != nil {
		// The viewer's fill-in rides along with the turn, checked afresh.
		if c, exists, err := s.cfg.Corrections.GetCorrection(r.Context(), report.ID, turn.ID, userFrom(r).ID); err == nil && exists {
			response["correction"] = c
			response["verification"] = timeline.Verify(turn, doc.TurnEntries(turn.ID), c)
		}
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) reportUnassigned(w http.ResponseWriter, r *http.Request) {
	_, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	entries := doc.Unassigned()
	if entries == nil {
		entries = []timeline.Entry{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

func (s *Server) reportDownload(w http.ResponseWriter, r *http.Request) {
	report, ok := s.ownReport(w, r)
	if !ok {
		return
	}
	path, err := artifacts.Confined(report.EvidenceRoot, report.ReportPath)
	if err != nil {
		writeError(w, http.StatusNotFound, "report_unavailable", "the report file is not available: "+err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition", `attachment; filename="report-`+report.ID+`.json"`)
	http.ServeFile(w, r, path)
}

// reportEntries returns every ledger entry of the report in document order,
// each with its turn id, so the client can index warnings across the run.
func (s *Server) reportEntries(w http.ResponseWriter, r *http.Request) {
	_, doc, ok := s.report(w, r)
	if !ok {
		return
	}
	entries := doc.Entries
	if entries == nil {
		entries = []timeline.Entry{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

func (s *Server) reportFrame(w http.ResponseWriter, r *http.Request) {
	report, ok := s.ownReport(w, r)
	if !ok {
		return
	}
	ms, err := strconv.ParseInt(r.URL.Query().Get("ms"), 10, 64)
	if err != nil || ms < 0 {
		writeError(w, http.StatusBadRequest, "bad_timestamp", "ms must be a non-negative integer millisecond timestamp")
		return
	}
	if report.SourcePath == "" {
		writeError(w, http.StatusNotFound, "recording_unavailable", "this report has no recording on this machine")
		return
	}
	if _, err := os.Stat(report.SourcePath); err != nil {
		writeError(w, http.StatusNotFound, "recording_unavailable", "the recording is no longer at its recorded path")
		return
	}
	path, err := s.cfg.Frames.At(r.Context(), report.ID, report.SourcePath, ms)
	if err != nil {
		writeError(w, http.StatusNotFound, "frame_unavailable", err.Error())
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "private, max-age=86400")
	http.ServeFile(w, r, path)
}

// reportVideo streams the recording behind a report (an upload or the file a
// command-line import named) so the viewer can seek to any entry.
func (s *Server) reportVideo(w http.ResponseWriter, r *http.Request) {
	report, ok := s.ownReport(w, r)
	if !ok {
		return
	}
	if report.SourcePath == "" {
		writeError(w, http.StatusNotFound, "recording_unavailable", "this report has no recording on this machine")
		return
	}
	serveVideo(w, r, report.SourcePath)
}

// docCache keeps recently used timeline documents in memory.
type docCache struct {
	mu    sync.Mutex
	limit int
	order []string
	docs  map[string]*timeline.Document
	hash  map[string]string
}

func newDocCache(limit int) *docCache {
	return &docCache{limit: limit, docs: map[string]*timeline.Document{}, hash: map[string]string{}}
}

// forget drops a report's document so a later report under the same id
// is read afresh.
func (c *docCache) forget(id string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.docs, id)
	delete(c.hash, id)
	for i, k := range c.order {
		if k == id {
			c.order = append(c.order[:i], c.order[i+1:]...)
			break
		}
	}
}

func (c *docCache) get(report jobs.Report) (*timeline.Document, error) {
	c.mu.Lock()
	if doc, ok := c.docs[report.ID]; ok && c.hash[report.ID] == report.ReportSHA256 {
		c.mu.Unlock()
		return doc, nil
	}
	c.mu.Unlock()
	path, err := artifacts.Confined(report.EvidenceRoot, report.TimelinePath)
	if err != nil {
		return nil, err
	}
	doc, err := timeline.Load(path)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.docs[report.ID]; !ok {
		c.order = append(c.order, report.ID)
		if len(c.order) > c.limit {
			oldest := c.order[0]
			c.order = c.order[1:]
			delete(c.docs, oldest)
			delete(c.hash, oldest)
		}
	}
	c.docs[report.ID], c.hash[report.ID] = doc, report.ReportSHA256
	return doc, nil
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	enc.Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}

func writeNotFoundOr(w http.ResponseWriter, err error) {
	var nf *jobs.NotFoundError
	if errors.As(err, &nf) {
		writeError(w, http.StatusNotFound, "not_found", err.Error())
		return
	}
	writeError(w, http.StatusInternalServerError, "store_error", err.Error())
}
