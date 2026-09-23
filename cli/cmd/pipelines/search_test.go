package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"reflect"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
)

func TestQueryOptionsCanAppearBeforeOrAfterWords(t *testing.T) {
	for _, args := range [][]string{
		{"--mode", "vector", "--page", "2", "--raw", "airborne", "radar"},
		{"airborne", "radar", "--page=2", "--raw", "--mode=vector"},
		{"airborne", "--mode", "vector", "radar", "--raw=true", "--page", "2"},
	} {
		flags := flag.NewFlagSet("search", flag.ContinueOnError)
		mode := flags.String("mode", "hybrid", "")
		page := flags.Int("page", 1, "")
		raw := flags.Bool("raw", false, "")
		if err := parseFlags(flags, args); err != nil {
			t.Fatal(err)
		}
		if *mode != "vector" || *page != 2 || !*raw || strings.Join(flags.Args(), " ") != "airborne radar" {
			t.Fatalf("wrong parsed query: %v", args)
		}
	}
	flags := flag.NewFlagSet("search", flag.ContinueOnError)
	if err := parseFlags(flags, []string{"--", "--literal", "-words"}); err != nil || !reflect.DeepEqual(flags.Args(), []string{"--literal", "-words"}) {
		t.Fatal(flags.Args(), err)
	}
}

func TestSearchValidationAndFocusedHelpDoNotLoadModels(t *testing.T) {
	for _, args := range [][]string{
		{"search", "radar", "--page", "0"}, {"search", "radar", "--page", "-1"},
		{"search", "radar", "--limit", "101"}, {"search", "radar", "--page"},
		{"search", "radar", "--unknown"}, {"search", "--raw"}, {"search", ""},
	} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, fstest.MapFS{})
		if err == nil || strings.Contains(err.Error(), "development build") {
			t.Fatal("invalid query reached dataset loading", args, err)
		}
	}
	for _, args := range [][]string{{"search", "--help"}, {"help", "search"}} {
		var out bytes.Buffer
		if err := runWithFiles(context.Background(), args, &out, fstest.MapFS{}); err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(out.String(), "--page") || strings.Contains(out.String(), "pipeline compare") {
			t.Fatal(out.String())
		}
	}
	var out bytes.Buffer
	if err := runWithFiles(context.Background(), []string{"--version"}, &out, fstest.MapFS{}); err != nil {
		t.Fatal(err)
	}
}

func TestSearchRawContentIsOptInAndUsesEverySavedEvidencePage(t *testing.T) {
	files := researchCommandBundle(t)
	d, err := dataset.Open(files["data/dataset.zip"].Data)
	if err != nil {
		t.Fatal(err)
	}
	rows := []dataset.Entity{d.Entities[0]}
	for _, raw := range []bool{false, true} {
		var out bytes.Buffer
		response := map[string]any{"dataset_id": d.Manifest.DatasetID, "match_status": "candidates"}
		err := writeSearchPage(d, response, "text", searchOptions{Page: dataset.Page{Number: 2, Size: 1}, Raw: raw, Mode: "vector"}, dataset.RankedPage[dataset.Entity]{Results: rows, Total: 3, HasMore: true}, nil, json.NewEncoder(&out))
		if err != nil {
			t.Fatal(err)
		}
		var actual struct {
			Page, Limit, Total int
			HasMore            bool `json:"has_more"`
			Results            []map[string]json.RawMessage
		}
		if err := json.Unmarshal(out.Bytes(), &actual); err != nil {
			t.Fatal(err)
		}
		if actual.Page != 2 || actual.Limit != 1 || actual.Total != 3 || !actual.HasMore {
			t.Fatal(out.String())
		}
		content, exists := actual.Results[0]["content"]
		if exists != raw {
			t.Fatal("raw content not opt-in")
		}
		if raw {
			want, err := d.Export(rows[0].ID, "json", "")
			if err != nil {
				t.Fatal(err)
			}
			var a, b any
			_ = json.Unmarshal(content, &a)
			_ = json.Unmarshal(want, &b)
			if !reflect.DeepEqual(a, b) {
				t.Fatal("content changed saved record/evidence")
			}
		}
	}
}
