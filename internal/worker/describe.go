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

// DescribeOutput rebuilds the terminal object of a finished analysis from
// the run directory it wrote. Everything in that object is a fact about the
// report on disk, so an analysis whose terminal object was lost on its way
// (something else wrote to the worker's standard output) need not be run
// again: the result is the same bytes the worker would have sent, less the
// worker's own version, which only the worker knows. The caller verifies it
// like any other result.
func DescribeOutput(runDir string) ([]byte, error) {
	reportPath := filepath.Join(runDir, "report.json")
	file, err := os.Open(reportPath)
	if err != nil {
		return nil, fmt.Errorf("the run has no report: %w", err)
	}
	defer file.Close()
	digest := sha256.New()
	var report struct {
		Source struct {
			SHA256 string `json:"sha256"`
		} `json:"source"`
		Verification struct {
			FullSourceProcessed bool `json:"full_source_processed"`
			FullyVerified       bool `json:"fully_verified"`
			GoReady             bool `json:"go_ready"`
		} `json:"verification"`
		StageFailures []StageFailure `json:"stage_failures"`
	}
	// The report is hashed as it is read; it can be a couple of hundred
	// megabytes and is decoded once.
	if err := json.NewDecoder(io.TeeReader(file, digest)).Decode(&report); err != nil {
		return nil, fmt.Errorf("the run's report cannot be read: %w", err)
	}
	if _, err := io.Copy(digest, file); err != nil {
		return nil, err
	}
	if !hexDigest.MatchString(report.Source.SHA256) {
		return nil, fmt.Errorf("the run's report does not name its recording")
	}
	result := Result{SchemaVersion: SchemaVersion, Status: StatusSucceeded, ReportSchemaVersion: ReportSchemaVersion,
		ReportPath: reportPath, ReportSHA256: hex.EncodeToString(digest.Sum(nil)), SourceSHA256: report.Source.SHA256,
		EvidenceRoot: runDir, FullSourceProcessed: report.Verification.FullSourceProcessed,
		FullyVerified: report.Verification.FullyVerified, GoReady: report.Verification.GoReady}
	if len(report.StageFailures) > 0 {
		result.Status, result.StageFailures = StatusCompletedWithStageFailures, report.StageFailures
	}
	if timeline := filepath.Join(runDir, "timeline.json"); fileExists(timeline) {
		result.TimelinePath = timeline
	}
	return json.Marshal(result)
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
