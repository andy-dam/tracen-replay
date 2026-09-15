package worker

import (
	"encoding/json"
	"strings"
)

// Progress is one JSON progress line from the worker's stderr.
//
// Stage boundaries arrive as {"stage":"stage_done","name":...,"wall_s":...}
// with memory fields when the worker has psutil; a failed enrichment stage as
// {"stage":"stage_failed","name":...,"error":...}; the OCR pass as
// {"stage":"ocr","processed":n,"total":m,...}, the only line with a meaningful
// percentage. Other stage values (for example "receipt_inspection" or
// "replay_loaded") are informational.
type Progress struct {
	Stage       string  `json:"stage"`
	Name        string  `json:"name,omitempty"`
	WallSeconds float64 `json:"wall_s,omitempty"`
	RSSMB       int     `json:"rss_mb,omitempty"`
	PeakRSSMB   int     `json:"peak_rss_mb,omitempty"`
	Processed   int     `json:"processed,omitempty"`
	Total       int     `json:"total,omitempty"`
	Error       string  `json:"error,omitempty"`
}

// Stage values with a defined meaning.
const (
	StageDone   = "stage_done"
	StageFailed = "stage_failed"
	StageOCR    = "ocr"
)

// ParseProgress decodes one stderr line. It returns false for anything that
// is not a JSON object carrying a "stage" field: library warnings, tracebacks
// and blank lines are expected on the same stream and are not errors.
func ParseProgress(line string) (Progress, bool) {
	line = strings.TrimSpace(line)
	if !strings.HasPrefix(line, "{") {
		return Progress{}, false
	}
	var p Progress
	if err := json.Unmarshal([]byte(line), &p); err != nil || p.Stage == "" {
		return Progress{}, false
	}
	return p, true
}

// Percent reports completion for the OCR pass; every other line has no
// meaningful numerator and denominator and reports false.
func (p Progress) Percent() (float64, bool) {
	if p.Stage != StageOCR || p.Total <= 0 {
		return 0, false
	}
	return 100 * float64(p.Processed) / float64(p.Total), true
}

// Label is a short human-readable description for a status display: the
// stage name for boundaries, "ocr" for the OCR pass, else the raw stage.
func (p Progress) Label() string {
	switch p.Stage {
	case StageDone, StageFailed:
		if p.Name != "" {
			return p.Name
		}
	}
	return p.Stage
}
