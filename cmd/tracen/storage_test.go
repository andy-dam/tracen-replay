package main

import (
	"os"
	"path/filepath"
	"testing"
)

// The analyzer runs in -workdir, so the defaults, which are relative to the
// repository root, must reach it as absolute paths.
func TestAnalyzerPathsAreAbsolute(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)
	// The temporary directory may itself be reached through a link.
	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	python := defaultPython()
	data := filepath.Join(".local", "tracen-data")
	models := filepath.Join(".local", "models", "rapidocr")
	reader := ""
	if err := analyzerPaths(&python, &data, &models, &reader); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ got, want string }{
		{python, filepath.Join(root, defaultPython())},
		{data, filepath.Join(root, ".local", "tracen-data")},
		{models, filepath.Join(root, ".local", "models", "rapidocr")},
		{reader, ""},
	} {
		if c.got != c.want {
			t.Errorf("got %q, want %q", c.got, c.want)
		}
	}
}

func TestAnalyzerPathsKeepBareInterpreter(t *testing.T) {
	python := "python"
	absolute := filepath.Join(t.TempDir(), "python")
	models := absolute
	if err := analyzerPaths(&python, &models); err != nil {
		t.Fatal(err)
	}
	if python != "python" {
		t.Errorf("a bare interpreter name is for the PATH lookup, got %q", python)
	}
	if models != absolute {
		t.Errorf("an absolute path changed: got %q, want %q", models, absolute)
	}
}
