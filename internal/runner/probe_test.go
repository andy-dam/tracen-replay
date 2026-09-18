package runner

import (
	"context"
	"os"
	"strings"
	"testing"
)

func fakeAnalyzer(mode string) *AnalyzerVersion {
	return &AnalyzerVersion{
		Exec:    Exec{Env: append(os.Environ(), "TRACEN_FAKE_WORKER="+mode)},
		Python:  os.Args[0],
		WorkDir: ".",
	}
}

func TestProbeRefusesAnEmptyCommand(t *testing.T) {
	if _, err := (Exec{}).Probe(context.Background(), nil, "."); err == nil {
		t.Fatal("expected a refusal")
	}
}

func TestAnalyzerVersionAsksOnceAndKeepsTheAnswer(t *testing.T) {
	analyzer := fakeAnalyzer("version")
	version, err := analyzer.Version(context.Background())
	if err != nil {
		t.Fatalf("Version: %v", err)
	}
	if version.Package != "0.1.0" || len(version.CodeDigest) != 64 {
		t.Fatalf("version = %+v", version)
	}
	// The identity belongs to a tree on disk, so a second caller pays nothing.
	// Breaking the fake proves the answer came from the cache and not a rerun.
	analyzer.Exec = Exec{Env: append(os.Environ(), "TRACEN_FAKE_WORKER=version-broken")}
	again, err := analyzer.Version(context.Background())
	if err != nil || again != version {
		t.Fatalf("cached call returned %+v, %v", again, err)
	}
}

func TestAnalyzerVersionDoesNotRememberAFailure(t *testing.T) {
	analyzer := fakeAnalyzer("version-broken")
	_, err := analyzer.Version(context.Background())
	if err == nil {
		t.Fatal("expected the failing probe to be reported")
	}
	// The analyzer's own message is what makes such a failure diagnosable.
	if !strings.Contains(err.Error(), "No module named") {
		t.Errorf("error lost the analyzer's message: %v", err)
	}
	// A later caller may ask again, because nothing says the failure is final.
	analyzer.Exec = Exec{Env: append(os.Environ(), "TRACEN_FAKE_WORKER=version")}
	if _, err := analyzer.Version(context.Background()); err != nil {
		t.Fatalf("second attempt: %v", err)
	}
}

func TestAnalyzerVersionNeedsAnInterpreter(t *testing.T) {
	if _, err := (&AnalyzerVersion{}).Version(context.Background()); err == nil {
		t.Fatal("expected a refusal without an interpreter")
	}
}
