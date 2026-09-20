package api

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// uploadExtensions are the containers the analyzer's ffmpeg decode accepts
// and the browser can play back for seeking (mp4 and webm; mov and mkv
// decode but may not play in every browser).
var uploadExtensions = map[string]bool{".mp4": true, ".m4v": true, ".mov": true, ".webm": true, ".mkv": true}

// uploadRecording streams one multipart file part ("file") to the user's
// upload directory, hashing it on the way, and records it when the copy is
// complete. Nothing is buffered in memory and the client never chooses the
// path on disk.
func (s *Server) uploadRecording(w http.ResponseWriter, r *http.Request) {
	if s.noRecordings(w) {
		return
	}
	user := userFrom(r)
	// What the user already keeps, and what everyone keeps, is checked
	// before a byte is read: the request's Content-Length says roughly how
	// much is coming (the multipart framing adds a few hundred bytes), and
	// the bytes that actually arrive are checked again after the copy.
	quota := s.cfg.Quota
	usage, err := s.cfg.Recordings.RecordingUsage(r.Context(), user.ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	incoming := max(r.ContentLength, 0)
	if quota.MaxRecordingsPerUser > 0 && usage.Count >= quota.MaxRecordingsPerUser {
		writeError(w, http.StatusForbidden, "quota_exceeded", fmt.Sprintf("you can keep at most %d recordings; delete one first", quota.MaxRecordingsPerUser))
		return
	}
	if quota.MaxBytesPerUser > 0 && usage.Bytes+incoming > quota.MaxBytesPerUser {
		writeError(w, http.StatusForbidden, "quota_exceeded", fmt.Sprintf("your recordings may take at most %s together; delete one first", gigabytes(quota.MaxBytesPerUser)))
		return
	}
	if quota.MaxBytesTotal > 0 {
		total, err := s.cfg.Recordings.RecordingUsage(r.Context(), "")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "store_error", err.Error())
			return
		}
		if total.Bytes+incoming > quota.MaxBytesTotal {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": map[string]string{"code": "storage_full",
				"message": "the service has no room for another recording right now; try again later"}})
			return
		}
	}
	r.Body = http.MaxBytesReader(w, r.Body, s.cfg.UploadLimit)
	reader, err := r.MultipartReader()
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "send the recording as multipart/form-data with a file part named file")
		return
	}
	for {
		part, err := reader.NextPart()
		if errors.Is(err, io.EOF) {
			writeError(w, http.StatusBadRequest, "bad_request", "no file part named file was sent")
			return
		}
		if err != nil {
			writeError(w, http.StatusBadRequest, "bad_request", "malformed multipart body: "+err.Error())
			return
		}
		if part.FormName() != "file" {
			io.Copy(io.Discard, part)
			continue
		}
		name := filepath.Base(strings.ReplaceAll(part.FileName(), "\\", "/"))
		ext := strings.ToLower(filepath.Ext(name))
		if name == "" || name == "." || !uploadExtensions[ext] {
			writeError(w, http.StatusBadRequest, "unsupported_recording", "upload an .mp4, .m4v, .mov, .webm or .mkv recording")
			return
		}
		if len(name) > 200 {
			name = name[:200]
		}
		owner := user.ID
		if owner == "" {
			owner = "local"
		}
		dir := filepath.Join(s.cfg.RecordingsDir, owner)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			writeError(w, http.StatusInternalServerError, "upload_failed", err.Error())
			return
		}
		id := randomID()
		path := filepath.Join(dir, id+ext)
		file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "upload_failed", err.Error())
			return
		}
		digest := sha256.New()
		size, copyErr := io.Copy(io.MultiWriter(file, digest), part)
		closeErr := file.Close()
		if copyErr != nil || closeErr != nil {
			os.Remove(path)
			var tooLarge *http.MaxBytesError
			if errors.As(copyErr, &tooLarge) {
				writeError(w, http.StatusRequestEntityTooLarge, "upload_too_large", "the recording exceeds the upload limit")
				return
			}
			writeError(w, http.StatusBadRequest, "upload_failed", "the upload did not complete")
			return
		}
		if size == 0 {
			os.Remove(path)
			writeError(w, http.StatusBadRequest, "upload_failed", "the recording is empty")
			return
		}
		if quota.MaxBytesPerUser > 0 && usage.Bytes+size > quota.MaxBytesPerUser {
			os.Remove(path)
			writeError(w, http.StatusForbidden, "quota_exceeded", fmt.Sprintf("your recordings may take at most %s together; delete one first", gigabytes(quota.MaxBytesPerUser)))
			return
		}
		// The file is a recording the analyzer can work on, or it is not
		// kept: not a video, or longer, larger or faster than the service
		// will spend its hours on.
		if s.cfg.Probe != nil {
			media, err := s.cfg.Probe(r.Context(), path)
			if err != nil {
				os.Remove(path)
				s.log.Info("upload refused", "user", user.ID, "name", name, "reason", err.Error())
				writeError(w, http.StatusBadRequest, "unsupported_recording", "the file is not a video this service can read")
				return
			}
			if reason := quota.refuse(media); reason != "" {
				os.Remove(path)
				writeError(w, http.StatusBadRequest, "unsupported_recording", reason)
				return
			}
		}
		recording := jobs.Recording{ID: id, UserID: user.ID, Name: name, Path: path, Size: size,
			SHA256: hex.EncodeToString(digest.Sum(nil)), CreatedAt: time.Now()}
		if err := s.cfg.Recordings.CreateRecording(r.Context(), recording); err != nil {
			os.Remove(path)
			writeError(w, http.StatusInternalServerError, "store_error", err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, recording)
		return
	}
}

// gigabytes says a byte count the way people read it.
func gigabytes(n int64) string {
	return strconv.FormatFloat(float64(n)/float64(1<<30), 'f', -1, 64) + " GB"
}

func randomID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
