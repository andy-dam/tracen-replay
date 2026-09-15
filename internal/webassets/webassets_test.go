package webassets

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHandlerServesTheClientOrTheNotBuiltPage(t *testing.T) {
	h := Handler()
	for _, path := range []string{"/", "/reports/abc", "/index.html"} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "Tracen Replay") {
			t.Fatalf("%s: %d %q", path, rec.Code, rec.Body.String()[:min(80, rec.Body.Len())])
		}
	}
}
