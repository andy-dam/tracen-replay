package webassets

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A client served from a directory follows the embedded handler's rules and
// picks up a build that lands after the service started.
func TestDirHandlerServesAndFallsBack(t *testing.T) {
	dir := t.TempDir()
	h := DirHandler(dir)
	get := func(path string) *httptest.ResponseRecorder {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", path, nil))
		return rec
	}
	if rec := get("/"); rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "not been built") {
		t.Fatalf("empty directory: %d %s", rec.Code, rec.Body.String()[:60])
	}
	os.MkdirAll(filepath.Join(dir, "assets"), 0o755)
	os.WriteFile(filepath.Join(dir, "index.html"), []byte("<html>app</html>"), 0o644)
	os.WriteFile(filepath.Join(dir, "assets", "a.js"), []byte("js"), 0o644)
	if rec := get("/"); rec.Code != http.StatusOK || rec.Body.String() != "<html>app</html>" {
		t.Fatalf("index: %d %q", rec.Code, rec.Body.String())
	}
	if rec := get("/assets/a.js"); rec.Code != http.StatusOK || rec.Body.String() != "js" || !strings.Contains(rec.Header().Get("Cache-Control"), "immutable") {
		t.Fatalf("asset: %d %q %q", rec.Code, rec.Body.String(), rec.Header().Get("Cache-Control"))
	}
	if rec := get("/reports/abc"); rec.Code != http.StatusOK || rec.Body.String() != "<html>app</html>" {
		t.Fatalf("deep link fallback: %d %q", rec.Code, rec.Body.String())
	}
}
