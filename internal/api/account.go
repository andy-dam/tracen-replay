package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// deleteAccount answers POST /api/auth/delete with {"password": "..."}: the
// account goes, with everything kept under it. The files go first (the
// recordings and their playback copies, every report's run, every job's
// log), then the records, so nothing is left that no record names. An
// analysis that is queued or running has to be cancelled first: its worker
// may be another machine, and would write into an account that is gone. A
// paused one is cancelled here, which deletes its working files.
func (s *Server) deleteAccount(w http.ResponseWriter, r *http.Request) {
	if s.noAuth(w) {
		return
	}
	// The routes under /api/auth/ resolve their own session.
	user, err := s.currentUser(r)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "unauthenticated", "sign in to continue")
		return
	}
	var body struct {
		Password string `json:"password"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil || body.Password == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "send {\"password\": \"...\"}")
		return
	}
	if err := s.cfg.Auth.CheckPassword(r.Context(), user.ID, body.Password); err != nil {
		writeError(w, http.StatusForbidden, "bad_credentials", "The password is incorrect.")
		return
	}
	ctx := r.Context()
	owned, err := s.cfg.Jobs.List(ctx, user.ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	for _, job := range owned {
		if job.UserID == user.ID && (job.Status == jobs.Queued || job.Status == jobs.Running) {
			writeError(w, http.StatusConflict, "analysis_active", "An analysis is queued or running. Cancel it first.")
			return
		}
	}
	for _, job := range owned {
		if job.UserID != user.ID {
			continue
		}
		if job.Status == jobs.Paused {
			s.cfg.Jobs.Cancel(ctx, job.ID)
		}
		s.removeJobFiles(r, job)
	}
	if reports, err := s.cfg.Reports.ListReportsForUser(ctx, user.ID); err == nil {
		for _, report := range reports {
			if report.UserID == user.ID {
				s.removeReportFiles(ctx, report)
			}
		}
	}
	if s.cfg.Recordings != nil {
		if recordings, err := s.cfg.Recordings.ListRecordingsForUser(ctx, user.ID); err == nil {
			for _, recording := range recordings {
				if s.cfg.Objects != nil {
					s.deleteRecordingObjects(ctx, recording)
				} else if path, err := artifacts.Confined(s.cfg.RecordingsDir, recording.Path); err == nil {
					os.Remove(path)
				}
			}
		}
	}
	if err := s.cfg.Auth.DeleteAccount(ctx, user.ID); err != nil && !errors.Is(err, auth.ErrNotFound) {
		writeError(w, http.StatusInternalServerError, "store_error", "The account could not be deleted.")
		return
	}
	s.log.Info("account deleted", "user", user.ID)
	s.clearSession(w, r)
	w.WriteHeader(http.StatusNoContent)
}

// removeJobFiles deletes a job's directory (its run and its log), or, in an
// object store, everything under the job's prefix.
func (s *Server) removeJobFiles(r *http.Request, job jobs.Job) {
	if s.cfg.Objects != nil {
		if objects, err := s.cfg.Objects.List(r.Context(), "jobs/"+job.ID+"/"); err == nil {
			for _, object := range objects {
				s.cfg.Objects.Delete(r.Context(), object.Key)
			}
		}
		return
	}
	if s.cfg.ArtifactsDir == "" {
		return
	}
	if dir, err := artifacts.ConfinedDir(s.cfg.ArtifactsDir, filepath.Join(s.cfg.ArtifactsDir, job.ID)); err == nil {
		os.RemoveAll(dir)
	}
}
