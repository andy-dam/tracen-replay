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
	"errors"
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
	"github.com/andy-dam/tracen-replay/internal/loadplan"
	"github.com/andy-dam/tracen-replay/internal/maintenance"
	tracenrunner "github.com/andy-dam/tracen-replay/internal/runner"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/updates"
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
	// In a macOS application the executable is Contents/MacOS/<name> and
	// everything it ships sits in Contents/Resources.
	if resources := filepath.Join(root, "..", "Resources"); runtime.GOOS == "darwin" {
		if _, err := os.Stat(filepath.Join(resources, "analyzer", "tracen_replay")); err == nil {
			root = filepath.Clean(resources)
		}
	}
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
	if runtime.GOOS != "windows" {
		// The standalone interpreter the Mac build ships keeps the Unix layout.
		l.python = filepath.Join(root, "python", "bin", "python3")
	}
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
	mu   sync.Mutex
	path string
	gpu  bool
	// chosen is whether the person has set the switch themselves. Until
	// they have, the switch follows what was found: on with a supported
	// accelerator, off without.
	chosen    bool
	available bool
	device    string
	provider  string
	manager   *jobs.Manager

	// parallel and memoryGB are the load the person allows; 0 means they
	// have not said, and the recommendation for this machine is used.
	parallel, memoryGB int
	// onClose is what closing the window does: ask, background or exit.
	onClose string
	// noUpdateCheck turns off the daily question to GitHub about a newer
	// release; the check is on until the person turns it off.
	noUpdateCheck bool
	// pausedDays is how many days a paused analysis keeps its progress.
	// Zero, the default, keeps it for good.
	pausedDays int
	// cores and totalGB are what the machine has.
	cores, totalGB int
}

// saved is the settings file. A field that is absent was never chosen.
type saved struct {
	GPU           *bool  `json:"gpu,omitempty"`
	Parallel      int    `json:"parallel,omitempty"`
	MemoryLimitGB int    `json:"memory_limit_gb,omitempty"`
	OnClose       string `json:"on_close,omitempty"`
	NoUpdateCheck bool   `json:"no_update_check,omitempty"`
	PausedDays    int    `json:"paused_lifetime_days,omitempty"`
}

func (s *settings) load() {
	s.onClose = "ask"
	data, err := os.ReadFile(s.path)
	if err != nil {
		return
	}
	var file saved
	if json.Unmarshal(data, &file) != nil {
		return
	}
	if file.GPU != nil {
		s.gpu, s.chosen = *file.GPU, true
	}
	s.parallel, s.memoryGB, s.noUpdateCheck = file.Parallel, file.MemoryLimitGB, file.NoUpdateCheck
	s.pausedDays = min(max(0, file.PausedDays), api.MaxPausedLifetimeDays)
	if file.OnClose == "background" || file.OnClose == "exit" {
		s.onClose = file.OnClose
	}
}

// save writes what the person has chosen, and only that.
func (s *settings) save() error {
	file := saved{Parallel: s.parallel, MemoryLimitGB: s.memoryGB, NoUpdateCheck: s.noUpdateCheck, PausedDays: s.pausedDays}
	if s.chosen {
		file.GPU = &s.gpu
	}
	if s.onClose != "ask" {
		file.OnClose = s.onClose
	}
	data, _ := json.Marshal(file)
	return os.WriteFile(s.path, data, 0o644)
}

// machine is what the load is planned for.
func (s *settings) machine() loadplan.Machine {
	return loadplan.Machine{MemoryGB: s.totalGB, Cores: s.cores, Accelerated: s.gpu && s.available}
}

// load settings in force: the person's, or the recommendation where they
// have not chosen.
func (s *settings) inForce() (memoryGB, parallel int) {
	memoryGB, parallel = s.machine().Recommend()
	if s.memoryGB > 0 {
		memoryGB = s.machine().ClampMemory(s.memoryGB)
	}
	if s.parallel > 0 {
		parallel = s.parallel
	}
	return memoryGB, parallel
}

func (s *settings) Get() api.Settings {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.view()
}

func (s *settings) view() api.Settings {
	m := s.machine()
	memoryGB, parallel := s.inForce()
	plan := m.Fit(memoryGB, parallel)
	out := api.Settings{GPU: s.gpu && s.available, GPUAvailable: s.available, Device: s.device,
		Parallel: plan.Parallel, MemoryLimitGB: memoryGB, OnClose: s.onClose, UpdateCheck: !s.noUpdateCheck, PausedLifetimeDays: s.pausedDays,
		ParallelMax: m.MaxParallel(memoryGB), Workers: plan.Workers,
		MemoryTotalGB: s.totalGB, MemoryMinGB: loadplan.MinMemoryGB, Cores: s.cores, Background: background}
	out.Recommended.MemoryLimitGB, out.Recommended.Parallel = m.Recommend()
	return out
}

func (s *settings) Change(c api.SettingsChange) (api.Settings, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if c.GPU != nil {
		s.gpu, s.chosen = *c.GPU && s.available, true
	}
	if c.MemoryLimitGB != nil {
		s.memoryGB = s.machine().ClampMemory(*c.MemoryLimitGB)
	}
	if c.Parallel != nil {
		s.parallel = *c.Parallel
	}
	if c.OnClose != nil {
		s.onClose = *c.OnClose
	}
	if c.UpdateCheck != nil {
		s.noUpdateCheck = !*c.UpdateCheck
	}
	if c.PausedLifetimeDays != nil {
		s.pausedDays = min(max(0, *c.PausedLifetimeDays), api.MaxPausedLifetimeDays)
	}
	// A parallel count the memory no longer allows is lowered to what fits,
	// and stays lowered if the memory is raised again later.
	if s.parallel > 0 {
		memoryGB, _ := s.inForce()
		s.parallel = min(s.parallel, s.machine().MaxParallel(memoryGB))
	}
	s.apply()
	return s.view(), s.save()
}

// apply tells the manager which device the next analysis uses, how many
// analyses run at once, how many readers each starts and how long a paused
// analysis is kept.
func (s *settings) apply() {
	device := "cpu"
	if s.gpu && s.available {
		device = s.provider
	}
	s.manager.SetOCRDevice(device)
	memoryGB, parallel := s.inForce()
	plan := s.machine().Fit(memoryGB, parallel)
	s.manager.SetLoad(plan.Parallel, plan.Workers, plan.DenseWorkers)
	s.manager.SetPausedLifetime(time.Duration(s.pausedDays) * 24 * time.Hour)
}

// detect asks the interpreter which ONNX Runtime providers it has and
// keeps the one the switch would turn on.
func (s *settings) detect(python string) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	ask := exec.CommandContext(ctx, python, "-c", "import onnxruntime; print(','.join(onnxruntime.get_available_providers()))")
	quiet(ask)
	out, err := ask.Output()
	providers := strings.TrimSpace(string(out))
	provider, kind := "", ""
	switch {
	case err != nil:
	case strings.Contains(providers, "DmlExecutionProvider"):
		provider, kind = "dml", "DirectX 12"
	case strings.Contains(providers, "CUDAExecutionProvider"):
		provider, kind = "cuda", "CUDA"
	case strings.Contains(providers, "CoreMLExecutionProvider"):
		provider, kind = "coreml", "CoreML"
	}
	// The runtime having a provider is not the machine having the hardware:
	// DirectML is in every Windows build of the runtime and runs in software
	// where there is no card, slower than the processor.
	name, known := "", false
	if provider != "" {
		name, known = hardwareAccelerator(provider)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case err != nil:
		s.device = "the analyzer's runtime could not be asked"
	case provider == "":
		s.device = "CPU only, no supported graphics card"
	case known && name == "":
		s.device = "CPU only, no graphics hardware found"
	case known:
		s.available, s.provider, s.device = true, provider, name+" ("+kind+")"
	default:
		// The machine could not be asked what it has. The switch can be
		// used, but it is not turned on for anyone.
		s.available, s.provider, s.device = true, provider, kind+" Device"
	}
	// Until the person sets the switch themselves it follows what was
	// found: on with real graphics hardware, off without.
	if !s.chosen {
		s.gpu = s.available && known
	}
	s.apply()
}

// App is what the window's loading page can call.
type App struct {
	url string
}

// URL is where the application is served; the loading page goes there.
func (a *App) URL() string { return a.url }

// window is what closing the window does. Closing can end the application
// or leave it running without a window (in the notification area on
// Windows, in the Dock on a Mac), so an analysis is not lost to a click on
// the X. Until the person has chosen, the page asks them.
type window struct {
	mu       sync.Mutex
	ctx      context.Context
	prefs    *settings
	quitting bool
	// asked is when the page was last asked and has not answered yet.
	asked time.Time
	// own is the address of this application's service, with its slash.
	own string
}

func (w *window) show() {
	if w.ctx == nil {
		return
	}
	if runtime.GOOS == "darwin" {
		wailsruntime.Show(w.ctx)
	}
	wailsruntime.WindowUnminimise(w.ctx)
	wailsruntime.WindowShow(w.ctx)
}

func (w *window) hide() {
	if runtime.GOOS == "darwin" {
		wailsruntime.Hide(w.ctx)
		return
	}
	wailsruntime.WindowHide(w.ctx)
}

func (w *window) quit() {
	w.mu.Lock()
	w.quitting = true
	w.mu.Unlock()
	if w.ctx != nil {
		wailsruntime.Quit(w.ctx)
	}
}

// beforeClose answers the window's close button; true keeps the
// application running.
func (w *window) beforeClose(ctx context.Context) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.quitting || w.prefs == nil {
		return false
	}
	switch w.prefs.Get().OnClose {
	case "exit":
		return false
	case "background":
		go w.hide()
		return true
	}
	// The page asks. Should it be unable to (it never loaded, or its script
	// failed), a second press while the question stands closes the
	// application, so the button can never stop working.
	if !w.asked.IsZero() && time.Since(w.asked) < 5*time.Second {
		return false
	}
	w.asked = time.Now()
	wailsruntime.WindowExecJS(ctx, "window.dispatchEvent(new CustomEvent('tracen:close-request'))")
	return true
}

// Open shows an address in the system's browser (api.Desktop). Only a page
// of the project's releases, or a page of this application's own service
// (a worker log), is opened; the page cannot send the person anywhere else.
func (w *window) Open(address string) error {
	w.mu.Lock()
	own := w.own
	w.mu.Unlock()
	if !strings.HasPrefix(address, updates.ReleasePage) && (own == "" || !strings.HasPrefix(address, own)) {
		return errors.New("only a release page or a page of this application is opened")
	}
	if w.ctx == nil {
		return errors.New("the window is not ready")
	}
	wailsruntime.BrowserOpenURL(w.ctx, address)
	return nil
}

// Close is the page's answer (api.Desktop).
func (w *window) Close(action string, remember bool) error {
	w.mu.Lock()
	w.asked = time.Time{}
	w.mu.Unlock()
	if action == "cancel" {
		return nil
	}
	if remember {
		if _, err := w.prefs.Change(api.SettingsChange{OnClose: &action}); err != nil {
			return err
		}
	}
	if action == "exit" {
		go w.quit()
	} else {
		go w.hide()
	}
	return nil
}

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
	win := &window{}
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
			// Starting the application again while it runs without a window
			// brings the window back.
			OnSecondInstanceLaunch: func(options.SecondInstanceData) { win.show() },
		},
		OnBeforeClose: win.beforeClose,
		OnStartup: func(ctx context.Context) {
			win.ctx = ctx
			wailsruntime.WindowSetTitle(ctx, "Tracen Replay")
			url, prefs, stop, err := start(logger, win)
			if err != nil {
				logger.Error("the application could not start", "error", err)
				wailsruntime.MessageDialog(ctx, wailsruntime.MessageDialogOptions{Type: wailsruntime.ErrorDialog,
					Title: "Tracen Replay", Message: "The application could not start: " + err.Error()})
				wailsruntime.Quit(ctx)
				return
			}
			app.url, shutdown = url, stop
			win.mu.Lock()
			win.prefs = prefs
			if cut := strings.Index(url, "/#"); cut > 0 {
				win.own = url[:cut+1]
			}
			win.mu.Unlock()
			startTray(win.show, win.quit)
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
			stopTray()
			if shutdown != nil {
				shutdown()
			}
		},
	})
}

// start brings the service up on a loopback port it picks and returns the
// address the window shows and what stops it all.
func start(logger *slog.Logger, desk api.Desktop) (string, *settings, func(), error) {
	l, err := find()
	if err != nil {
		return "", nil, nil, err
	}
	if err := os.MkdirAll(l.data, 0o755); err != nil {
		return "", nil, nil, err
	}
	// The analyzer calls ffmpeg and ffprobe by name. With the bundled ones
	// first on this process's PATH it finds them on a machine that has no
	// ffmpeg of its own, and uses the same ones on a machine that has.
	if filepath.IsAbs(l.ffmpeg) {
		if _, err := os.Stat(l.ffmpeg); err == nil {
			os.Setenv("PATH", filepath.Dir(l.ffmpeg)+string(os.PathListSeparator)+os.Getenv("PATH"))
		}
	}
	db, err := store.Open(filepath.Join(l.data, "tracen.db"))
	if err != nil {
		return "", nil, nil, err
	}
	ctx, stop := context.WithCancel(context.Background())
	fail := func(err error) (string, *settings, func(), error) {
		stop()
		db.Close()
		return "", nil, nil, err
	}
	// The one person at this machine: a user row, so recordings, jobs and
	// reports have an owner the records can name.
	local := auth.User{ID: "local", Email: "local@this.computer", DisplayName: "This computer", CreatedAt: time.Now()}
	if existing, err := db.UserByID(ctx, "local"); err == nil {
		local = existing
	} else if err := db.CreateUser(ctx, local, ""); err != nil {
		return fail(fmt.Errorf("the local user: %w", err))
	}

	// The manager starts with one analysis and one reader; the settings put
	// the person's load, or the one recommended for this machine, in force
	// before anything can be queued (prefs.apply below).
	manager, err := jobs.NewManager(jobs.Config{DataDir: l.data, Python: l.python, WorkDir: l.analyzer, ModelDir: l.models,
		Workers: 1, DenseWorkers: 1, OCRDevice: "cpu", LearnedReader: l.reader,
		Parallel: 1, QueueLimit: 8, Recordings: db, Logger: logger}, db, tracenrunner.Exec{Logger: logger})
	if err != nil {
		return fail(err)
	}
	prefs := &settings{path: filepath.Join(l.data, "settings.json"), manager: manager,
		cores: runtime.NumCPU(), totalGB: int(totalMemoryBytes() >> 30)}
	prefs.load()
	prefs.apply()
	go prefs.detect(l.python)
	if _, err := manager.Recover(ctx); err != nil {
		return fail(err)
	}
	// An analysis that ran to the end and whose result could not be read
	// (a stray line on the worker's standard output) has its report taken
	// from the output it wrote, rather than being run again.
	go func() {
		if n := manager.RescueAll(ctx, local.ID); n > 0 {
			logger.Info("finished analyses recovered", "count", n)
		}
	}()
	go manager.Run(ctx)

	// Someone who installed this build has to replace it by hand, so they
	// are told when a newer release exists: on the Runs page and in
	// Settings. Once a day, one request to GitHub, which Settings turns off.
	// A build that is not a release (a test build) never asks.
	releases := &updates.Checker{Current: version, Log: logger, Enabled: func() bool { return prefs.Get().UpdateCheck }}
	go releases.Run(ctx)

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
		AllowedHosts: []string{"127.0.0.1"}, Settings: prefs, Desktop: desk, UploadLimit: 16 << 30, LocalUser: local,
		Quota: api.Quota{MaxDuration: 0, MaxPixels: 4096 * 2304, MaxFPS: 120},
		Version: func() api.VersionInfo {
			info := api.VersionInfo{Version: version}
			info.Latest, info.URL = releases.Available()
			return info
		},
		Probe: func(ctx context.Context, path string) (artifacts.Media, error) {
			return artifacts.ProbeMedia(ctx, l.ffprobe, path)
		}})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	go server.Serve(listener)
	logger.Info("tracen desktop", "url", "http://"+addr, "data", l.data, "version", version)
	return "http://" + addr + "/#/runs", prefs, func() {
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
