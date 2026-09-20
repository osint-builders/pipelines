package main

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

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
