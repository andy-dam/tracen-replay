// Command tracen-desktop is Tracen Replay as a desktop application: one
// window, no accounts, no port to know. The service that the website runs
// starts inside the process on a loopback port it picks itself, with its
// data under the user's application folder, and the window shows it. The
// analyzer, its models and ffmpeg live next to the executable, where the
// installer puts them.
//go:build windows || darwin

package main

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	wailsruntime "github.com/wailsapp/wails/v2/pkg/runtime"

	"github.com/andy-dam/tracen-replay/internal/api"
	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/maintenance"
	tracenrunner "github.com/andy-dam/tracen-replay/internal/runner"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/webassets"
)

//go:embed all:frontend
var frontend embed.FS

// version is set by the linker from the release tag.
var version = "dev"

// layout is where the pieces are, next to the executable.
type layout struct{ root, python, analyzer, models, ffmpeg, ffprobe, reader, data string }

func find() (layout, error) {
	exe, err := os.Executable()
	if err != nil {
		return layout{}, err
	}
	root := filepath.Dir(exe)
	if _, err := os.Stat(filepath.Join(root, "analyzer", "tracen_replay")); err != nil {
		// A development run from the repository: the checked-out tree.
		if wd, err := os.Getwd(); err == nil {
			if _, err := os.Stat(filepath.Join(wd, "analyzer", "tracen_replay")); err == nil {
				root = wd
			}
		}
	}
	l := layout{root: root, analyzer: filepath.Join(root, "analyzer"), models: filepath.Join(root, "models"),
		ffmpeg: filepath.Join(root, "ffmpeg", "ffmpeg"+exeSuffix()), ffprobe: filepath.Join(root, "ffmpeg", "ffprobe"+exeSuffix()),
		reader: filepath.Join(root, "analyzer", "tracen_replay", "data", "reader.onnx")}
	l.python = filepath.Join(root, "python", "python"+exeSuffix())
	if _, err := os.Stat(l.python); err != nil {
		// The repository's virtual environment, for development.
		l.python = filepath.Join(root, ".venv", "Scripts", "python.exe")
		if runtime.GOOS != "windows" {
			l.python = filepath.Join(root, ".venv", "bin", "python")
		}
		l.models = filepath.Join(root, ".local", "models", "rapidocr")
		l.ffmpeg, l.ffprobe = "ffmpeg", "ffprobe"
	}
	if _, err := os.Stat(l.reader); err != nil {
		l.reader = ""
	}
	base, err := os.UserConfigDir()
	if err != nil {
		base = root
	}
	l.data = filepath.Join(base, "TracenReplay")
	return l, nil
}

func exeSuffix() string {
	if runtime.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

// settings is the desktop's one choice, kept as a file in the data folder,
// and the manager it applies to.
type settings struct {
	mu        sync.Mutex
	path      string
	gpu       bool
	available bool
	device    string
	provider  string
	manager   *jobs.Manager
}

func (s *settings) load() {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return
	}
	var saved struct {
		GPU bool `json:"gpu"`
	}
	if json.Unmarshal(data, &saved) == nil {
		s.gpu = saved.GPU
	}
}

func (s *settings) Get() api.Settings {
	s.mu.Lock()
	defer s.mu.Unlock()
	return api.Settings{GPU: s.gpu && s.available, GPUAvailable: s.available, Device: s.device}
}

func (s *settings) Set(v api.Settings) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.gpu = v.GPU && s.available
	s.apply()
	data, _ := json.Marshal(map[string]bool{"gpu": s.gpu})
	return os.WriteFile(s.path, data, 0o644)
}

// apply tells the manager which device the next analysis uses.
func (s *settings) apply() {
	device := "cpu"
	if s.gpu && s.available {
		device = s.provider
	}
	s.manager.SetOCRDevice(device)
}

// detect asks the interpreter which ONNX Runtime providers it has and
// keeps the one the switch would turn on.
func (s *settings) detect(python string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, python, "-c", "import onnxruntime; print(','.join(onnxruntime.get_available_providers()))").Output()
	s.mu.Lock()
	defer s.mu.Unlock()
	providers := strings.TrimSpace(string(out))
	switch {
	case err != nil:
		s.device = "the analyzer's runtime could not be asked"
	case strings.Contains(providers, "DmlExecutionProvider"):
		s.available, s.provider, s.device = true, "dml", "a DirectX 12 graphics card"
	case strings.Contains(providers, "CUDAExecutionProvider"):
		s.available, s.provider, s.device = true, "cuda", "an NVIDIA graphics card (CUDA)"
	case strings.Contains(providers, "CoreMLExecutionProvider"):
		s.available, s.provider, s.device = true, "coreml", "Apple silicon (CoreML)"
	default:
		s.device = "CPU only, no supported graphics card"
	}
	s.apply()
}

// App is what the window's loading page can call.
type App struct {
	url string
}

// URL is where the application is served; the loading page goes there.
func (a *App) URL() string { return a.url }

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stderr, nil))
	slog.SetDefault(logger)
	if err := run(logger); err != nil {
		fmt.Fprintln(os.Stderr, "tracen-desktop:", err)
		os.Exit(1)
	}
}

// run opens the window. Nothing touches the data folder here: the build
// tool runs this program once to read its bindings, and a second launch
// must not either, so the service starts only when a real window does
// (OnStartup), behind a single-instance lock. A startup that marked
// unfinished jobs interrupted from any other place once stamped a live
// analysis as interrupted.
func run(logger *slog.Logger) error {
	app := &App{}
	var windowCtx context.Context
	var shutdown func()
	loading, _ := fs.Sub(frontend, "frontend")
	return wails.Run(&options.App{
		Title:            "Tracen Replay",
		Width:            1320,
		Height:           880,
		MinWidth:         960,
		MinHeight:        640,
		BackgroundColour: &options.RGBA{R: 15, G: 18, B: 24, A: 1},
		AssetServer:      &assetserver.Options{Assets: loading},
		Bind:             []any{app},
		SingleInstanceLock: &options.SingleInstanceLock{
			UniqueId: "tracen-replay-desktop-0f6b2c9e",
			OnSecondInstanceLaunch: func(options.SecondInstanceData) {
				if windowCtx != nil {
					wailsruntime.WindowUnminimise(windowCtx)
					wailsruntime.WindowShow(windowCtx)
				}
			},
		},
		OnStartup: func(ctx context.Context) {
			windowCtx = ctx
			wailsruntime.WindowSetTitle(ctx, "Tracen Replay")
			url, stop, err := start(logger)
			if err != nil {
				logger.Error("the application could not start", "error", err)
				wailsruntime.MessageDialog(ctx, wailsruntime.MessageDialogOptions{Type: wailsruntime.ErrorDialog,
					Title: "Tracen Replay", Message: "The application could not start: " + err.Error()})
				wailsruntime.Quit(ctx)
				return
			}
			app.url, shutdown = url, stop
		},
		// The service is listening by the time the window's page is up, and
		// the window goes there. Navigating from Go avoids a cross-origin
		// request from the loading page, which the engine would refuse.
		OnDomReady: func(ctx context.Context) {
			if app.url != "" {
				wailsruntime.WindowExecJS(ctx, "window.location.replace("+jsString(app.url)+")")
			}
		},
		OnShutdown: func(context.Context) {
			if shutdown != nil {
				shutdown()
			}
		},
	})
}

// start brings the service up on a loopback port it picks and returns the
// address the window shows and what stops it all.
func start(logger *slog.Logger) (string, func(), error) {
	l, err := find()
	if err != nil {
		return "", nil, err
	}
	if err := os.MkdirAll(l.data, 0o755); err != nil {
		return "", nil, err
	}
	db, err := store.Open(filepath.Join(l.data, "tracen.db"))
	if err != nil {
		return "", nil, err
	}
	ctx, stop := context.WithCancel(context.Background())
	fail := func(err error) (string, func(), error) {
		stop()
		db.Close()
		return "", nil, err
	}
	// The one person at this machine: a user row, so recordings, jobs and
	// reports have an owner the records can name.
	local := auth.User{ID: "local", Email: "local@this.computer", DisplayName: "This computer", CreatedAt: time.Now()}
	if existing, err := db.UserByID(ctx, "local"); err == nil {
		local = existing
	} else if err := db.CreateUser(ctx, local, ""); err != nil {
		return fail(fmt.Errorf("the local user: %w", err))
	}

	workers := max(2, runtime.NumCPU()/2)
	manager, err := jobs.NewManager(jobs.Config{DataDir: l.data, Python: l.python, WorkDir: l.analyzer, ModelDir: l.models,
		Workers: min(workers, 4), DenseWorkers: max(1, min(workers, 4)-1), OCRDevice: "cpu", LearnedReader: l.reader,
		Parallel: 1, QueueLimit: 8, Recordings: db, Logger: logger}, db, tracenrunner.Exec{Logger: logger})
	if err != nil {
		return fail(err)
	}
	prefs := &settings{path: filepath.Join(l.data, "settings.json"), manager: manager}
	prefs.load()
	go prefs.detect(l.python)
	if _, err := manager.Recover(ctx); err != nil {
		return fail(err)
	}
	go manager.Run(ctx)

	recordingsDir := filepath.Join(l.data, "recordings")
	os.MkdirAll(recordingsDir, 0o755)
	frames := artifacts.Frames{FFmpeg: l.ffmpeg, CacheDir: filepath.Join(l.data, "frames"), Gate: artifacts.NewGate(2), MaxCacheBytes: 2 << 30}
	go maintenance.Sweeper{Store: db, RecordingsDir: recordingsDir, Frames: frames, Logger: logger}.Run(ctx)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return fail(err)
	}
	addr := listener.Addr().String()
	ready := func() []api.Check {
		return []api.Check{
			check("python", l.python, func() error { _, err := os.Stat(l.python); return err }),
			check("analyzer", l.analyzer, func() error {
				_, err := os.Stat(filepath.Join(l.analyzer, "tracen_replay", "analysis_job.py"))
				return err
			}),
			check("model-dir", l.models, func() error { _, err := os.Stat(filepath.Join(l.models, "PP-OCRv6_det_small.onnx")); return err }),
			check("ffmpeg", l.ffmpeg, func() error { _, err := exec.LookPath(l.ffmpeg); return err }),
			{Name: "ocr-device", OK: true, Note: prefs.Get().Device},
		}
	}
	handler := api.New(api.Config{Jobs: manager, Reports: db, Recordings: db, Corrections: db, RecordingsDir: recordingsDir,
		ArtifactsDir: filepath.Join(l.data, "jobs"), Frames: frames, Ready: ready, Logger: logger, Static: webassets.Handler(),
		AllowedHosts: []string{"127.0.0.1"}, Settings: prefs, UploadLimit: 16 << 30, LocalUser: local,
		Quota:   api.Quota{MaxDuration: 0, MaxPixels: 4096 * 2304, MaxFPS: 120},
		Version: func() api.VersionInfo { return api.VersionInfo{Version: version} },
		Probe: func(ctx context.Context, path string) (artifacts.Media, error) {
			return artifacts.ProbeMedia(ctx, l.ffprobe, path)
		}})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	go server.Serve(listener)
	logger.Info("tracen desktop", "url", "http://"+addr, "data", l.data, "version", version)
	return "http://" + addr + "/#/runs", func() {
		stop()
		server.Close()
		db.Close()
	}, nil
}

// jsString quotes a string for a script.
func jsString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func check(name, note string, probe func() error) api.Check {
	if err := probe(); err != nil {
		return api.Check{Name: name, OK: false, Note: note + ": " + err.Error()}
	}
	return api.Check{Name: name, OK: true, Note: note}
}
