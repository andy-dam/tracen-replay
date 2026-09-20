package objectstore

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The contract every store keeps: put, stat, open, list, delete, and a
// missing object is ErrNotFound.
func exercise(t *testing.T, s Store) {
	t.Helper()
	ctx := context.Background()
	body := []byte("frame frame frame")
	if err := s.Put(ctx, "originals/u1/rec-1.mp4", bytes.NewReader(body), int64(len(body)), "video/mp4"); err != nil {
		t.Fatal(err)
	}
	if err := s.Put(ctx, "originals/u1/rec-2.mp4", strings.NewReader("x"), 1, "video/mp4"); err != nil {
		t.Fatal(err)
	}
	if err := s.Put(ctx, "reports/r1/report.json", strings.NewReader("{}"), 2, "application/json"); err != nil {
		t.Fatal(err)
	}
	object, err := s.Stat(ctx, "originals/u1/rec-1.mp4")
	if err != nil || object.Size != int64(len(body)) || object.Key != "originals/u1/rec-1.mp4" || object.ModTime.IsZero() {
		t.Fatalf("stat: %+v %v", object, err)
	}
	reader, err := s.Open(ctx, "originals/u1/rec-1.mp4")
	if err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(reader)
	reader.Close()
	if !bytes.Equal(got, body) {
		t.Fatalf("open: %q", got)
	}
	list, err := s.List(ctx, "originals/u1/")
	if err != nil || len(list) != 2 || list[0].Key != "originals/u1/rec-1.mp4" || list[1].Key != "originals/u1/rec-2.mp4" {
		t.Fatalf("list: %+v %v", list, err)
	}
	if list, _ := s.List(ctx, "reports/"); len(list) != 1 {
		t.Fatalf("list reports: %+v", list)
	}
	if err := s.Delete(ctx, "originals/u1/rec-2.mp4"); err != nil {
		t.Fatal(err)
	}
	if err := s.Delete(ctx, "originals/u1/rec-2.mp4"); err != nil {
		t.Fatalf("deleting a missing object is not an error: %v", err)
	}
	if _, err := s.Stat(ctx, "originals/u1/rec-2.mp4"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("stat of a missing object: %v", err)
	}
	if _, err := s.Open(ctx, "originals/u1/nope.mp4"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("open of a missing object: %v", err)
	}
	for _, bad := range []string{"", "/originals/x", "originals/../x", "originals/./x", "originals/x/", "a\x00b"} {
		if err := s.Put(ctx, bad, strings.NewReader("x"), 1, ""); err == nil {
			t.Fatalf("key %q must be refused", bad)
		}
	}
}

func TestLocalStore(t *testing.T) {
	root := t.TempDir()
	s, err := NewLocal(root)
	if err != nil {
		t.Fatal(err)
	}
	exercise(t, s)
	path, err := s.Path("originals/u1/rec-1.mp4")
	if err != nil || filepath.Dir(filepath.Dir(path)) != filepath.Join(root, "originals") {
		t.Fatalf("path: %s %v", path, err)
	}
	if _, err := s.Path("originals/u1/rec-2.mp4"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("path of a deleted object: %v", err)
	}
	if _, err := s.PresignUpload(context.Background(), "originals/u1/x.mp4", "video/mp4", time.Minute); !errors.Is(err, ErrNoPresign) {
		t.Fatalf("a local store issues no URLs: %v", err)
	}
	// A failed write leaves nothing under the key.
	failing := failingReader{}
	if err := s.Put(context.Background(), "originals/u1/broken.mp4", failing, -1, ""); err == nil {
		t.Fatal("a reader error must fail the put")
	}
	if entries, _ := os.ReadDir(filepath.Join(root, "originals", "u1")); len(entries) != 1 {
		t.Fatalf("a failed put must leave no part file: %v", entries)
	}
}

type failingReader struct{}

func (failingReader) Read([]byte) (int, error) { return 0, errors.New("boom") }

func TestValidKey(t *testing.T) {
	for _, good := range []string{"a/b", "originals/u1/rec.mp4", "kept/x/y/z.mp4"} {
		if !ValidKey(good) {
			t.Errorf("%q must be valid", good)
		}
	}
	for _, bad := range []string{"", "/a", "a/", "a//b", "a/../b", ".", "a/.", "a\\b", "a\nb", strings.Repeat("k", 1025)} {
		if ValidKey(bad) {
			t.Errorf("%q must be invalid", bad)
		}
	}
}

// Against the Azurite emulator (or a real account) when a connection string
// is given; the same contract, plus URLs a client can upload to and read from.
func TestAzureStore(t *testing.T) {
	connection := os.Getenv("TRACEN_TEST_AZURE_STORAGE")
	if connection == "" {
		t.Skip("set TRACEN_TEST_AZURE_STORAGE to an Azure Storage connection string (Azurite: UseDevelopmentStorage=true)")
	}
	s, err := NewAzureFromConnectionString(connection)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, key := range []string{"originals/u1/rec-1.mp4", "originals/u1/rec-2.mp4", "reports/r1/report.json", "originals/u1/up.mp4"} {
		s.Delete(ctx, key)
	}
	exercise(t, s)
	upload, err := s.PresignUpload(ctx, "originals/u1/up.mp4", "video/mp4", 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	request, _ := http.NewRequest(http.MethodPut, upload, strings.NewReader("uploaded bytes"))
	request.Header.Set("x-ms-blob-type", "BlockBlob")
	request.Header.Set("Content-Type", "video/mp4")
	response, err := http.DefaultClient.Do(request)
	if err != nil || response.StatusCode != http.StatusCreated {
		t.Fatalf("upload by URL: %v %v", err, response)
	}
	read, err := s.PresignRead(ctx, "originals/u1/up.mp4", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	request, _ = http.NewRequest(http.MethodGet, read, nil)
	request.Header.Set("Range", "bytes=0-7")
	response, err = http.DefaultClient.Do(request)
	if err != nil || response.StatusCode != http.StatusPartialContent {
		t.Fatalf("read by URL with a range: %v %v", err, response)
	}
	got, _ := io.ReadAll(response.Body)
	if string(got) != "uploaded" {
		t.Fatalf("ranged read: %q", got)
	}
	// A URL for one blob opens no other.
	other := strings.Replace(read, "up.mp4", "rec-1.mp4", 1)
	if response, err := http.Get(other); err != nil || response.StatusCode != http.StatusForbidden {
		t.Fatalf("a URL must be bound to its blob: %v %v", err, response)
	}
}
