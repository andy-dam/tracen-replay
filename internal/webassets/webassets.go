// Package webassets embeds the built browser client (web/, built by Vite
// into dist/) and serves it with a single-page fallback.
package webassets

import (
	"embed"
	"io/fs"
	"net/http"
	"os"
	"path"
	"strings"
)

//go:embed all:dist
var dist embed.FS

// Handler serves the embedded client. Paths that do not name a file fall
// back to index.html so hash-free deep links and reloads keep working; API
// paths are never routed here.
func Handler() http.Handler {
	sub, err := fs.Sub(dist, "dist")
	if err != nil {
		panic(err)
	}
	return serve(sub)
}

// DirHandler serves a built client from a directory on disk with the same
// rules as the embedded one, so a rebuilt client is picked up on the next
// page load without restarting the service.
func DirHandler(dir string) http.Handler {
	return serve(os.DirFS(dir))
}

func serve(sub fs.FS) http.Handler {
	files := http.FS(sub)
	server := http.FileServer(files)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Checked per request: a directory can be built after the service started.
		if f, err := sub.Open("index.html"); err != nil {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(notBuilt))
			return
		} else {
			f.Close()
		}
		name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
		if name == "" || name == "index.html" {
			// Serve the entry page directly; http.FileServer would redirect
			// /index.html to / otherwise.
			r2 := r.Clone(r.Context())
			r2.URL.Path = "/"
			w.Header().Set("Cache-Control", "no-cache")
			server.ServeHTTP(w, r2)
			return
		}
		if f, err := sub.Open(name); err == nil {
			f.Close()
			if strings.HasPrefix(name, "assets/") {
				w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
			}
			server.ServeHTTP(w, r)
			return
		}
		r2 := r.Clone(r.Context())
		r2.URL.Path = "/"
		w.Header().Set("Cache-Control", "no-cache")
		server.ServeHTTP(w, r2)
	})
}

const notBuilt = `<!doctype html><html lang="en"><head><meta charset="UTF-8"><title>Tracen Replay</title></head>
<body><p>The browser client has not been built. Run <code>npm install &amp;&amp; npm run build</code> in <code>web/</code>, then rebuild the binary.</p>
<p>The API is available under <a href="/api/reports">/api</a>.</p></body></html>`
