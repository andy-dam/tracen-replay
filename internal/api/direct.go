package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path"
	"regexp"
	"strings"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/objectstore"
)

// Direct upload: when the object store can sign URLs, the browser puts
// the recording straight into it and the API never carries the bytes. The
// client asks for a target (the quotas are checked, a key of the API's
// choosing is signed for a few minutes), uploads, then reports the upload
// complete; the API probes the object where it lies, refuses what is not a
// recording, and records it. A target that is never completed leaves an
// object no record names; the sweeper removes those after a day.

// uploadTTL is how long an upload URL stays valid: long enough for a slow
// link to move a few gigabytes.
const uploadTTL = 2 * time.Hour

var uploadID = regexp.MustCompile(`^[0-9a-f]{32}$`)

// uploadTarget is the answer to a request for an upload target.
type uploadTarget struct {
	// Mode is "direct" (put the file at URL with Headers, then complete)
	// or "multipart" (post the file to /api/recordings as before).
	Mode      string            `json:"mode"`
	ID        string            `json:"id,omitempty"`
	URL       string            `json:"url,omitempty"`
	Method    string            `json:"method,omitempty"`
	Headers   map[string]string `json:"headers,omitempty"`
	ExpiresAt time.Time         `json:"expires_at,omitzero"`
}

// uploadOwner names the prefix a user's uploads live under.
func uploadOwner(userID string) string {
	if userID == "" {
		return "local"
	}
	return userID
}

// checkUploadName says the extension of an acceptable upload name, or an
// error message.
func checkUploadName(name string) (ext string, refusal string) {
	name = path.Base(strings.ReplaceAll(name, "\\", "/"))
	ext = strings.ToLower(path.Ext(name))
	if name == "" || name == "." || !uploadExtensions[ext] {
		return "", "upload an .mp4, .m4v, .mov, .webm or .mkv recording"
	}
	return ext, ""
}

// beginUpload answers POST /api/recordings/uploads.
func (s *Server) beginUpload(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	var body struct {
		Name string `json:"name"`
		Size int64  `json:"size"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "send the recording's name and size as JSON")
		return
	}
	if s.cfg.Objects == nil {
		writeJSON(w, http.StatusOK, uploadTarget{Mode: "multipart"})
		return
	}
	ext, refusal := checkUploadName(body.Name)
	if refusal != "" {
		writeError(w, http.StatusBadRequest, "unsupported_recording", refusal)
		return
	}
	if body.Size <= 0 {
		writeError(w, http.StatusBadRequest, "upload_failed", "the recording is empty")
		return
	}
	if body.Size > s.cfg.UploadLimit {
		writeError(w, http.StatusRequestEntityTooLarge, "upload_too_large", "the recording exceeds the upload limit")
		return
	}
	user := userFrom(r)
	if !s.withinUploadQuota(w, r, user.ID, body.Size) {
		return
	}
	id := randomID()
	key := "originals/" + uploadOwner(user.ID) + "/" + id + ext
	contentType := videoType(key)
	url, err := s.cfg.Objects.PresignUpload(r.Context(), key, contentType, uploadTTL)
	if errors.Is(err, objectstore.ErrNoPresign) {
		writeJSON(w, http.StatusOK, uploadTarget{Mode: "multipart"})
		return
	}
	if err != nil {
		s.log.Error("upload URL not signed", "user", user.ID, "error", err)
		writeError(w, http.StatusInternalServerError, "upload_failed", "the upload could not be prepared")
		return
	}
	writeJSON(w, http.StatusOK, uploadTarget{Mode: "direct", ID: id, URL: url, Method: http.MethodPut,
		Headers: map[string]string{"x-ms-blob-type": "BlockBlob", "Content-Type": contentType}, ExpiresAt: time.Now().Add(uploadTTL)})
}

// withinUploadQuota refuses, with the same answers as a multipart upload,
// an upload that would pass the user's or the service's bounds.
func (s *Server) withinUploadQuota(w http.ResponseWriter, r *http.Request, userID string, incoming int64) bool {
	quota := s.cfg.Quota
	usage, err := s.cfg.Recordings.RecordingUsage(r.Context(), userID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return false
	}
	if quota.MaxRecordingsPerUser > 0 && usage.Count >= quota.MaxRecordingsPerUser {
		writeError(w, http.StatusForbidden, "quota_exceeded", fmt.Sprintf("you can keep at most %d recordings; delete one first", quota.MaxRecordingsPerUser))
		return false
	}
	if quota.MaxBytesPerUser > 0 && usage.Bytes+incoming > quota.MaxBytesPerUser {
		writeError(w, http.StatusForbidden, "quota_exceeded", fmt.Sprintf("your recordings may take at most %s together; delete one first", gigabytes(quota.MaxBytesPerUser)))
		return false
	}
	if quota.MaxBytesTotal > 0 {
		total, err := s.cfg.Recordings.RecordingUsage(r.Context(), "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "store_error", err.Error())
			return false
		}
		if total.Bytes+incoming > quota.MaxBytesTotal {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": map[string]string{"code": "storage_full",
				"message": "the service has no room for another recording right now; try again later"}})
			return false
		}
	}
	return true
}

// completeUpload answers POST /api/recordings/uploads/{id}/complete.
func (s *Server) completeUpload(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	if s.cfg.Objects == nil {
		writeError(w, http.StatusNotFound, "not_found", "direct uploads are not available here")
		return
	}
	id := r.PathValue("id")
	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil || !uploadID.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "send the upload id you were given and the recording's name")
		return
	}
	ext, refusal := checkUploadName(body.Name)
	if refusal != "" {
		writeError(w, http.StatusBadRequest, "unsupported_recording", refusal)
		return
	}
	name := path.Base(strings.ReplaceAll(body.Name, "\\", "/"))
	if len(name) > 200 {
		name = name[:200]
	}
	user := userFrom(r)
	key := "originals/" + uploadOwner(user.ID) + "/" + id + ext
	ctx := r.Context()
	if _, err := s.cfg.Recordings.GetRecording(ctx, id); err == nil {
		writeError(w, http.StatusConflict, "upload_completed", "this upload was already completed")
		return
	}
	object, err := s.cfg.Objects.Stat(ctx, key)
	if err != nil {
		writeError(w, http.StatusNotFound, "upload_missing", "No upload with this id has arrived. The file goes to the upload URL first.")
		return
	}
	if object.Size == 0 || object.Size > s.cfg.UploadLimit {
		s.cfg.Objects.Delete(ctx, key)
		if object.Size == 0 {
			writeError(w, http.StatusBadRequest, "upload_failed", "the recording is empty")
		} else {
			writeError(w, http.StatusRequestEntityTooLarge, "upload_too_large", "the recording exceeds the upload limit")
		}
		return
	}
	if !s.withinUploadQuota(w, r, user.ID, object.Size) {
		s.cfg.Objects.Delete(ctx, key)
		return
	}
	if s.cfg.Probe != nil {
		input, err := s.recordingInput(ctx, key)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "upload_failed", "the upload could not be read back")
			return
		}
		media, err := s.cfg.Probe(ctx, input)
		if err != nil {
			s.cfg.Objects.Delete(ctx, key)
			s.log.Info("upload refused", "user", user.ID, "name", name, "reason", err.Error())
			writeError(w, http.StatusBadRequest, "unsupported_recording", "the file is not a video this service can read")
			return
		}
		if reason := s.cfg.Quota.refuse(media); reason != "" {
			s.cfg.Objects.Delete(ctx, key)
			writeError(w, http.StatusBadRequest, "unsupported_recording", reason)
			return
		}
	}
	// The hash is filled in by the worker that first analyzes the
	// recording; the API never reads the bytes.
	recording := jobs.Recording{ID: id, UserID: user.ID, Name: name, Path: key, Size: object.Size, CreatedAt: time.Now()}
	if err := s.cfg.Recordings.CreateRecording(ctx, recording); err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, s.withOriginalUntil(recording))
}

// playbackKey is what a recording plays and extracts frames from: its
// kept copy when a worker made one, otherwise the original.
func playbackKey(recording jobs.Recording) string {
	if recording.KeptPath != "" {
		return recording.KeptPath
	}
	return recording.Path
}

// deleteRecordingObjects removes the recording's original and its kept
// copy.
func (s *Server) deleteRecordingObjects(ctx context.Context, recording jobs.Recording) {
	for _, key := range []string{recording.Path, recording.KeptPath} {
		if key == "" {
			continue
		}
		if err := s.cfg.Objects.Delete(ctx, key); err != nil {
			s.log.Warn("deleted recording's object not removed", "recording", recording.ID, "key", key, "error", err)
		}
	}
}
