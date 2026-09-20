// Package objectstore is where recordings, kept copies, reports and cached
// frames live: a directory on one machine, or Azure Blob containers in the
// cloud. Callers name an object by a key such as "originals/<user>/<id>.mp4"
// and never learn where it is. A store that can hand a browser a URL to
// upload to or read from says so through Presign; one that cannot returns
// ErrNoPresign and the API carries the bytes itself.
package objectstore

import (
	"context"
	"errors"
	"io"
	"strings"
	"time"
)

// ErrNotFound is returned for a key that holds nothing.
var ErrNotFound = errors.New("object not found")

// ErrNoPresign is returned by a store that cannot issue URLs.
var ErrNoPresign = errors.New("this store does not issue URLs")

// Object describes one stored object.
type Object struct {
	Key     string
	Size    int64
	ModTime time.Time
}

// Store is the storage the service and the workers share.
type Store interface {
	// Put writes an object whole; size is its length in bytes, or -1 when
	// unknown, in which case the store may buffer.
	Put(ctx context.Context, key string, body io.Reader, size int64, contentType string) error
	// Open reads an object from the start.
	Open(ctx context.Context, key string) (io.ReadCloser, error)
	// Stat describes an object.
	Stat(ctx context.Context, key string) (Object, error)
	// Delete removes an object; a missing object is not an error.
	Delete(ctx context.Context, key string) error
	// List returns the objects under a prefix, in key order.
	List(ctx context.Context, prefix string) ([]Object, error)
	// PresignUpload returns a URL a client may PUT the object's bytes to for
	// ttl, or ErrNoPresign.
	PresignUpload(ctx context.Context, key, contentType string, ttl time.Duration) (string, error)
	// PresignRead returns a URL a client may GET the object from for ttl,
	// with range requests, or ErrNoPresign.
	PresignRead(ctx context.Context, key string, ttl time.Duration) (string, error)
}

// LocalPaths is implemented by a store whose objects are files on this
// machine, so a caller may serve one with range support straight from disk.
type LocalPaths interface {
	// Path returns the file behind a key, or ErrNotFound.
	Path(key string) (string, error)
}

// ValidKey reports whether a key names one object safely: slash-separated
// segments without "." or "..", no leading slash, no control characters.
func ValidKey(key string) bool {
	if key == "" || len(key) > 1024 || strings.HasPrefix(key, "/") || strings.HasSuffix(key, "/") {
		return false
	}
	for _, segment := range strings.Split(key, "/") {
		if segment == "" || segment == "." || segment == ".." || strings.ContainsAny(segment, "\\\x00") {
			return false
		}
	}
	for _, r := range key {
		if r < 0x20 || r == 0x7f {
			return false
		}
	}
	return true
}
