package worker

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
)

// Versions the service was written against. A worker that reports another
// schema is refused rather than interpreted.
const (
	SchemaVersion       = "tracen-replay/analysis-job-v1"
	ReportSchemaVersion = "tracen-replay/full-recording-v1"
)

// Terminal status values.
const (
	StatusSucceeded                  = "succeeded"
	StatusCompletedWithStageFailures = "completed_with_stage_failures"
	StatusFailed                     = "failed"
)

// Worker exit codes.
const (
	ExitSuccess  = 0
	ExitProducer = 1
	ExitInput    = 2
)

// StageFailure is one enrichment stage the worker skipped fail-soft.
type StageFailure struct {
	Stage string `json:"stage"`
	Error string `json:"error"`
}

// PrunedFrames reports what --prune-frames deleted.
type PrunedFrames struct {
	Files int   `json:"files"`
	Bytes int64 `json:"bytes"`
}

// WorkerVersion identifies the worker that produced a terminal object: the
// installed package version and a digest over the analyzer's source files.
type WorkerVersion struct {
	Package    string `json:"package"`
	CodeDigest string `json:"code_digest"`
}

// Failure is the worker's error envelope: a stable code and a message.
type Failure struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Result is the worker's terminal JSON object.
type Result struct {
	SchemaVersion       string         `json:"schema_version"`
	Status              string         `json:"status"`
	ReportSchemaVersion string         `json:"report_schema_version,omitempty"`
	ReportPath          string         `json:"report_path,omitempty"`
	ReportSHA256        string         `json:"report_sha256,omitempty"`
	SourceSHA256        string         `json:"source_sha256,omitempty"`
	EvidenceRoot        string         `json:"evidence_root,omitempty"`
	FullSourceProcessed bool           `json:"full_source_processed,omitempty"`
	FullyVerified       bool           `json:"fully_verified,omitempty"`
	GoReady             bool           `json:"go_ready,omitempty"`
	TimelinePath        string         `json:"timeline_path,omitempty"`
	StageFailures       []StageFailure `json:"stage_failures,omitempty"`
	PrunedFrames        *PrunedFrames  `json:"pruned_frames,omitempty"`
	Error               *Failure       `json:"error,omitempty"`
	WorkerVersion       *WorkerVersion `json:"worker_version,omitempty"`
}

// Completed reports whether the status is one of the two success values.
func (r Result) Completed() bool {
	return r.Status == StatusSucceeded || r.Status == StatusCompletedWithStageFailures
}

// ContractError is a violation of the worker contract detected on the Go side.
// Code is stable and suitable for a job record; Message explains it.
type ContractError struct {
	Code    string
	Message string
}

func (e *ContractError) Error() string { return e.Code + ": " + e.Message }

// Contract error codes.
const (
	CodeBadTerminalOutput  = "bad_terminal_output"
	CodeMultipleObjects    = "multiple_terminal_objects"
	CodeSchemaMismatch     = "schema_mismatch"
	CodeStatusExitMismatch = "status_exit_mismatch"
	CodeReportMissing      = "report_missing"
	CodeReportHashMismatch = "report_hash_mismatch"
	CodeTimelineMissing    = "timeline_missing"
	CodeTimelineInvalid    = "timeline_invalid"
)

var hexDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)

// DecodeTerminal parses the worker's stdout, which must hold exactly one JSON
// object of the analysis-job-v1 schema. Surrounding whitespace is tolerated;
// a second object, a non-object, or a foreign schema is a contract error.
func DecodeTerminal(stdout []byte) (Result, error) {
	dec := json.NewDecoder(bytes.NewReader(stdout))
	var r Result
	if err := dec.Decode(&r); err != nil {
		if errors.Is(err, io.EOF) {
			return Result{}, &ContractError{CodeBadTerminalOutput, "worker wrote no terminal JSON object"}
		}
		return Result{}, &ContractError{CodeBadTerminalOutput, "worker stdout is not a JSON object: " + err.Error()}
	}
	var extra json.RawMessage
	if err := dec.Decode(&extra); err == nil {
		return Result{}, &ContractError{CodeMultipleObjects, "worker wrote more than one terminal JSON object"}
	} else if !errors.Is(err, io.EOF) {
		return Result{}, &ContractError{CodeBadTerminalOutput, "trailing data after the terminal JSON object: " + err.Error()}
	}
	if r.SchemaVersion != SchemaVersion {
		return Result{}, &ContractError{CodeSchemaMismatch, fmt.Sprintf("terminal schema %q, expected %q", r.SchemaVersion, SchemaVersion)}
	}
	switch r.Status {
	case StatusFailed:
		if r.Error == nil || r.Error.Code == "" {
			return Result{}, &ContractError{CodeBadTerminalOutput, "failed status without an error code"}
		}
	case StatusSucceeded, StatusCompletedWithStageFailures:
		switch {
		case r.ReportSchemaVersion != ReportSchemaVersion:
			return Result{}, &ContractError{CodeSchemaMismatch, fmt.Sprintf("report schema %q, expected %q", r.ReportSchemaVersion, ReportSchemaVersion)}
		case r.ReportPath == "" || r.EvidenceRoot == "":
			return Result{}, &ContractError{CodeBadTerminalOutput, "success without report_path or evidence_root"}
		case !hexDigest.MatchString(r.ReportSHA256) || !hexDigest.MatchString(r.SourceSHA256):
			return Result{}, &ContractError{CodeBadTerminalOutput, "success without well-formed report_sha256 and source_sha256"}
		case r.Status == StatusCompletedWithStageFailures && len(r.StageFailures) == 0:
			return Result{}, &ContractError{CodeBadTerminalOutput, "completed_with_stage_failures lists no failed stage"}
		}
	default:
		return Result{}, &ContractError{CodeBadTerminalOutput, fmt.Sprintf("unknown terminal status %q", r.Status)}
	}
	return r, nil
}

// Interpret combines the exit code with the terminal object. Exit code zero
// requires a success status and a non-zero exit requires a failed status; a
// disagreement is a contract error, and a non-zero exit without a decodable
// object is reported with the worker's stderr tail left to the caller.
func Interpret(exitCode int, stdout []byte) (Result, error) {
	r, err := DecodeTerminal(stdout)
	if err != nil {
		if exitCode != ExitSuccess {
			return Result{}, &ContractError{CodeBadTerminalOutput, fmt.Sprintf("worker exited %d without a valid terminal object: %v", exitCode, err)}
		}
		return Result{}, err
	}
	if (exitCode == ExitSuccess) != r.Completed() {
		return Result{}, &ContractError{CodeStatusExitMismatch, fmt.Sprintf("exit code %d disagrees with status %q", exitCode, r.Status)}
	}
	return r, nil
}
