// Package sources lists the recordings the service may analyze. Recordings
// live in one configured folder; the browser only ever sees server-issued
// identifiers, so a request cannot name an arbitrary path.
package sources

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/andy-dam/tracen-replay/internal/jobs"
)

// Extensions the folder listing accepts, lower case with the dot.
var Extensions = map[string]bool{".mp4": true, ".mkv": true, ".mov": true, ".webm": true, ".avi": true}

// Folder is a jobs.Sources over one directory (non-recursive).
type Folder struct {
	root string
}

// NewFolder validates the directory and returns the source list provider.
func NewFolder(root string) (*Folder, error) {
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	resolved, err := filepath.EvalSymlinks(abs)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, errors.New("sources: " + root + " is not a directory")
	}
	return &Folder{root: resolved}, nil
}

// Root is the resolved recordings directory.
func (f *Folder) Root() string { return f.root }

// ID derives the stable identifier of a file name within the folder.
func ID(name string) string {
	sum := sha256.Sum256([]byte(name))
	return hex.EncodeToString(sum[:8])
}

// List returns the eligible recordings sorted by name. Symbolic links and
// anything that resolves outside the folder are skipped.
func (f *Folder) List() ([]jobs.Source, error) {
	entries, err := os.ReadDir(f.root)
	if err != nil {
		return nil, err
	}
	var out []jobs.Source
	for _, entry := range entries {
		if entry.IsDir() || entry.Type()&os.ModeSymlink != 0 || !Extensions[strings.ToLower(filepath.Ext(entry.Name()))] {
			continue
		}
		path := filepath.Join(f.root, entry.Name())
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil || filepath.Dir(resolved) != f.root {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		out = append(out, jobs.Source{ID: ID(entry.Name()), Name: entry.Name(), Path: path, Size: info.Size()})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

// Resolve returns the recording behind an identifier issued by List.
func (f *Folder) Resolve(id string) (jobs.Source, error) {
	list, err := f.List()
	if err != nil {
		return jobs.Source{}, err
	}
	for _, source := range list {
		if source.ID == id {
			return source, nil
		}
	}
	return jobs.Source{}, &jobs.NotFoundError{Kind: "source", ID: id}
}
