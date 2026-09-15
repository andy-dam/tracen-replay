//go:build windows

package runner

import (
	"os/exec"
	"strconv"
	"unsafe"

	"golang.org/x/sys/windows"
)

func configureTree(cmd *exec.Cmd) {}

// tree is a Windows job object holding the worker and every process it
// creates. The job is created with KILL_ON_JOB_CLOSE, so the whole tree ends
// when the handle closes: on cancel, on a normal exit, and also when this
// process dies without running any cleanup (a crash or a forced kill), which
// is the case taskkill alone cannot cover.
type tree struct {
	job windows.Handle
}

// adoptTree assigns a started child to a fresh job object. Processes the
// child creates after the assignment inherit the job; the assignment happens
// right after Start, before the interpreter has finished loading.
func adoptTree(cmd *exec.Cmd) (*tree, error) {
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return nil, err
	}
	var info windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err := windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
		windows.CloseHandle(job)
		return nil, err
	}
	process, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err != nil {
		windows.CloseHandle(job)
		return nil, err
	}
	defer windows.CloseHandle(process)
	if err := windows.AssignProcessToJobObject(job, process); err != nil {
		windows.CloseHandle(job)
		return nil, err
	}
	return &tree{job: job}, nil
}

// kill ends every process of the job and, as a fallback for a child that
// could not be assigned, walks the tree with taskkill, which is part of every
// Windows installation.
func (t *tree) kill(cmd *exec.Cmd) {
	if t != nil && t.job != 0 {
		windows.TerminateJobObject(t.job, 1)
	}
	killTree(cmd)
}

// close releases the job; with KILL_ON_JOB_CLOSE any process still in it ends.
func (t *tree) close() {
	if t != nil && t.job != 0 {
		windows.CloseHandle(t.job)
		t.job = 0
	}
}

func killTree(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid)).Run()
}

func alive(pid int) bool {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(handle)
	var code uint32
	if err := windows.GetExitCodeProcess(handle, &code); err != nil {
		return false
	}
	return code == 259 // STILL_ACTIVE
}
