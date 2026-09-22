package loadplan

import "testing"

func TestRecommendation(t *testing.T) {
	for _, c := range []struct {
		name             string
		machine          Machine
		memory, parallel int
	}{
		{"a 32 GB desktop with a graphics card runs two", Machine{MemoryGB: 31, Cores: 12, Accelerated: true}, 23, 2},
		{"16 GB has room for one", Machine{MemoryGB: 16, Cores: 8, Accelerated: true}, 10, 1},
		{"8 GB still gets the least that works", Machine{MemoryGB: 8, Cores: 4}, 7, 1},
		{"plenty of memory but four logical processors runs one", Machine{MemoryGB: 64, Cores: 4}, 48, 1},
		{"memory that could not be read", Machine{Cores: 8}, 8, 1},
	} {
		memory, parallel := c.machine.Recommend()
		if memory != c.memory || parallel != c.parallel {
			t.Errorf("%s: recommended %d GB and %d at once, wanted %d GB and %d", c.name, memory, parallel, c.memory, c.parallel)
		}
	}
}

func TestFitStaysWithinTheLimit(t *testing.T) {
	m := Machine{MemoryGB: 31, Cores: 12, Accelerated: true}
	for limit := MinMemoryGB; limit <= 31; limit++ {
		for asked := 1; asked <= 8; asked++ {
			plan := m.Fit(limit, asked)
			if plan.Parallel < 1 || plan.Parallel > asked || plan.Workers < 1 || plan.Workers > maxReaders || plan.DenseWorkers < 1 {
				t.Fatalf("limit %d, asked %d: %+v", limit, asked, plan)
			}
			if need := plan.Parallel * m.NeedMB(plan.Workers); need > limit*1024 {
				t.Fatalf("limit %d GB, asked %d: the plan %+v needs %d MB", limit, asked, plan, need)
			}
			if plan.Parallel > m.MaxParallel(limit) {
				t.Fatalf("limit %d: %d at once is more than the most allowed, %d", limit, plan.Parallel, m.MaxParallel(limit))
			}
		}
	}
}

func TestFitSharesTheProcessor(t *testing.T) {
	m := Machine{MemoryGB: 64, Cores: 12, Accelerated: true}
	// Half the logical processors, not more, and never more than the
	// analyzer takes.
	if one, three := m.Fit(60, 1), m.Fit(60, 3); one.Workers != 6 || one.DenseWorkers != 5 || three.Workers != 2 || three.Parallel != 3 {
		t.Fatalf("one at once: %+v; three at once: %+v", one, three)
	}
	if many := (Machine{MemoryGB: 256, Cores: 64, Accelerated: true}).Fit(200, 1); many.Workers != maxReaders || maxReaders != 8 {
		t.Fatalf("a large machine: %+v (cap %d)", many, maxReaders)
	}
	// A limit below the least that works is raised to it; above the machine,
	// lowered to it.
	if m.ClampMemory(1) != MinMemoryGB || m.ClampMemory(500) != 64 {
		t.Fatalf("clamped to %d and %d", m.ClampMemory(1), m.ClampMemory(500))
	}
}
