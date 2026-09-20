package objectstore

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Local keeps objects as files under one directory: the key is the path
// below it. It issues no URLs.
type Local struct {
	Root string
}

// NewLocal makes the root directory and returns the store.
func NewLocal(root string) (*Local, error) {
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(abs, 0o755); err != nil {
		return nil, err
	}
	return &Local{Root: abs}, nil
}

func (l *Local) path(key string) (string, error) {
	if !ValidKey(key) {
		return "", fmt.Errorf("objectstore: bad key %q", key)
	}
	return filepath.Join(l.Root, filepath.FromSlash(key)), nil
}

// Path returns the file behind a key (LocalPaths).
func (l *Local) Path(key string) (string, error) {
	path, err := l.path(key)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return "", ErrNotFound
	}
	return path, nil
}

func (l *Local) Put(ctx context.Context, key string, body io.Reader, size int64, contentType string) error {
	path, err := l.path(key)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	// Written beside the final name and renamed, so a reader never sees a
	// half-written object and a failed write leaves nothing under the key.
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.part")
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(tmp, body)
	closeErr := tmp.Close()
	if copyErr != nil || closeErr != nil {
		os.Remove(tmp.Name())
		return errors.Join(copyErr, closeErr)
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}

func (l *Local) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	path, err := l.Path(key)
	if err != nil {
		return nil, err
	}
	return os.Open(path)
}

func (l *Local) Stat(ctx context.Context, key string) (Object, error) {
	path, err := l.Path(key)
	if err != nil {
		return Object{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return Object{}, ErrNotFound
	}
	return Object{Key: key, Size: info.Size(), ModTime: info.ModTime()}, nil
}

func (l *Local) Delete(ctx context.Context, key string) error {
	path, err := l.path(key)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

func (l *Local) List(ctx context.Context, prefix string) ([]Object, error) {
	if prefix != "" && !ValidKey(strings.TrimSuffix(prefix, "/")) {
		return nil, fmt.Errorf("objectstore: bad prefix %q", prefix)
	}
	var out []Object
	err := filepath.WalkDir(l.Root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || strings.HasPrefix(entry.Name(), ".") {
			return nil
		}
		rel, err := filepath.Rel(l.Root, path)
		if err != nil {
			return nil
		}
		key := filepath.ToSlash(rel)
		if !strings.HasPrefix(key, prefix) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		out = append(out, Object{Key: key, Size: info.Size(), ModTime: info.ModTime()})
		return nil
	})
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out, err
}

func (l *Local) PresignUpload(ctx context.Context, key, contentType string, ttl time.Duration) (string, error) {
	return "", ErrNoPresign
}

func (l *Local) PresignRead(ctx context.Context, key string, ttl time.Duration) (string, error) {
	return "", ErrNoPresign
}
