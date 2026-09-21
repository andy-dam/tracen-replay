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

// StageOrder is the analyzer's stages in the order an analysis first reaches
// them; every finished analysis on record follows it, some without the
// optional ones. The analyzer also names a stage again when a later step
// reuses it (receipt_inspection after assemble), and names a few that are
// not steps at all, so a job's recorded stage only ever moves forward along
// this list (StageRank): the progress a viewer sees never steps back. The
// client lays its progress bar out from the same list (web/src/phases.ts),
// and a test keeps the two equal.
var StageOrder = []string{
	"capture", "ocr",
	"base_readings", "automatic_refinement", "race_quantity_refinement", "currency_refinement",
	"currency_refinement_complete", "currency_padding_refinement_complete", "reload_readings",
	"hint_card_preparation", "inspections_merged",
	"receipt_inspection", "numeric_receipt_recovery", "training_gain_recovery", "inspection_loads",
	"occluded_receipt_recovery",
	"assemble", "boundary_state_recovery", "assemble_after_boundary", "validate_output",
	"save_report", "timeline_document", "viewer", "complete",
}

// StageRank is a stage's place in StageOrder, or -1 for a name that is not
// one of its steps.
func StageRank(stage string) int {
	for i, name := range StageOrder {
		if name == stage {
			return i
		}
	}
	return -1
}

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
