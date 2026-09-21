package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	"io/fs"
	"strings"
	"testing"
)

type trackedBundleFS struct {
	fs.FS
	randomAccess bool
	closed       bool
	reads        int
}

type trackedBundleFile struct {
	fs.File
	owner *trackedBundleFS
}

func (f *trackedBundleFile) Read(p []byte) (int, error) {
	f.owner.reads++
	if f.owner.randomAccess {
		return 0, errors.New("the embedded bundle must not be copied")
	}
	return f.File.Read(p)
}

func (f *trackedBundleFile) Close() error {
	f.owner.closed = true
	return f.File.Close()
}

type randomBundleFile struct {
	*trackedBundleFile
	io.ReaderAt
}

func (f *trackedBundleFS) Open(name string) (fs.File, error) {
	file, err := f.FS.Open(name)
	if err != nil {
		return nil, err
	}
	tracked := &trackedBundleFile{file, f}
	if f.randomAccess {
		body, err := fs.ReadFile(f.FS, name)
		if err != nil {
			_ = file.Close()
			return nil, err
		}
		return &randomBundleFile{tracked, bytes.NewReader(body)}, nil
	}
	return tracked, nil
}

func TestBundleUsesRandomAccessAndClosesFile(t *testing.T) {
	var expected bytes.Buffer
	if err := runWithFiles(context.Background(), []string{"info"}, &expected, legacyBundle(t)); err != nil {
		t.Fatal(err)
	}
	for _, randomAccess := range []bool{true, false} {
		files := &trackedBundleFS{FS: legacyBundle(t), randomAccess: randomAccess}
		var output bytes.Buffer
		if err := runWithFiles(context.Background(), []string{"info"}, &output, files); err != nil {
			t.Fatal(err)
		}
		if !files.closed || (randomAccess && files.reads != 0) || (!randomAccess && files.reads == 0) {
			t.Fatalf("random access %v: closed=%v reads=%d", randomAccess, files.closed, files.reads)
		}
		if output.String() != expected.String() {
			t.Fatal("bundle reader changed command output")
		}
	}
}

func TestHelpWorksWithoutDatasetOrModel(t *testing.T) {
	var output bytes.Buffer
	if err := run(context.Background(), []string{"--help"}, &output); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), "offline") {
		t.Fatal(output.String())
	}
}

func TestBadArgumentsFailBeforeLoadingModel(t *testing.T) {
	for _, args := range [][]string{{"unknown"}, {"search", ""}, {"search", "--limit", "0", "radar"}, {"get", "--format", "pdf", "id"}, {"search", "--mode", "keyword", "radar"}, {"search", "unquoted", "query"}} {
		if err := run(context.Background(), args, &bytes.Buffer{}); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
}
