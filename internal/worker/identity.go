package worker

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

// VersionSchema is the envelope the analyzer answers --worker-version with. It
// is deliberately not the job schema: nothing ran, so there is no job status to
// report, only who the analyzer is.
const VersionSchema = "tracen-replay/worker-version-v1"

// Version is the analyzer's answer to --worker-version.
type Version struct {
	SchemaVersion string         `json:"schema_version"`
	WorkerVersion *WorkerVersion `json:"worker_version"`
}

// VersionArgv returns the argument vector that asks the installed analyzer who
// it is. The question has no source, output or model, so only the interpreter
// is needed; the process must still run with the analyzer's WorkDir as its
// working directory, exactly as a job does.
func VersionArgv(python string) ([]string, error) {
	if python == "" {
		return nil, errors.New("worker: python interpreter is required")
	}
	return append([]string{python, "-X", "utf8", "-m", "tracen_replay.analysis_job"}, VersionArgs()...), nil
}

// VersionArgs is the analyzer's own argument for the identity query: what
// VersionArgv passes after the interpreter and the module, and what a worker
// container takes.
func VersionArgs() []string { return []string{"--worker-version"} }

// DecodeVersion parses that answer, which must hold exactly one JSON object of
// the worker-version schema carrying a source digest. It is as strict as the
// terminal decoder for the same reason: a half-understood answer would be
// compared against the identity stored on a report.
func DecodeVersion(stdout []byte) (WorkerVersion, error) {
	dec := json.NewDecoder(bytes.NewReader(stdout))
	var answer Version
	if err := dec.Decode(&answer); err != nil {
		if errors.Is(err, io.EOF) {
			return WorkerVersion{}, errors.New("analyzer wrote no worker-version object")
		}
		return WorkerVersion{}, fmt.Errorf("analyzer worker-version output is not a JSON object: %w", err)
	}
	var extra json.RawMessage
	if err := dec.Decode(&extra); err == nil {
		return WorkerVersion{}, errors.New("analyzer wrote more than one worker-version object")
	} else if !errors.Is(err, io.EOF) {
		return WorkerVersion{}, fmt.Errorf("trailing data after the worker-version object: %w", err)
	}
	if answer.SchemaVersion != VersionSchema {
		return WorkerVersion{}, fmt.Errorf("worker-version schema %q, expected %q", answer.SchemaVersion, VersionSchema)
	}
	if answer.WorkerVersion == nil {
		return WorkerVersion{}, errors.New("worker-version object carries no identity")
	}
	if !hexDigest.MatchString(answer.WorkerVersion.CodeDigest) {
		return WorkerVersion{}, fmt.Errorf("worker-version code digest %q is not a sha-256 digest", answer.WorkerVersion.CodeDigest)
	}
	return *answer.WorkerVersion, nil
}
