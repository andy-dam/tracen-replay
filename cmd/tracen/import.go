package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/timeline"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// runImport registers an existing analyzer run directory (report.json plus
// timeline.json) as an imported report without modifying it. The report is
// hashed, the timeline is validated against timeline-v1, and the record
// points at the originals in place.
func runImport(args []string) error {
	fs := flag.NewFlagSet("import", flag.ContinueOnError)
	dataDir := fs.String("data", filepath.Join(".local", "tracen-data"), "directory holding the database")
	recording := fs.String("recording", "", "optional path of the recording, enabling frame extraction")
	id := fs.String("id", "", "report id (default: derived from the run directory name)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 1 {
		return errors.New("usage: tracen import [-data DIR] [-recording FILE] [-id ID] RUN_DIR")
	}
	root, err := filepath.Abs(fs.Arg(0))
	if err != nil {
		return err
	}
	reportPath := filepath.Join(root, "report.json")
	timelinePath := filepath.Join(root, "timeline.json")
	digest, err := fileSHA256(reportPath)
	if err != nil {
		return fmt.Errorf("report: %w", err)
	}
	if err := checkReportSchema(reportPath); err != nil {
		return err
	}
	doc, err := timeline.Load(timelinePath)
	if err != nil {
		return err
	}
	if *recording != "" {
		if _, err := os.Stat(*recording); err != nil {
			return fmt.Errorf("recording: %w", err)
		}
		if abs, err := filepath.Abs(*recording); err == nil {
			*recording = abs
		}
	}
	if *id == "" {
		*id = "imported-" + filepath.Base(root)
	}
	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		return err
	}
	db, err := store.Open(filepath.Join(*dataDir, "tracen.db"))
	if err != nil {
		return err
	}
	defer db.Close()
	report := jobs.Report{ID: *id, Origin: "imported", SourceName: doc.Source.Name, SourceSHA256: doc.Source.SHA256,
		ReportPath: reportPath, ReportSHA256: digest, TimelinePath: timelinePath, EvidenceRoot: root, SourcePath: *recording,
		CreatedAt: time.Now(), DurationMS: doc.Source.DurationMS, Turns: len(doc.Turns), Entries: len(doc.Entries)}
	if err := db.CreateReport(context.Background(), report); err != nil {
		return fmt.Errorf("record report: %w", err)
	}
	fmt.Printf("imported %s: %s, %d turns, %d entries, report sha256 %s\n", report.ID, doc.Source.Name, report.Turns, report.Entries, digest[:12])
	return nil
}

func checkReportSchema(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	// Walk the top-level keys with a streaming decoder: the report is large
	// and the schema key can sit anywhere in the object.
	dec := json.NewDecoder(f)
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return fmt.Errorf("report %s is not a JSON object", path)
	}
	for dec.More() {
		keyTok, err := dec.Token()
		if err != nil {
			return fmt.Errorf("report %s: %w", path, err)
		}
		key, _ := keyTok.(string)
		if key == "schema_version" {
			var version string
			if err := dec.Decode(&version); err != nil {
				return fmt.Errorf("report %s: schema_version is not a string", path)
			}
			if version != worker.ReportSchemaVersion {
				return fmt.Errorf("report %s declares schema %q, expected %q", path, version, worker.ReportSchemaVersion)
			}
			return nil
		}
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			return fmt.Errorf("report %s: %w", path, err)
		}
	}
	return fmt.Errorf("report %s has no schema_version", path)
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
