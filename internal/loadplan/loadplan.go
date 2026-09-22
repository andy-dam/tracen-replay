// Package loadplan decides how much of a machine the analyses use: how many
// run at once and how many reader processes each starts, within a memory
// limit the person sets. The limit is a plan, not a fence: nothing is killed
// for crossing it. What an analysis needs was measured on a real recording
// (docs/ocr-performance.md, "Memory"), so keeping the plan under the limit
// keeps the analyses under it.
package loadplan

// Machine is what the computer has.
type Machine struct {
	// MemoryGB is the installed memory; 0 when it could not be read.
	MemoryGB int
	// Cores is the number of logical processors.
	Cores int
	// Accelerated says the analyses read frames on graphics hardware. Each
	// reader is then a process of its own with its own copy of the runtime.
	Accelerated bool
}

// What one analysis commits at its peak, in MB. Measured on 1080p
// recordings (2026-09-21, this repository's analyzer on Windows): the
// analyzer's own process reaches 3.4 GB assembling a full career and 4.8 GB
// writing the viewer page, a reader process holds about half a gigabyte,
// and the whole tree committed 6.5 GB with three readers and 8.4 GB in the
// worst run seen. The readers are the small part, so the number of analyses
// is what the memory limit really decides.
const (
	baseMB              = 6000
	readerMB            = 500
	readerAcceleratedMB = 600
	// reserveGB is left to the system and whatever else is open when a limit
	// is recommended.
	reserveGB = 6
	// MinMemoryGB is the smallest limit that still runs one analysis with
	// one reader.
	MinMemoryGB = 7
	// maxReaders is the most the analyzer itself accepts (--workers 1 to 8).
	// What an analysis really gets is decided by the memory limit and by the
	// processor: half the logical processors, so the machine stays usable.
	// Measured on this repository's 12-thread machine with a graphics card:
	// three readers analyze a 35-minute career in 32 minutes, five in 24
	// (docs/ocr-performance.md).
	maxReaders = 8
)

// Plan is what the manager is told.
type Plan struct {
	// Parallel is how many analyses run at once.
	Parallel int
	// Workers is the number of readers each analysis starts, and
	// DenseWorkers the number it uses for the 60 fps re-reads.
	Workers, DenseWorkers int
}

func (m Machine) readerMB() int {
	if m.Accelerated {
		return readerAcceleratedMB
	}
	return readerMB
}

// NeedMB is the memory one analysis with this many readers holds at its peak.
func (m Machine) NeedMB(readers int) int { return baseMB + readers*m.readerMB() }

// MaxParallel is the most analyses the limit and the processor allow: each
// needs room for at least one reader, and two logical processors.
func (m Machine) MaxParallel(memoryLimitGB int) int {
	byMemory := memoryLimitGB * 1024 / m.NeedMB(1)
	byCores := max(1, m.Cores/2)
	return max(1, min(byMemory, byCores, 8))
}

// Fit turns the two settings into a plan that stays within them: the
// parallel count is lowered to what fits, and each analysis gets as many
// readers as its share of the memory and of the processor allows. An
// analysis that starts while none other runs is planned with parallel 1, so
// a lone analysis uses the whole share rather than the share it would have
// had to leave for an analysis that is not there (Manager.Config.Readers).
func (m Machine) Fit(memoryLimitGB, parallel int) Plan {
	memoryLimitGB = m.ClampMemory(memoryLimitGB)
	parallel = max(1, min(parallel, m.MaxParallel(memoryLimitGB)))
	shareMB := memoryLimitGB * 1024 / parallel
	byMemory := (shareMB - baseMB) / m.readerMB()
	byCores := max(1, m.Cores/(2*parallel))
	workers := max(1, min(byMemory, byCores, maxReaders))
	return Plan{Parallel: parallel, Workers: workers, DenseWorkers: max(1, workers-1)}
}

// ClampMemory keeps a limit between the least that works and what the
// machine has.
func (m Machine) ClampMemory(memoryLimitGB int) int {
	top := m.MemoryGB
	if top <= 0 {
		top = 64
	}
	return max(MinMemoryGB, min(memoryLimitGB, top))
}

// Recommend is the setting suggested for this machine: memory up to what is
// left after a reserve for everything else, and two analyses at once where
// that leaves each of them a full set of readers, otherwise one.
func (m Machine) Recommend() (memoryLimitGB, parallel int) {
	if m.MemoryGB <= 0 {
		return m.ClampMemory(8), 1
	}
	memoryLimitGB = m.ClampMemory(min(m.MemoryGB-reserveGB, m.MemoryGB*3/4))
	parallel = 1
	if two := m.Fit(memoryLimitGB, 2); two.Parallel == 2 && two.Workers >= 3 {
		parallel = 2
	}
	return memoryLimitGB, parallel
}
