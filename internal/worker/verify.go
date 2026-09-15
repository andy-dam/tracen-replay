package worker

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// TimelineSchemaVersion is the timeline document schema this service reads.
const TimelineSchemaVersion = "tracen-replay/timeline-v1"

// Artifacts are the verified files of a completed job.
type Artifacts struct {
	ReportPath   string
	ReportSHA256 string
	TimelinePath string
}

// Verify checks the files a completed result names: the report exists and
// hashes to report_sha256, and timeline.json exists beside it with the
// expected schema. Exit code zero and a well-formed object are not enough on
// their own; this is the step that makes a job's success trustworthy.
func Verify(r Result) (Artifacts, error) {
	if !r.Completed() {
		return Artifacts{}, &ContractError{CodeBadTerminalOutput, "verify called for a result that did not complete"}
	}
	digest, err := fileSHA256(r.ReportPath)
	if err != nil {
		return Artifacts{}, &ContractError{CodeReportMissing, "report is not readable: " + err.Error()}
	}
	if digest != r.ReportSHA256 {
		return Artifacts{}, &ContractError{CodeReportHashMismatch, fmt.Sprintf("report hashes to %s, worker reported %s", digest, r.ReportSHA256)}
	}
	timeline := r.TimelinePath
	if timeline == "" {
		timeline = filepath.Join(filepath.Dir(r.ReportPath), "timeline.json")
	}
	if err := checkTimelineSchema(timeline); err != nil {
		return Artifacts{}, err
	}
	return Artifacts{ReportPath: r.ReportPath, ReportSHA256: digest, TimelinePath: timeline}, nil
}

func checkTimelineSchema(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return &ContractError{CodeTimelineMissing, "timeline is not readable: " + err.Error()}
	}
	defer f.Close()
	var head struct {
		SchemaVersion string `json:"schema_version"`
	}
	if err := json.NewDecoder(f).Decode(&head); err != nil {
		return &ContractError{CodeTimelineInvalid, "timeline is not a JSON object: " + err.Error()}
	}
	if head.SchemaVersion != TimelineSchemaVersion {
		return &ContractError{CodeTimelineInvalid, fmt.Sprintf("timeline schema %q, expected %q", head.SchemaVersion, TimelineSchemaVersion)}
	}
	return nil
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
