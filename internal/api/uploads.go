package api

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
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

func randomID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
