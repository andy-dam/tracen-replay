package worker

import (
	"strings"
	"testing"
)

const digest = "2eca55da00a834e13e6d32583091299d086ba8d7c9033639527be5b099f0af65"

func TestVersionArgvAsksOnlyTheInterpreter(t *testing.T) {
	if _, err := VersionArgv(""); err == nil {
		t.Fatal("an empty interpreter must be refused")
	}
	argv, err := VersionArgv("python")
	if err != nil {
		t.Fatalf("VersionArgv: %v", err)
	}
	want := []string{"python", "-X", "utf8", "-m", "tracen_replay.analysis_job", "--worker-version"}
	if strings.Join(argv, " ") != strings.Join(want, " ") {
		t.Fatalf("argv = %v, want %v", argv, want)
	}
}

func TestDecodeVersionAcceptsOneWellFormedAnswer(t *testing.T) {
	answer := `{"schema_version":"tracen-replay/worker-version-v1",` +
		`"worker_version":{"package":"0.1.0","code_digest":"` + digest + `"}}`
	version, err := DecodeVersion([]byte("\n" + answer + "\n"))
	if err != nil {
		t.Fatalf("DecodeVersion: %v", err)
	}
	if version.Package != "0.1.0" || version.CodeDigest != digest {
		t.Fatalf("version = %+v", version)
	}
}

func TestDecodeVersionRefusesAnythingItCannotTrust(t *testing.T) {
	good := `{"schema_version":"tracen-replay/worker-version-v1","worker_version":{"package":"0.1.0","code_digest":"` + digest + `"}}`
	for name, stdout := range map[string]string{
		"nothing":        "",
		"not json":       "loading models...",
		"a job envelope": `{"schema_version":"tracen-replay/analysis-job-v1","status":"succeeded"}`,
		"no identity":    `{"schema_version":"tracen-replay/worker-version-v1"}`,
		"short digest":   `{"schema_version":"tracen-replay/worker-version-v1","worker_version":{"package":"0.1.0","code_digest":"abc"}}`,
		"two answers":    good + good,
		"trailing noise": good + " oops",
	} {
		if _, err := DecodeVersion([]byte(stdout)); err == nil {
			t.Errorf("%s: expected a refusal", name)
		}
	}
}
