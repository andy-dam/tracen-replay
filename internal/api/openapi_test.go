package api

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The spec is prose-heavy: nearly every summary and description is a sentence.
// An unquoted YAML scalar that contains a colon followed by a space is read as
// a mapping, which makes the whole document unparseable for anyone generating
// a client from it. Nothing in the service loads the file, so a parser would
// not notice; this guards the one mistake that is easy to make while editing
// it. A quoted or flow-style value may contain whatever it likes.
var proseScalar = regexp.MustCompile(`^\s*(summary|description):\s+([^"'{\[\s].*)$`)

func TestOpenAPIProseDoesNotBreakTheDocument(t *testing.T) {
	path := filepath.Join("..", "..", "api", "openapi.yaml")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for number, line := range strings.Split(string(body), "\n") {
		match := proseScalar.FindStringSubmatch(strings.TrimRight(line, "\r"))
		if match == nil {
			continue
		}
		if strings.Contains(match[2], ": ") {
			t.Errorf("api/openapi.yaml:%d: %s must be quoted; an unquoted value containing %q parses as a mapping\n  %s",
				number+1, match[1], ": ", strings.TrimSpace(line))
		}
	}
}
